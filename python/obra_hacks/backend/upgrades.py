#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging
import re
from collections import namedtuple
from datetime import date

from peewee import JOIN, Window, fn, prefetch

from .data import (DISCIPLINE_MAP, NAME_RE, NUMBER_RE, SCHEDULE_2018,
                   SCHEDULE_2019, SCHEDULE_2019_DATE, UPGRADES)
from .models import (Event, ObraPersonSnapshot, PendingUpgrade, Person, Points,
                     Race, Result, db)
from .outputs import get_writer
from .scrapers import scrape_person

logger = logging.getLogger(__name__)
Point = namedtuple('Point', 'value,place,date')


@db.savepoint()
def recalculate_points(upgrade_discipline, incremental=False):
    """
    Create Points for qualifying Results for all Races of this type.
    """
    logger.info('Recalculating points - upgrade_discipline={} incremental={}'.format(upgrade_discipline, incremental))
    points_created = 0

    if not incremental:
        # Delete all Result data for this discipline and recalc from scratch
        (Points.delete()
               .where(Points.result_id << (Result.select(Result.id)
                                                 .join(Race, src=Result)
                                                 .join(Event, src=Race)
                                                 .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])))
               .execute())

    # Get all categorized races that don't have points yet
    query = (Race.select(Race, Event)
                 .join(Event, src=Race)
                 .join(Result, src=Race)
                 .join(Points, src=Result, join_type=JOIN.LEFT_OUTER)
                 .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                 .where(Race.categories.length() > 0)
                 .group_by(Race, Event)
                 .having(fn.COUNT(Points.result_id) == 0))

    for race in query.execute():
        logger.info('Got Race [{}]{}: [{}]{} on {} with {} starters'.format(
            race.event.id, race.event.name, race.id, race.name, race.date, race.starters))

        # Extract categories from field name and check points depth for gender and field size
        points = get_points_schedule(race.event.discipline, race)

        if race.categories and points:
            # If everything looks good, get the top N finishers for this race and assign points
            results = (race.results.select(Result.id,
                                           Result.place,
                                           Person.id,
                                           Person.first_name,
                                           Person.last_name,
                                           (Result.place.cast('integer') - 1).alias('zplace'))
                                   .join(Person, src=Result)
                                   .where(Result.place.cast('integer') > 0)
                                   .where(Result.place.cast('integer') <= len(points))
                                   .order_by(Result.place.cast('integer').asc()))
            for result in results.execute():
                if not (NAME_RE.match(result.person.first_name) and NAME_RE.match(result.person.last_name)):
                    logger.debug('Invalid name: {} {}'.format(result.person.first_name, result.person.last_name))
                    continue
                logger.info('{}, {}: {} points for {} in {} at {}: {}'.format(
                    result.person.last_name,
                    result.person.first_name,
                    points[result.zplace],
                    result.place,
                    '/'.join(str(c) for c in race.categories),
                    race.event.name,
                    race.name))

                (Points.insert(result=result,
                               value=points[result.zplace])
                       .execute())
                points_created += 1
        else:
            logger.info('Invalid category or insufficient starters for this field')

    logger.info('Recalculation created {} points'.format(points_created))
    return points_created


@db.savepoint()
def sum_points(upgrade_discipline):
    """
    Calculate running points totals and detect upgrades
    Attempts to do some guessing at category and upgrades based on race participation
    and acrued points, but there's a potential to get it wrong. It'd be nice if the site
    tracked historical rider categories, but all you get is a point in time snapshot at
    the time the data is retrieved.
    """
    # Note that Race IDs don't necessarily imply the actual order that the races occurred
    # at the event. However, due to the way the site assigns created/updated
    # values, and the fact that the races are usually listed in order of occurrence in the
    # spreadsheet that is uploaded, we generally can imply actual order from the timestamps.
    logger.info('Recalculating point sums and upgrades - upgrade_discipline={}'.format(upgrade_discipline))
    null_result = Result(race=Race(categories=[]), person=Person(), points=[])

    results = (Result.select(Result.id,
                             Result.place,
                             Person,
                             Race.id,
                             Race.name,
                             Race.date,
                             Race.categories,
                             Race.starters,
                             Event.id,
                             Event.name,
                             Event.discipline)
                     .join(Person, src=Result)
                     .join(Race, src=Result)
                     .join(Event, src=Race)
                     .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                     .order_by(Person.id.asc(),
                               Race.date.asc(),
                               Race.created.asc()))

    prev_result = null_result
    is_woman = False
    cat_points = []
    categories = {9}
    upgrade_notes = []
    upgrade_race = Race(date=date(1970, 1, 1))

    for result in prefetch(results, Points):
        # Reset stats when the person changes
        if prev_result.person == result.person:
            if prev_result.race == result.race:
                logger.warn('{0}, {1}: {2}/{3} at [{4}]{5} - Ignoring duplicate results in same race'.format(
                            result.person.last_name,
                            result.person.first_name,
                            result.place,
                            result.race.starters,
                            result.race.id,
                            result.race.name))
                prev_result = result
                continue
        else:
            prev_result = null_result
            is_woman = False
            cat_points[:] = []
            categories = {9}
            upgrade_notes[:] = []
            upgrade_race = Race(date=date(1970, 1, 1))

        def result_points_value():
            return result.points[0].value if result.points else 0

        def needed_upgrade():
            return prev_result.points[0].needs_upgrade if prev_result.points else False

        def points_sum():
            return sum(int(p.value) for p in cat_points)

        def erase_points():
            for point in result.points:
                point.delete_instance(recursive=True)
            result.points[:] = []

        expired_points = expire_points(cat_points, result.race.date)
        if expired_points:
            upgrade_notes.append('{} {} EXPIRED'.format(expired_points, 'POINT HAS' if expired_points == 1 else 'POINTS HAVE'))

        # Only process finishes (no dns) with a known category
        if NUMBER_RE.match(result.place) and result.race.categories:
            upgrade_category = max(categories) - 1

            # Don't have any gender information in results, flag person as woman by race participation
            # I should call this is_not_cis_male or something lol
            if 'women' in result.race.name.lower():
                is_woman = True

            # Here's the goofy category change logic
            if categories == {1} and 1 in result.race.categories:
                # Nowhere to go when you're in cat 1
                erase_points()
            elif upgrade_category in result.race.categories and needed_upgrade():
                # If the race category includes their upgrade category, and they needed an upgrade as of the previous result
                obra_category = get_obra_data(result.person, result.race.date).category_for_discipline(result.race.event.discipline)
                logger.debug('OBRA category check: obra={}, upgrade_category={}'.format(obra_category, upgrade_category))
                if obra_category is None or obra_category <= upgrade_category:
                    # If they're not a member or have been upgraded on the site, give them the upgrade.
                    # The actual upgrade probably happened much later, but we have no idea when so this is the best we can do.
                    upgrade_notes.append('UPGRADED TO {} WITH {} POINTS'.format(upgrade_category, points_sum()))
                    cat_points[:] = []
                    categories = {upgrade_category}
                    upgrade_race = result.race
            elif (not categories.intersection(result.race.categories) and
                  min(categories) > min(result.race.categories)):
                # Race category does not overlap with rider category, and the race cateogory is more skilled
                if categories == {9}:
                    # First result for this rider, assign rider current race category - which may be multiple, such as 1/2 or 3/4
                    if result.race.categories in ([1], [1, 2], [1, 2, 3], [3, 4, 5]):
                        # If we first saw them racing as a pro they've probably been there for a while.
                        # if we first saw them racing as a junior, they might still be there.
                        # Just check the site and assign their category from that.
                        obra_category = get_obra_data(result.person, result.race.date).category_for_discipline(result.race.event.discipline)
                        logger.debug('OBRA category check: obra={}, race={}'.format(obra_category, result.race.categories))
                        if obra_category in result.race.categories:
                            categories = {obra_category}
                        else:
                            categories = {max(result.race.categories)}
                    else:
                        categories = set(result.race.categories)
                    if categories == {1}:
                        erase_points()
                    # Add a dummy point and note to ensure Points creation
                    upgrade_notes.append('')
                else:
                    # Complain if they don't have enough points or races for the upgrade
                    if can_upgrade(upgrade_discipline, points_sum(), max(result.race.categories), cat_points, True):
                        upgrade_note = ''
                    else:
                        upgrade_note = 'PREMATURELY '
                    upgrade_note += 'UPGRADED TO {} WITH {} POINTS'.format(max(result.race.categories), points_sum())
                    cat_points[:] = []
                    upgrade_notes.append(upgrade_note)
                    categories = {max(result.race.categories)}
                    upgrade_race = result.race
            elif (not categories.intersection(result.race.categories) and
                  max(categories) < max(result.race.categories)):
                # points expire after a year, unless the race occurred in 2021, in which case go two years back
                max_points_age = 365
                if result.race.date.year == 2021:
                    max_points_age = 365 * 2  # f*ck 2020
                # Race category does not overlap with rider category, and the race category is less skilled
                if is_woman and 'women' not in result.race.name.lower():
                    # Women can race down-category in a men's race
                    pass
                elif not points_sum() and (result.race.date - upgrade_race.date).days > max_points_age:
                    # All their points expired and it's been a year since they changed categories, probably nobody cares, give them a downgrade
                    cat_points[:] = []
                    upgrade_notes.append('DOWNGRADED TO {}'.format(min(result.race.categories)))
                    categories = {min(result.race.categories)}
                    upgrade_race = result.race
                elif result.points:
                    upgrade_notes.append('NO POINTS FOR RACING BELOW CATEGORY')
                    result.points[0].value = 0
            elif (len(categories.intersection(result.race.categories)) < len(categories) and
                  len(categories) > 1):
                # Refine category for rider who'd only been seen in multi-category races
                categories.intersection_update(result.race.categories)
                upgrade_notes.append('')
        elif result.points:
            logger.warn('Have points for a race with place={} and categories={}'.format(result.place, result.race.categories))

        cat_points.append(Point(result_points_value(), result.place, result.race.date))

        if (upgrade_race == result.race or upgrade_notes or points_sum()) and not result.points:
            # Ensure we have a Points record to add notes to if they upgraded, have notes, or have running points
            result.points = [Points.create(result=result, value=0)]

        if result.points:
            if (needs_upgrade(result.person, upgrade_discipline, points_sum(), upgrade_category, cat_points) or
                (needed_upgrade() and can_upgrade(upgrade_discipline, points_sum(), upgrade_category, cat_points) and upgrade_race != result.race)):
                # If they needed an upgrade last time, and still can upgrade, but didn't upgrade yet...
                # Or if they need an upgrade now...
                upgrade_notes.append('NEEDS UPGRADE')
                result.points[0].needs_upgrade = True

            result.points[0].sum_categories = list(categories)
            result.points[0].sum_value = points_sum()

            if upgrade_race == result.race:
                confirm_category_change(result, upgrade_notes)

            if upgrade_notes:
                result.points[0].notes = '; '.join(reversed(sorted(n.capitalize() for n in upgrade_notes if n)))
                upgrade_notes[:] = []

            result.points[0].save()

        prev_result = result

        logger.info('{0}, {1}: {2} points for {3}/{4} at [{5}]{6}: {7} on {8} ({9} in {10} {11}) | {12}'.format(
            result.person.last_name,
            result.person.first_name,
            result_points_value(),
            result.place,
            result.race.starters,
            result.race.id,
            result.race.event.name,
            result.race.name,
            result.race.date,
            '/'.join(str(c) for c in categories),
            '/'.join(str(c) for c in result.race.categories) or '-',
            result.race.event.discipline,
            result.points[0].notes if result.points else ''))


@db.savepoint()
def confirm_pending_upgrades(upgrade_discipline):
    """
    Since upgrades are recognized the next race after they're earned,
    we don't have a good way of suppressing them if someone is upgraded
    on the OBRA website but don't race again.
    Work around that by creating a PendingUpgrade record that will mark it until they race again.
    """
    logger.info('Checking for confirmed upgrades - upgrade_discipline={}'.format(upgrade_discipline))
    (PendingUpgrade.delete()
                   .where(PendingUpgrade.discipline == upgrade_discipline)
                   .execute())

    last_result = (Result.select()
                         .join(Race, src=Result)
                         .join(Event, src=Race)
                         .join(Person, src=Result)
                         .where(Race.categories.length() > 0)
                         .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                         .select(fn.DISTINCT(fn.FIRST_VALUE(Result.id)
                                               .over(partition_by=[Result.person_id],
                                                     order_by=[Race.date.desc(), Race.created.desc()],
                                                     start=Window.preceding()
                                                     )
                                             ).alias('first_id')))

    query = (Result.select(Result,
                           Race,
                           Event,
                           Person,
                           Points)
                   .join(Race, src=Result)
                   .join(Event, src=Race)
                   .join(Person, src=Result)
                   .join(Points, src=Result)
                   .where(Result.id << last_result)
                   .where(Points.needs_upgrade == True)
                   .where(~(Race.name.contains('Junior')))
                   .order_by(Points.sum_categories.asc(),
                             Points.sum_value.desc()))

    for result in query.prefetch(Points):
        result.points[0].sum_categories = [min(result.points[0].sum_categories) - 1]
        confirm_category_change(result, ['UPGRADED'])
        if result.points[0].upgrade_confirmation_id:
            logger.debug('Confirmed pending upgrade for {}, {} to {}'.format(
                result.person.last_name,
                result.person.first_name,
                result.points[0].sum_categories[0]))
            (PendingUpgrade.insert(result_id=result.id,
                                   upgrade_confirmation_id=result.points[0].upgrade_confirmation_id,
                                   discipline=upgrade_discipline)
                           .on_conflict(conflict_target=[PendingUpgrade.result],
                                        preserve=[PendingUpgrade.upgrade_confirmation, PendingUpgrade.discipline])
                           .execute())


def print_points(upgrade_discipline, output_format):
    """
    Print out points tally for each Person
    """
    if output_format == 'null':
        return

    cur_year = date.today().year
    start_date = date(cur_year - 1, 1, 1)

    upgrades_needed = (Points.select(Points,
                                     Result.place,
                                     Event.discipline,
                                     Person.id,
                                     Person.first_name,
                                     Person.last_name,
                                     fn.MAX(Race.date).alias('last_date'))
                             .join(Result, src=Points)
                             .join(Person, src=Result)
                             .join(Race, src=Result)
                             .join(Event, src=Race)
                             .where(Race.date >= start_date)
                             .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                             .group_by(Person.id)
                             .having(Points.needs_upgrade == True)
                             .order_by(Points.sum_categories.asc(),
                                       Points.sum_value.desc(),
                                       Person.last_name.collate('NOCASE').asc(),
                                       Person.first_name.collate('NOCASE').asc()))

    points = (Points.select(Points,
                            Result,
                            Person,
                            Race.id,
                            Race.name,
                            Race.date,
                            Race.starters,
                            Race.categories,
                            Event.id,
                            Event.name,
                            Event.discipline)
                    .join(Result, src=Points)
                    .join(Person, src=Result)
                    .join(Race, src=Result)
                    .join(Event, src=Race)
                    .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                    .where(fn.LENGTH(Person.last_name) > 1)
                    .order_by(Person.last_name.collate('NOCASE').asc(),
                              Person.first_name.collate('NOCASE').asc(),
                              Race.date.asc()))

    person = None
    with get_writer(output_format, upgrade_discipline) as writer:
        writer.start_upgrades()
        for point in upgrades_needed.execute():
            # Confirm that they haven't already been upgraded on the site
            discipline = point.result.race.event.discipline
            obra_category = get_obra_data(point.result.person, point.result.race.date).category_for_discipline(discipline)
            if obra_category is not None and obra_category >= min(point.sum_categories):
                writer.upgrade(point)
        writer.end_upgrades()

        for point in points.execute():
            if person != point.result.person:
                if person:
                    writer.end_person(person)
                person = point.result.person
                writer.start_person(person)
            writer.point(point)
        else:
            writer.end_person(person, True)


def get_points_schedule(event_discipline, race):
    """
    Get the points shedule for the race's gender, starter count, and discipline
    See: http://www.obra.org/upgrade_rules.html
    """
    field = 'women' if re.search('women|junior', race.name, re.I) else 'open'
    if race.date >= SCHEDULE_2019_DATE:
        schedule = SCHEDULE_2019
    else:
        schedule = SCHEDULE_2018

    if event_discipline in schedule:
        if field in schedule[event_discipline]:
            field_size_list = schedule[event_discipline][field]
        else:
            field_size_list = schedule[event_discipline]['open']

        for field_size in field_size_list:
            if race.starters >= field_size['min'] and race.starters <= field_size['max']:
                return field_size['points']
    else:
        logger.warn('No points schedule for event_discipline={} field={} starters={} date={}'.format(event_discipline, field, race.starters, race.date))

    return []


def needs_upgrade(person, upgrade_discipline, points_sum, category, cat_points):
    """
    Determine if the rider needs an upgrade for this discipline
    """
    if upgrade_discipline in UPGRADES and category in UPGRADES[upgrade_discipline]:
        logger.debug('Checking upgrade schedule for upgrade_discipline={} category={}'.format(upgrade_discipline, category))
        if 'podiums' in UPGRADES[upgrade_discipline][category]:
            # FIXME - also need to check field size and gender
            podiums = UPGRADES[upgrade_discipline][category]['podiums']
            podium_races = [p for p in cat_points if safe_int(p.place) <= 3]
            if len(podium_races) >= podiums:
                logger.debug('Returning True (podium_races)')
                return True
            else:
                logger.debug('Returning False (podium_races)')
                return False
        else:
            max_points = UPGRADES[upgrade_discipline][category]['max']
            logger.debug('Returning {} (max_points)'.format(points_sum >= max_points))
            return points_sum >= max_points
    else:
        logger.debug('No upgrade schedule for upgrade_discipline={} category={}'.format(upgrade_discipline, category))

    return False


def can_upgrade(upgrade_discipline, points_sum, category, cat_points, check_min_races=False):
    """
    Determine if the rider can upgrade to a given category, based on their current points and race count
    """
    if upgrade_discipline in UPGRADES and category in UPGRADES[upgrade_discipline]:
        if 'podiums' in UPGRADES[upgrade_discipline][category]:
            logger.debug('Returning {} (category)'.format(category > 0))
            return category > 0
        else:
            min_points = UPGRADES[upgrade_discipline][category].get('min')
            min_races = UPGRADES[upgrade_discipline][category].get('races')
            logger.debug('Checking upgrade_discipline={} points_sum={} category={} num_races={} min_points={} min_races={}'.format(
                upgrade_discipline, points_sum, category, len(cat_points), min_points, min_races))
            if check_min_races and min_races and len(cat_points) >= min_races:
                logger.debug('Returning True (min_races)')
                return True
            elif points_sum >= min_points:
                logger.debug('Returning True (min_points)')
                return True
            else:
                logger.debug('Returning False')
                return False
    else:
        logger.warn('No upgrade schedule for upgrade_discipline={} category={}'.format(upgrade_discipline, category))

    return True


def get_obra_data(person, date):
    """
    Try to get a snapshot of OBRA data from on or before the given date.
    If we have data from on or before the requested date, use that.
    If we have data from some other newer date, use that.
    If we don't have any data at all, get some.
    """
    # FIXME - seems like we're using stale data here? When do we want to fetch new data?
    if person.obra.where(ObraPersonSnapshot.date <= date).count():
        query = person.obra.order_by(ObraPersonSnapshot.date.desc()).where(ObraPersonSnapshot.date <= date)
    elif person.obra.count():
        query = person.obra.order_by(ObraPersonSnapshot.date.asc())
    else:
        scrape_person(person)
        query = person.obra

    data = query.first()
    logger.debug('OBRA Data: data requested={} returned={} for person={}'.format(date, data.date, person.id))
    return data


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return 999


def expire_points(points, race_date):
    """
    Calculate the sum of all points earned more than one year ago.
    Modify the passed list by removing these expired points, and return the previously calculated sum.
    """
    # points expire after a year, unless the race occurred in 2021, in which case go two years back
    max_points_age = 365
    if race_date.year == 2021:
        max_points_age = 365 * 2  # f*ck 2020
    expired_points = sum(int(p.value) for p in points if (race_date - p.date).days > max_points_age)
    points[:] = [p for p in points if (race_date - p.date).days <= max_points_age]
    return expired_points


def confirm_category_change(result, notes):
    """Check the site to see if an upgrade or downgrade has been recognized there"""
    obra_data = get_obra_data(result.person, result.race.date)
    obra_category = obra_data.category_for_discipline(result.race.event.discipline)
    result_category = min(result.points[0].sum_categories)

    if obra_category is None:
        return

    for i, note in enumerate(notes):
        if 'UPGRADED' in note:
            logger.debug('Confirming {}'.format(note))
            if obra_category <= result_category:
                result.points[0].upgrade_confirmation_id = obra_data.id
                notes[i] += ' (CONFIRMED {})'.format(obra_data.date)
            break

        if 'DOWNGRADED' in note:
            logger.debug('Confirming {}'.format(note))
            if obra_category >= result_category:
                result.points[0].upgrade_confirmation_id = obra_data.id
                notes[i] += ' (CONFIRMED {})'.format(obra_data.date)
            break

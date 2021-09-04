import logging
from datetime import date
from email.utils import formatdate
from time import time

from obra_hacks.backend.data import DISCIPLINE_MAP
from obra_hacks.backend.models import (Event, ObraPersonSnapshot,
                                            PendingUpgrade, Person, Points,
                                            Quality, Race, Rank, Result,
                                            Series)
from peewee import JOIN, Entity, Select, Window, fn

from flask_restx import Resource, fields, marshal

logger = logging.getLogger(__name__)
cache_timeout = 900


def register(api, cache):
    ns = api.namespace('upgrades', 'Upgrade Status and Events')

    def get_pending(r):
        if isinstance(r.pending, list) and r.pending:
            return r.pending[0].upgrade_confirmation.date

    def make_discipline(event):
        return {'name': event.discipline,
                'display': event.discipline.split('_')[0].title(),
                }

    result = ns.model('Result',
                      {'id': fields.Integer,
                       'place': fields.String,
                       'time': fields.Integer,
                       'laps': fields.Integer,
                       'value': fields.Integer(attribute=lambda r: r.points[0].value),
                       'sum_value': fields.Integer(attribute=lambda r: r.points[0].sum_value),
                       'sum_categories': fields.List(fields.Integer, attribute=lambda r: r.points[0].sum_categories),
                       'notes': fields.String(attribute=lambda r: r.points[0].notes),
                       'needs_upgrade': fields.Boolean(attribute=lambda r: r.points[0].needs_upgrade),
                       'rank': fields.Integer(attribute=lambda r: r.rank[0].value if r.rank else None),
                       'pending_date': fields.Date(attribute=get_pending),
                       })

    person = ns.model('Person',
                      {'id': fields.Integer,
                       'first_name': fields.String,
                       'last_name': fields.String,
                       'team_name': fields.String,
                       'name': fields.String(attribute=lambda p: '{} {}'.format(p.first_name, p.last_name).title() if p else None),
                       })

    series = ns.model('Series',
                      {'id': fields.Integer,
                       'name': fields.String,
                       })

    event = ns.model('Event',
                     {'id': fields.Integer,
                      'name': fields.String,
                      'year': fields.Integer,
                      'series': fields.Nested(series, allow_null=True),
                      })

    race = ns.model('Race',
                    {'id': fields.Integer,
                     'name': fields.String,
                     'date': fields.Date,
                     'starters': fields.Integer,
                     'categories': fields.List(fields.Integer),
                     'quality': fields.Integer(attribute=lambda r: r.quality[0].value if r.quality else None)
                     })

    discipline = ns.model('Discipline',
                          {'name': fields.String,
                           'display': fields.String,
                           })

    event_with_discipline = ns.clone('EventWithDiscipline', event,
                                     {'discipline': fields.Nested(discipline, attribute=make_discipline),
                                      })

    # Race with event info
    race_with_event = ns.clone('RaceWithEvent', race,
                               {'event': fields.Nested(event_with_discipline),
                                })

    # Results for upgrades - contains person info and race data
    result_with_person_and_race = ns.clone('ResultWithPersonAndRace', result,
                                           {'race': fields.Nested(race),
                                            'person': fields.Nested(person),
                                            })

    result_with_person_and_race_with_event = ns.clone('ResultWithPersonAndRaceWithEvent', result,
                                                      {'race': fields.Nested(race_with_event),
                                                       'person': fields.Nested(person),
                                                       })

    # Container for grouping results by discipline
    discipline_results = ns.model('DisciplineResultsWithRaceAndPerson',
                                  {'name': fields.String,
                                   'display': fields.String,
                                   'results': fields.List(fields.Nested(result_with_person_and_race)),
                                   })

    @ns.route('/pending/')
    @ns.response(200, 'Success', [discipline_results])
    @ns.response(500, 'Server Error')
    class UpgradesPending(Resource):
        """
        Get the most recent result for each person, if that person needed an upgrade as of that event,
        grouped by discipline, sorted by category and points
        """
        @cache.cached(timeout=cache_timeout)
        def get(self):
            # limit results to people who raced since jan 1 of the previous year
            start_year = date.today().year - 1
            if start_year == 2020:
                start_year = 2019  # f*ck 2020
            start_date = date(start_year, 1, 1)
            disciplines = []

            for upgrade_discipline in DISCIPLINE_MAP.keys():
                # Subquery to find the most recent result for each person
                latest_cte = (Result.select()
                                    .join(Race, src=Result)
                                    .join(Event, src=Race)
                                    .join(Person, src=Result)
                                    .where(Race.date >= start_date)
                                    .where(Race.categories.length() > 0)
                                    .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                                    .select(fn.DISTINCT(fn.FIRST_VALUE(Result.id)
                                                          .over(partition_by=[Result.person_id],
                                                                order_by=[Race.date.desc(), Race.created.desc()],
                                                                start=Window.preceding()
                                                                )
                                                        ).alias('result_id'))
                                    .cte('latest_results'))

                latest_results = Select(
                    from_list=[fn.JSON_EACH(latest_cte.select_from(fn.JSON_GROUP_ARRAY(latest_cte.c.result_id).python_value(str)).scalar())],
                    columns=[Entity('value')])

                query = (Result.select(Result,
                                       Race,
                                       Event,
                                       Person,
                                       Points,
                                       PendingUpgrade,
                                       ObraPersonSnapshot,
                                       Rank,
                                       Quality)
                               .join(Race, src=Result)
                               .join(Event, src=Race)
                               .join(Person, src=Result)
                               .join(Points, src=Result)
                               .join(PendingUpgrade, src=Result, join_type=JOIN.LEFT_OUTER)
                               .join(ObraPersonSnapshot, src=PendingUpgrade, join_type=JOIN.LEFT_OUTER)
                               .join(Rank, src=Result, join_type=JOIN.LEFT_OUTER)
                               .join(Quality, src=Race, join_type=JOIN.LEFT_OUTER)
                               .where(Result.id << latest_results)
                               .where(Points.needs_upgrade == True)
                               .order_by(Points.sum_categories.asc(),
                                         Points.sum_value.desc()))

                disciplines.append({'name': upgrade_discipline,
                                    'display': upgrade_discipline.split('_')[0].title(),
                                    'results': query.prefetch(Race, Event, Person,
                                                              Points, PendingUpgrade, ObraPersonSnapshot, Rank, Quality),
                                    })

            return ([marshal(d, discipline_results) for d in disciplines],
                    200,
                    {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

    @ns.route('/pending/top/')
    @ns.response(200, 'Success', [result_with_person_and_race_with_event])
    @ns.response(500, 'Server Error')
    class UpgradesPendingTop(Resource):
        """
        Get the most recent 6 pending upgrade results, sorted by category and points
        """
        @cache.cached(timeout=cache_timeout)
        def get(self):
            # limit results to people who raced since jan 1 of the previous year
            start_year = date.today().year - 1
            if start_year == 2020:
                start_year = 2019  # f*ck 2020
            start_date = date(start_year, 1, 1)

            # Subquery to find the most recent result for each person
            latest_cte = (Result.select()
                                .join(Race, src=Result)
                                .join(Event, src=Race)
                                .join(Person, src=Result)
                                .where(Race.date >= start_date)
                                .where(Race.categories.length() > 0)
                                .select(fn.DISTINCT(fn.FIRST_VALUE(Result.id)
                                                      .over(partition_by=[Result.person_id],
                                                            order_by=[Race.date.desc(), Race.created.desc()],
                                                            start=Window.preceding()
                                                            )
                                                    ).alias('result_id'))
                                .cte('latest_results'))

            latest_results = Select(
                from_list=[fn.JSON_EACH(latest_cte.select_from(fn.JSON_GROUP_ARRAY(latest_cte.c.result_id).python_value(str)).scalar())],
                columns=[Entity('value')])

            query = (Result.select(Result,
                                   Race,
                                   Event,
                                   Series,
                                   Person,
                                   Points,
                                   Rank,
                                   Quality)
                           .join(Race, src=Result)
                           .join(Event, src=Race)
                           .join(Series, src=Event, join_type=JOIN.LEFT_OUTER)
                           .join(Person, src=Result)
                           .join(Points, src=Result)
                           .join(Rank, src=Result, join_type=JOIN.LEFT_OUTER)
                           .join(Quality, src=Race, join_type=JOIN.LEFT_OUTER)
                           .where(Result.id << latest_results)
                           .where(Points.needs_upgrade == True)
                           .order_by(Points.sum_categories.asc(),
                                     Points.sum_value.desc())
                           .limit(6))

            return ([marshal(r, result_with_person_and_race_with_event) for r in query.prefetch(Race, Event, Series, Person, Points, Rank, Quality)],
                    200,
                    {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

    @ns.route('/recent/')
    @ns.response(200, 'Success', [discipline_results])
    @ns.response(500, 'Server Error')
    class UpgradesRecent(Resource):
        """
        Get all results where the person upgraded or downgraded, grouped by discipline, sorted by date, category, name
        """
        @cache.cached(timeout=cache_timeout)
        def get(self):
            # limit results to people who raced since jan 1 of the previous year
            start_year = date.today().year - 1
            if start_year == 2020:
                start_year = 2019  # f*ck 2020
            start_date = date(start_year, 1, 1)
            disciplines = []

            for upgrade_discipline in DISCIPLINE_MAP.keys():
                query = (Result.select(Result,
                                       Race,
                                       Event,
                                       Series,
                                       Person,
                                       Points,
                                       PendingUpgrade,
                                       ObraPersonSnapshot,
                                       Rank,
                                       Quality)
                               .join(Race, src=Result)
                               .join(Event, src=Race)
                               .join(Series, src=Event, join_type=JOIN.LEFT_OUTER)
                               .join(Person, src=Result)
                               .join(Points, src=Result)
                               .join(PendingUpgrade, src=Result, join_type=JOIN.LEFT_OUTER)
                               .join(ObraPersonSnapshot, src=PendingUpgrade, join_type=JOIN.LEFT_OUTER)
                               .join(Rank, src=Result, join_type=JOIN.LEFT_OUTER)
                               .join(Quality, src=Race, join_type=JOIN.LEFT_OUTER)
                               .where(Race.date >= start_date)
                               .where(~(Race.name.contains('junior')))
                               .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                               .where(Points.notes.contains('upgraded') | Points.notes.contains('downgraded'))
                               .order_by(Race.date.desc(),
                                         Points.sum_categories.asc(),
                                         Person.last_name.asc(),
                                         Person.first_name.asc()))

                disciplines.append({'name': upgrade_discipline,
                                    'display': upgrade_discipline.split('_')[0].title(),
                                    'results': query.prefetch(Race, Event, Series, Person,
                                                              Points, PendingUpgrade, ObraPersonSnapshot, Rank, Quality),
                                    })

            return ([marshal(d, discipline_results) for d in disciplines],
                    200,
                    {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

    @ns.route('/recent/top/')
    @ns.response(200, 'Success', [result_with_person_and_race_with_event])
    @ns.response(500, 'Server Error')
    class UpgradesRecentTop(Resource):
        """
        Get the most recent 6 results where the person upgraded or downgraded, sorted by date, category, name
        """
        @cache.cached(timeout=cache_timeout)
        def get(self):
            # limit results to people who raced since jan 1 of the previous year
            start_year = date.today().year - 1
            if start_year == 2020:
                start_year = 2019  # f*ck 2020
            start_date = date(start_year, 1, 1)

            query = (Result.select(Result,
                                   Race,
                                   Event,
                                   Series,
                                   Person,
                                   Points,
                                   Rank,
                                   Quality)
                           .join(Race, src=Result)
                           .join(Event, src=Race)
                           .join(Series, src=Event, join_type=JOIN.LEFT_OUTER)
                           .join(Person, src=Result)
                           .join(Points, src=Result)
                           .join(Rank, src=Result, join_type=JOIN.LEFT_OUTER)
                           .join(Quality, src=Race, join_type=JOIN.LEFT_OUTER)
                           .where(Race.date >= start_date)
                           .where(~(Race.name.contains('Junior')))
                           .where(Points.notes.contains('upgraded') | Points.notes.contains('downgraded'))
                           .order_by(Race.date.desc(),
                                     Points.sum_categories.asc(),
                                     Person.last_name.asc(),
                                     Person.first_name.asc())
                           .limit(6))

            return ([marshal(r, result_with_person_and_race_with_event) for r in query.prefetch(Race, Event, Series, Person, Points, Rank, Quality)],
                    200,
                    {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

    @ns.route('/all/')
    @ns.response(200, 'Success', [discipline_results])
    @ns.response(500, 'Server Error')
    class UpgradesAll(Resource):
        """
        Get the most recent result for each person, if that person had upgrade points as of that event,
        grouped by discipline, sorted by points and category
        """
        @cache.cached(timeout=cache_timeout)
        def get(self):
            # limit results to people who raced since jan 1 of the previous year
            start_year = date.today().year - 1
            if start_year == 2020:
                start_year = 2019  # f*ck 2020
            start_date = date(start_year, 1, 1)
            disciplines = []

            for upgrade_discipline in DISCIPLINE_MAP.keys():
                # Subquery to find the most recent result for each person
                latest_cte = (Result.select()
                                    .join(Race, src=Result)
                                    .join(Event, src=Race)
                                    .join(Person, src=Result)
                                    .where(Race.date >= start_date)
                                    .where(Race.categories.length() > 0)
                                    .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                                    .select(fn.DISTINCT(fn.FIRST_VALUE(Result.id)
                                                          .over(partition_by=[Result.person_id],
                                                                order_by=[Race.date.desc(), Race.created.desc()],
                                                                start=Window.preceding()
                                                                )
                                                        ).alias('result_id'))
                                    .cte('latest_results'))

                latest_results = Select(
                    from_list=[fn.JSON_EACH(latest_cte.select_from(fn.JSON_GROUP_ARRAY(latest_cte.c.result_id).python_value(str)).scalar())],
                    columns=[Entity('value')])

                query = (Result.select(Result,
                                       Race,
                                       Event,
                                       Person,
                                       Points,
                                       PendingUpgrade,
                                       ObraPersonSnapshot,
                                       Rank,
                                       Quality)
                               .join(Race, src=Result)
                               .join(Event, src=Race)
                               .join(Person, src=Result)
                               .join(Points, src=Result)
                               .join(PendingUpgrade, src=Result, join_type=JOIN.LEFT_OUTER)
                               .join(ObraPersonSnapshot, src=PendingUpgrade, join_type=JOIN.LEFT_OUTER)
                               .join(Rank, src=Result, join_type=JOIN.LEFT_OUTER)
                               .join(Quality, src=Race, join_type=JOIN.LEFT_OUTER)
                               .where(Result.id << latest_results)
                               .where(Points.value > 0)
                               .order_by(Points.sum_value.desc(),
                                         Points.sum_categories.asc()))

                disciplines.append({'name': upgrade_discipline,
                                    'display': upgrade_discipline.split('_')[0].title(),
                                    'results': query.prefetch(Race, Event, Person,
                                                              Points, PendingUpgrade, ObraPersonSnapshot, Rank, Quality),
                                    })

            return ([marshal(d, discipline_results) for d in disciplines],
                    200,
                    {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

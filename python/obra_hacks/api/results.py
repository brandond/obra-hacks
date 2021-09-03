import logging
from copy import deepcopy
from email.utils import formatdate
from time import time

from obra_hacks.backend.data import DISCIPLINE_MAP
from obra_hacks.backend.models import (Event, ObraPersonSnapshot,
                                            PendingUpgrade, Person, Points,
                                            Quality, Race, Rank, Result,
                                            Series)
from obra_hacks.backend.rankings import get_ranks
from peewee import JOIN

from flask_restx import Resource, fields, marshal

logger = logging.getLogger(__name__)
cache_timeout = 900


def register(api, cache):
    ns = api.namespace('results', 'Race Results')

    def get_pending(r):
        if isinstance(r.pending, list) and r.pending:
            return r.pending[0].upgrade_confirmation.date

    def fill_results(pdr):
        if not pdr['results']:
            return []

        # Create initial filler point. The oldest result SHOULD have placeholder points,
        # but if for some reason it does not we create our own based on the race info.
        if pdr['results'][-1].points:
            last_points = deepcopy(pdr['results'][-1].points[0])
            last_points.result = None
            last_points.notes = ''
            last_points.value = 0
        else:
            last_points = Points(sum_categories=pdr['results'][-1].race.categories)

        # Backfill points from oldest to newest
        for result in reversed(pdr['results']):
            if result.points:
                last_points = deepcopy(result.points[0])
                last_points.result = None
                last_points.notes = ''
                last_points.value = 0
            else:
                result.points.append(last_points)

        return pdr['results']

    # Base stuff and relationships
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

    result = ns.model('Result',
                      {'id': fields.Integer,
                       'place': fields.String,
                       'time': fields.Integer,
                       'laps': fields.Integer,
                       'value': fields.Integer(attribute=lambda r: r.points[0].value if r.points else None),
                       'sum_value': fields.Integer(attribute=lambda r: r.points[0].sum_value if r.points else None),
                       'sum_categories': fields.List(fields.Integer, attribute=lambda r: r.points[0].sum_categories if r.points else None),
                       'notes': fields.String(attribute=lambda r: r.points[0].notes if r.points else None),
                       'needs_upgrade': fields.Boolean(attribute=lambda r: r.points[0].needs_upgrade if r.points else None),
                       'rank': fields.Integer(attribute=lambda r: r.rank[0].value if r.rank else None),
                       'pending_date': fields.Date(attribute=get_pending),
                       })

    # Race with event info
    race_with_event = ns.clone('RaceWithEvent', race,
                               {'event': fields.Nested(event),
                                })

    # Results for a race - contains person info without race data
    result_with_person = ns.clone('ResultWithPerson', result,
                                  {'person': fields.Nested(person),
                                   })

    # Results for a person - contains event info without person data
    result_with_race = ns.clone('ResultWithRace', result,
                                {'race': fields.Nested(race_with_event),
                                 })

    # Container for grouping of results by race for an event
    event_race_results = ns.clone('EventRaceResults', race,
                                  {'results': fields.List(fields.Nested(result_with_person)),
                                   })

    # Container for grouping results by discipline
    discipline_results = ns.model('DisciplineResultsWithRace',
                                         {'name': fields.String,
                                          'display': fields.String,
                                          'rank': fields.Integer,
                                          'results': fields.List(fields.Nested(result_with_race), attribute=fill_results),
                                          })

    # Contains all results for a person, grouped by discipline
    person_results = ns.clone('PersonResults', person,
                              {'disciplines': fields.List(fields.Nested(discipline_results)),
                               })

    # Contains all results for an event, grouped by race
    event_results = ns.clone('EventResults', event,
                             {'races': fields.List(fields.Nested(event_race_results)),
                              })

    @ns.route('/person/<int:id>')
    @ns.response(200, 'Success', person_results)
    @ns.response(404, 'Not Found')
    @ns.response(500, 'Server Error')
    class ResultsForPerson(Resource):
        """
        Return results for a person.
        """
        @cache.cached(timeout=cache_timeout)
        def get(self, id):
            try:
                db_person = Person.get_by_id(id)
                db_person.disciplines = []
                for upgrade_discipline in DISCIPLINE_MAP.keys():
                    ranks = get_ranks(upgrade_discipline, None, [id])
                    query = (Result.select(Result, Points, PendingUpgrade, ObraPersonSnapshot, Race, Event, Series, Rank, Quality)
                                   .join(Points, src=Result, join_type=JOIN.LEFT_OUTER)
                                   .join(PendingUpgrade, src=Result, join_type=JOIN.LEFT_OUTER)
                                   .join(ObraPersonSnapshot, src=PendingUpgrade, join_type=JOIN.LEFT_OUTER)
                                   .join(Race, src=Result)
                                   .join(Event, src=Race)
                                   .join(Series, src=Event, join_type=JOIN.LEFT_OUTER)
                                   .join(Rank, src=Result, join_type=JOIN.LEFT_OUTER)
                                   .join(Quality, src=Race, join_type=JOIN.LEFT_OUTER)
                                   .where(Result.person == db_person)
                                   .where(Event.discipline << DISCIPLINE_MAP[upgrade_discipline])
                                   .order_by(Race.date.desc(), Race.created.desc()))

                    db_person.disciplines.append({'name': upgrade_discipline,
                                                  'display': upgrade_discipline.split('_')[0].title(),
                                                  'rank': ranks[id],
                                                  'results': query.prefetch(Points, PendingUpgrade, ObraPersonSnapshot, Race, Event, Series, Rank, Quality),
                                                  })
                return (marshal(db_person, person_results), 200, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})
            except Person.DoesNotExist:
                return ({}, 404, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

    @ns.route('/event/<int:id>')
    @ns.response(200, 'Success', event_results)
    @ns.response(404, 'Not Found')
    @ns.response(500, 'Server Error')
    class ResultsForEvent(Resource):
        """
        Return results for an event.
        """
        @cache.cached(timeout=cache_timeout)
        def get(self, id):
            try:
                event = Event.get_by_id(id)
                event.races = (event.races
                                    .order_by(Race.name.asc())
                                    .prefetch(Result, Points, Person, Rank, Quality))
                if event.races:
                    return (marshal(event, event_results), 200, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})
                else:
                    raise Event.DoesNotExist()
            except Event.DoesNotExist:
                return ({}, 404, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

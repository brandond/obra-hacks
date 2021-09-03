#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
from email.utils import formatdate
from time import time

from obra_hacks.backend.data import DISCIPLINE_MAP
from obra_hacks.backend.models import Person
from obra_hacks.backend.rankings import get_ranks
from peewee import Entity, Select, fn

from flask_restx import Resource, fields, marshal

try:
    import ujson as json
except ImportError:
    import json

logger = logging.getLogger(__name__)
cache_timeout = 900


def register(api, cache):
    ns = api.namespace('ranks', 'USAC-style Rankings')

    person = ns.model('Person',
                      {'id': fields.Integer,
                       'first_name': fields.String,
                       'last_name': fields.String,
                       'team_name': fields.String,
                       'name': fields.String(attribute=lambda p: '{} {}'.format(p.first_name, p.last_name).title() if p else None),
                       })

    person_rank = ns.clone('PersonRank', person,
                          {'rank': fields.Integer,
                           'place': fields.Integer,
                           })

    discipline_ranks = ns.model('DisciplineRanks',
                                {'name': fields.String,
                                 'display': fields.String,
                                 'ranks': fields.List(fields.Nested(person_rank)),
                                 })

    @ns.route('/')
    @ns.response(200, 'Success', [discipline_ranks])
    @ns.response(500, 'Server Error')
    class DisciplineRanks(Resource):
        """
        Get the top 500 ranks, grouped by discipline
        """
        @cache.cached(timeout=cache_timeout)
        def get(self):
            disciplines = []

            for upgrade_discipline in DISCIPLINE_MAP.keys():
                ranks = get_ranks(upgrade_discipline)
                ranked_people_ids = sorted([k for k in ranks.keys() if k], key=lambda k: ranks[k])[:501]
                ranked_people_json = Select(from_list=[fn.JSON_EACH(json.dumps(ranked_people_ids))], columns=[Entity('value')])
                query = (Person.select().where(Person.id << ranked_people_json))

                for person in query:
                    person.rank = int(ranks[person.id])
                    ranks[person.id] = person

                place = 0
                prev_rank = 0
                for person_id in ranked_people_ids:
                    person = ranks[person_id]
                    place += person.rank != prev_rank
                    person.place = place
                    prev_rank = person.rank

                disciplines.append({'name': upgrade_discipline,
                                    'display': upgrade_discipline.split('_')[0].title(),
                                    'ranks': [ranks[person_id] for person_id in ranked_people_ids],
                                    })

            return ([marshal(d, discipline_ranks) for d in disciplines], 200, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

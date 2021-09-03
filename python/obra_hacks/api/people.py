import logging
from email.utils import formatdate
from time import time

from obra_hacks.backend.models import Person

from flask import request
from flask_restx import Resource, fields, marshal

logger = logging.getLogger(__name__)
cache_timeout = 900


def register(api, cache):
    ns = api.namespace('people', 'People Search and Metadata')

    result = ns.model('SearchResult',
                      {'id': fields.Integer,
                       'name': fields.String,
                       'first_name': fields.String,
                       'last_name': fields.String,
                       'team_name': fields.String,
                       })

    @ns.route('/')
    @ns.response(200, 'Success', [result])
    @ns.response(400, 'Bad Request')
    @ns.response(500, 'Server Error')
    class PeopleSearch(Resource):
        """
        Get a list of people whose name matches a search string
        """
        @ns.param(name='name', description='Name Search String', type='string', minLength=3, required=True)
        @cache.cached(timeout=cache_timeout, query_string=True)
        def get(self):
            name = request.args.get('name', '')
            if len(name) < 3:
                return ({'message': 'Search string too short'}, 400)

            query = (Person.select(Person,
                                   Person.first_name
                                         .concat(' ')
                                         .concat(Person.last_name)
                                         .alias('name'))
                           .where(Person.first_name
                           .concat(' ')
                           .concat(Person.last_name)
                           .contains(name)))
            return ([marshal(r, result) for r in query], 200, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

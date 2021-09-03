import logging
from email.utils import formatdate
from time import time

from obra_hacks.backend import data

from flask_restx import Resource, fields, marshal

logger = logging.getLogger(__name__)
cache_timeout = 86400


def register(api, cache):
    ns = api.namespace('disciplines', 'Race Disciplines')
    discipline = ns.model('RaceDiscipline',
                          {'name': fields.String,
                           'display': fields.String,
                           })

    @ns.route('/')
    @ns.response(200, 'Success', [discipline])
    @ns.response(400, 'Bad Request')
    @ns.response(500, 'Server Error')
    class People(Resource):
        @cache.cached(timeout=cache_timeout)
        def get(self):
            disciplines = [{'name': d, 'display': d.split('_')[0].title()} for d in sorted(data.DISCIPLINE_MAP.keys())]
            return ([marshal(d, discipline) for d in disciplines], 200, {'Expires': formatdate(timeval=time() + cache_timeout, usegmt=True)})

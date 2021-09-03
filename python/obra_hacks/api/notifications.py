import logging

from flask_restx import Resource, fields, marshal

logger = logging.getLogger(__name__)


def register(api, cache):
    # TODO - how am I going to store PushSubscription data now that there's no subscriptionId?
    # Now need some way to store it as part of session data or something, but I don't want
    # to require registration...
    ns = api.namespace('notifications', 'Push Notifications')

    response = fields.Integer

    @ns.route('/')
    @ns.response(200, 'Success', response)
    @ns.response(400, 'Bad Request')
    @ns.response(500, 'Server Error')
    class People(Resource):
        def get(self):
            return (marshal(1, response), 200, {'Cache-Control': 'no-cache, no-store'})

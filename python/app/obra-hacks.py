import os
import logging
from importlib import import_module

from flask import Blueprint, Flask
from flask_caching import Cache
from flask_restx import Api

logger = logging.getLogger(__name__)


def create_application():
    app = Flask(__name__, static_folder=None)
    app_api_v1 = Blueprint('api_v1', __name__)
    api = Api(app_api_v1, version='1.0', title='OBRA Hacks', contact='brad@oatmail.org')
    cache = Cache(app=app, with_jinja2_ext=False, config={'CACHE_TYPE': os.environ.get('CACHE_TYPE', 'uwsgi'),
                                                          'CACHE_UWSGI_NAME': 'default'})

    for name in ['disciplines', 'events', 'notifications', 'people', 'ranks', 'results', 'upgrades']:
        module_name = 'obra_hacks.api.' + name
        try:
            import_module(module_name).register(api, cache)
        except Exception:
            logger.error('Unable to import module ' + module_name)

    app.register_blueprint(app_api_v1, url_prefix='/api/v1')
    return app


application = create_application()

if __name__ == '__main__':
    logging.basicConfig(level="DEBUG")
    application.run(debug=True)

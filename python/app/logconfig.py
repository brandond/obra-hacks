import logging
import os

logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'), format='[python %(name)s pid: %(process)d] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
logger.info('{} imported'.format(__name__))

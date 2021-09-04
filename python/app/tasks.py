import logging
import os
from datetime import date

from obra_hacks.backend import data, models, rankings, scrapers, upgrades

import uwsgi
from uwsgidecorators import rbtimer

logger = logging.getLogger(__name__)
logger.info('{} imported'.format(__name__))
full_scrape_done = False


@rbtimer(60, target='spooler')
def scrape_events(num):
    # Most of this is cribbed from obra_hacks.backend.commands
    if 'NO_SCRAPE' in os.environ:
        logger.debug('Year scrape disabled by NO_SCRAPE')
        return

    global full_scrape_done
    cur_year = date.today().year

    if full_scrape_done:
        years = [cur_year]
    else:
        years = range(cur_year - 6, cur_year + 1)

    for discipline in data.DISCIPLINE_MAP.keys():
        clear_cache = False

        # Do the entire discipline re-scrape in a transaction
        with models.db.atomic('IMMEDIATE'):
            for year in years:
                scrapers.scrape_year(year, discipline)
                scrapers.scrape_parents(year, discipline)
                scrapers.clean_events(year, discipline)

            if scrapers.scrape_new(discipline) or not full_scrape_done:
                if upgrades.recalculate_points(discipline, incremental=full_scrape_done):
                    rankings.calculate_race_ranks(discipline, incremental=full_scrape_done)
                    upgrades.sum_points(discipline)
                    upgrades.confirm_pending_upgrades(discipline)
                    clear_cache = True

        if clear_cache:
            uwsgi.cache_clear('default')

    full_scrape_done = True


@rbtimer(1800, target='spooler')
def scrape_recent(num):
    if 'NO_SCRAPE' in os.environ:
        logger.debug('Recent event re-scrape disabled by NO_SCRAPE')
        return

    for discipline in data.DISCIPLINE_MAP.keys():
        clear_cache = False

        # Do the entire discipline update in a transaction
        with models.db.atomic('IMMEDIATE'):
            if scrapers.scrape_recent(discipline, 3):
                if upgrades.recalculate_points(discipline, incremental=True):
                    rankings.calculate_race_ranks(discipline, incremental=True)
                    upgrades.sum_points(discipline)
                    upgrades.confirm_pending_upgrades(discipline)
                    clear_cache = True

        if clear_cache:
            uwsgi.cache_clear('default')

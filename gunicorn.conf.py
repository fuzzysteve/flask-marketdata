import os

bind = f"{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('PORT', '8001')}"
workers = int(os.environ.get('WORKERS', 4))
# Load the app once in the master and fork the workers from it.
preload_app = True
timeout = 30
# API clients send long /aggregates/?types=... URLs (up to ~8k). gunicorn's
# default (4094) rejected them with 400; 8190 is its maximum short of
# unlimited and matches nginx's default 8k request-line limit.
limit_request_line = 8190
accesslog = 'logs/access.log'
errorlog = 'logs/error.log'
pidfile = 'logs/gunicorn.pid'
# Request, status-code, worker and log metrics over statsd (e.g. Netdata on
# 127.0.0.1:8125). Off unless STATSD_HOST is set.
statsd_host = os.environ.get('STATSD_HOST') or None
statsd_prefix = os.environ.get('STATSD_PREFIX', 'market')

import os

bind = f"{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('PORT', '8001')}"
workers = int(os.environ.get('WORKERS', 4))
# Preload so the random fallback SECRET_KEY is shared by all workers.
preload_app = True
timeout = 30
accesslog = 'logs/access.log'
errorlog = 'logs/error.log'
pidfile = 'logs/gunicorn.pid'
# Request, status-code, worker and log metrics over statsd (e.g. Netdata on
# 127.0.0.1:8125). Off unless STATSD_HOST is set.
statsd_host = os.environ.get('STATSD_HOST') or None
statsd_prefix = os.environ.get('STATSD_PREFIX', 'market')

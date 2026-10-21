import logging
import os
import secrets
import time

from flask import Flask, Response

from . import phpcompat

# Where the loaders write aggregatecsv.csv.gz (the PHP site's public/ dir).
PUBLIC_DIR = os.environ.get('PUBLIC_DIR', os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'public')))
ORDERBOOK_DIR = os.environ.get('ORDERBOOK_DIR', '/opt/orderbooks')


def json_response(data, expires_in=None, status=200):
    response = Response(phpcompat.php_json(data), status=status,
                        content_type='application/json;charset=utf-8')
    if expires_in is not None:
        response.expires = int(time.time()) + expires_in
    return response


def create_app():
    app = Flask(__name__, static_folder=None)

    secret_key = os.environ.get('PYAPP_SECRET_KEY')
    if not secret_key:
        app.logger.warning('PYAPP_SECRET_KEY not set; using a random key (sessions reset on restart)')
        secret_key = secrets.token_hex(32)
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_SECURE=os.environ.get('PYAPP_SECURE_COOKIES', '0') == '1',
        SESSION_COOKIE_SAMESITE='Lax',
        ALLOW_WRITES=os.environ.get('PYAPP_ALLOW_WRITES', '0') == '1',
    )

    # Twig escaped every template; Flask only autoescapes .html by default.
    app.jinja_env.autoescape = True
    app.jinja_env.filters.update(
        number_format=phpcompat.number_format,
        date=phpcompat.date_filter,
        split=phpcompat.split_filter,
        json_encode=phpcompat.php_json,
    )

    @app.after_request
    def default_cache_control(response):
        # Replaces the Slim HttpCache('public', 300) middleware.
        if 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'public, max-age=300'
        return response

    from .routes import api, appraisal, auth, pages, static
    for module in (pages, api, appraisal, auth, static):
        app.register_blueprint(module.bp)

    if not app.debug:
        app.logger.setLevel(logging.INFO)
    return app

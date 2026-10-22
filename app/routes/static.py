"""Static files. Site assets live in app/static; generated data (the aggregate
CSV dump written by the loaders, and the order books) is served from where the
loaders put it. Only known paths are exposed."""
import os

from flask import Blueprint, send_from_directory

from .. import ORDERBOOK_DIR, PUBLIC_DIR

bp = Blueprint('static_files', __name__)

APP_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

STATIC_FILES = ('robots.txt', '504.html', 'favicon.ico', 'favicon.svg', 'apple-touch-icon.png')


@bp.route('/css/<path:filename>')
def css(filename):
    return send_from_directory(f'{APP_STATIC_DIR}/css', filename)


@bp.route('/js/<path:filename>')
def js(filename):
    return send_from_directory(f'{APP_STATIC_DIR}/js', filename)


@bp.route('/<any(' + ', '.join(f'"{f}"' for f in STATIC_FILES) + '):filename>')
def static_file(filename):
    return send_from_directory(APP_STATIC_DIR, filename)


@bp.route('/aggregatecsv.csv.gz')
def aggregate_csv():
    # nginx serves this (and /orderbooks/) in production. Passing a mimetype stops
    # Werkzeug guessing text/csv + Content-Encoding: gzip, which made clients
    # unpack the file; nginx sends the raw gzip bytes, as the PHP site did.
    return send_from_directory(PUBLIC_DIR, 'aggregatecsv.csv.gz', mimetype='application/octet-stream')


@bp.route('/orderbooks/<path:filename>')
def orderbook(filename):
    return send_from_directory(ORDERBOOK_DIR, filename, mimetype='application/octet-stream')

import hmac
import os
import re
import secrets
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, redirect, request, session

bp = Blueprint('auth', __name__)

AUTHORIZE_URL = 'https://login.eveonline.com/oauth/authorize'
TOKEN_URL = 'https://login.eveonline.com/oauth/token'
REDIRECT_URI = 'https://market.fuzzwork.co.uk/auth/updater'
SCOPES = 'esi-universe.read_structures.v1 esi-markets.structure_markets.v1'
USER_AGENT = 'Fuzzwork Market (market.fuzzwork.co.uk)'
SECRETS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src', 'secretreal.php')


def _credentials():
    """Env vars first; otherwise read the PHP site's secretreal.php (read-only)."""
    client_id = os.environ.get('PYAPP_EVE_CLIENT_ID')
    client_secret = os.environ.get('PYAPP_EVE_CLIENT_SECRET')
    if client_id and client_secret:
        return client_id, client_secret
    try:
        with open(SECRETS_FILE) as f:
            source = f.read()
    except OSError:
        return client_id, client_secret
    found = dict(re.findall(r'\$(clientid|secret)\s*=\s*[\'"]([^\'"]*)[\'"]', source))
    return client_id or found.get('clientid'), client_secret or found.get('secret')


def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/authlogin')
def login():
    client_id, _ = _credentials()
    state = secrets.token_hex(16)
    session['oauth_state'] = state
    query = urlencode({'response_type': 'code', 'redirect_uri': REDIRECT_URI,
                       'client_id': client_id or '', 'scope': SCOPES, 'state': state})
    return _no_store(redirect(f'{AUTHORIZE_URL}?{query}'))


@bp.route('/auth/updater')
def updater():
    expected = session.pop('oauth_state', None)
    state = request.args.get('state')
    if not expected or not state or not hmac.compare_digest(expected, state):
        return _no_store(current_app.response_class(status=403))

    client_id, client_secret = _credentials()
    try:
        requests.post(TOKEN_URL, auth=(client_id or '', client_secret or ''),
                      data={'grant_type': 'authorization_code', 'code': request.args.get('code', '')},
                      headers={'User-Agent': USER_AGENT}, timeout=10)
    except requests.RequestException:
        return _no_store(current_app.response_class(status=502))
    # Token values are intentionally not written to the response body.
    return _no_store(current_app.response_class(status=200))

import json
import secrets

from flask import Blueprint, abort, current_app, redirect, render_template, request

from .. import appraisal_parser, cache, db, json_response
from ..phpcompat import php_mul, php_number

bp = Blueprint('appraisal', __name__)


def _typeid_lookup():
    def build():
        rows = db.fetch_all('select lower("typeName") "typeName","typeID" from evesde."invTypes" '
                            'where published=true and "marketGroupID" is not null')
        return {row['typeName']: row['typeID'] for row in rows}
    return cache.cached_lookup('sde:typeidlookup', build)


def _typename_lookup():
    def build():
        rows = db.fetch_all('select "typeName","typeID",volume from evesde."invTypes" '
                            'where published=true and "marketGroupID" is not null')
        return {str(row['typeID']): {'typeName': row['typeName'], 'volume': row['volume']}
                for row in rows}
    return cache.cached_lookup('sde:typenamelookup', build)


@bp.route('/appraisal/', methods=['GET'])
def form():
    return render_template('appraisal.phtml')


@bp.route('/appraisal/', methods=['POST'])
def submit():
    region = request.form.get('region')
    if not region or not region.isascii() or not region.isdigit():
        return ''
    if 'pasteblock' not in request.form:
        return ''

    inventory = appraisal_parser.parse(request.form['pasteblock'], _typeid_lookup())
    if inventory:
        if not current_app.config['ALLOW_WRITES']:
            return 'Appraisal saving is disabled on this instance (PYAPP_ALLOW_WRITES=0).', 503
        identifier = secrets.token_hex(9)
        db.execute('insert into appraisal (identifier,list) values (%(id)s,%(list)s)',
                   {'id': identifier,
                    'list': json.dumps({str(k): v for k, v in inventory.items()})})
        return redirect(f'/appraisal/{region}/{identifier}')
    return render_template('appraisal.phtml')


@bp.route('/appraisal/<int:region>/<identifier>', defaults={'as_json': None})
@bp.route('/appraisal/<int:region>/<identifier>/<as_json>')
def display(region, identifier, as_json):
    typenames = _typename_lookup()
    region_row = db.fetch_one('select "regionName","regionID" from evesde."mapRegions" '
                              'where "regionID"=%(region)s', {'region': region})
    if not region_row:
        abort(404)
    regionid = region_row['regionID']

    row = db.fetch_one('select list from appraisal where identifier=%(id)s', {'id': identifier})
    if not row:
        abort(404)
    items = row['list'] if isinstance(row['list'], dict) else {}
    appraisal, total = _price(regionid, items, typenames)

    # Same keys, in the same order, as the PHP's $args.
    args = {'region': region_row['regionName'], 'identifier': identifier}
    if as_json is not None:
        args['json'] = as_json
    args.update(regionid=regionid, appraisal=appraisal, total=total)

    if as_json not in (None, '', '0'):
        return json_response(args, expires_in=300)
    return render_template('displayappraisal.phtml', **args)


def _price(regionid, items, typenames):
    """Prices {typeID string: quantity} in a region from the Redis aggregates.
    Returns the appraisal lines and their totals."""
    # Fetch all buy/sell keys with MGET (in chunks) rather than one GET round trip per key
    keys = []
    for item in items:
        keys += [f'{regionid}|{item}|true', f'{regionid}|{item}|false']
    values = iter(cache.mget_chunked(keys))

    appraisal = []
    total = {'pbuy': 0, 'mbuy': 0, 'psell': 0, 'msell': 0, 'volume': 0}
    for item, quantity in items.items():
        buy = (next(values) or '').split('|')
        sell = (next(values) or '').split('|')
        info = typenames.get(item, {})
        line = {
            'typeid': item,
            'typename': info.get('typeName', ''),
            'quantity': quantity,
            'pbuy': php_mul(quantity, buy[7] if len(buy) > 7 else 0),
            'psell': php_mul(quantity, sell[7] if len(sell) > 7 else 0),
            'mbuy': php_mul(quantity, buy[1] if len(buy) > 1 else 0),
            'msell': php_mul(quantity, sell[2] if len(sell) > 2 else 0),
            'volume': php_mul(quantity, info.get('volume', 0)),
        }
        for key in total:
            total[key] += php_number(line[key])
        appraisal.append(line)
    return appraisal, total


# Flask's automatic OPTIONS would answer the preflight without the CORS headers.
@bp.route('/api/appraisal/', methods=['POST'], provide_automatic_options=False)
def api_appraisal():
    """Prices a paste without saving it. The response is the saved appraisal's
    JSON without the identifier."""
    body = request.form if request.form else (request.get_json(silent=True) or {})
    if not isinstance(body, dict):
        body = {}
    region = str(body.get('region', ''))
    pasteblock = body.get('pasteblock')
    if not region.isascii() or not region.isdigit():
        return _api_error('Give a regionID as region, e.g. 10000002 for The Forge')
    if not isinstance(pasteblock, str):
        return _api_error('Give the items to price as pasteblock')
    region_row = db.fetch_one('select "regionName","regionID" from evesde."mapRegions" '
                              'where "regionID"=%(region)s', {'region': int(region)})
    if not region_row:
        return _api_error(f'Not a region: {region}')

    inventory = appraisal_parser.parse(pasteblock, _typeid_lookup())
    appraisal, total = _price(region_row['regionID'], {str(k): v for k, v in inventory.items()},
                              _typename_lookup())
    return _api_cors(json_response({'region': region_row['regionName'],
                                    'regionid': region_row['regionID'],
                                    'appraisal': appraisal, 'total': total}))


@bp.route('/api/appraisal/', methods=['OPTIONS'])
def api_appraisal_preflight():
    # A JSON POST from a browser on another site sends this first.
    response = _api_cors(current_app.response_class(status=204))
    response.headers['Access-Control-Allow-Methods'] = 'POST'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Max-Age'] = '86400'
    return response


def _api_error(message):
    return _api_cors(json_response({'error': message}, status=400))


def _api_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Cache-Control'] = 'no-store'
    return response

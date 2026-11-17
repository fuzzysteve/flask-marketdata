import datetime
import glob

from flask import Blueprint, redirect, render_template, request

from .. import ORDERBOOK_DIR, cache, db, history, json_response, orders, phpcompat
from .pages import REGION_LIST_SQL

bp = Blueprint('api', __name__)

AGGREGATE_FIELDS = ('weightedAverage', 'max', 'min', 'stddev', 'median', 'volume',
                    'orderCount', 'percentile')


def _location(params):
    for name in ('region', 'station', 'system'):
        if name in params:
            return params[name]
    return None


def _aggregate_side(value):
    """One side of a Redis aggregate as the /aggregates/ fields, 0 where missing."""
    details = (value or '|||||||').split('|')
    details += [None] * (len(AGGREGATE_FIELDS) - len(details))
    return {field: phpcompat.php_truthy_or_zero(details[i])
            for i, field in enumerate(AGGREGATE_FIELDS)}


@bp.route('/aggregates/', methods=['GET', 'POST'])
def aggregates():
    region = _location(request.args)
    if region is None:
        region = 0
    types = request.args.get('types', '')
    body = request.form if request.form else (request.get_json(silent=True) or {})
    if isinstance(body, dict) and 'types' in body:
        types = body['types']
        posted = _location(body)
        if posted is not None:
            region = posted
    if isinstance(types, list):
        types = ','.join(str(t) for t in types)

    typelist = [t for t in str(types).split(',') if t != '']
    keys = [f'{region}|{t}|{buy}' for t in typelist for buy in ('true', 'false')]
    values = iter(cache.mget_chunked(keys))

    result = {}
    for t in typelist:
        result[t] = {side: _aggregate_side(next(values)) for side in ('buy', 'sell')}

    response = json_response(phpcompat.php_array(result), expires_in=300)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@bp.route('/api/aggregates/', methods=['GET', 'POST'])
def aggregates_alias():
    # A commonly guessed URL. 308 keeps the method and body for POSTs.
    query = request.query_string.decode()
    return redirect('/aggregates/' + (f'?{query}' if query else ''), code=308)


@bp.route('/api/orderset')
def orderset():
    row = db.fetch_one('select max(id) id from orderset')
    return json_response({'orderset': row['id']}, expires_in=300)


@bp.route('/api/typeids')
def typeids():
    rows = db.fetch_all('select "typeID" as value,"typeName" as label from evesde."invTypes" '
                        'where "marketGroupID" is not null order by "typeName"')
    return json_response(rows, expires_in=3600)


@bp.route('/api/regionids')
def regionids():
    return json_response(db.fetch_all(REGION_LIST_SQL), expires_in=3600)


@bp.route('/api/stationids')
def stationids():
    rows = db.fetch_all('select "stationID" as value,"stationName" as label '
                        'from evesde."staStations" order by "stationName"')
    return json_response(rows, expires_in=3600)


@bp.route('/api/marketgroup/<parent>/')
def marketgroup(parent):
    if not parent.isdigit():
        return '', 404
    params = {'parent': int(parent)}
    marketgroups = db.fetch_all(
        'select "parentGroupID","marketGroupID","marketGroupName","iconID","hasTypes" '
        'from evesde."invMarketGroups" where "parentGroupID"=%(parent)s '
        'or (%(parent)s=0 and "parentGroupID" is null) order by "marketGroupName"', params)
    types = db.fetch_all('select "typeID","typeName","iconID" from evesde."invTypes" '
                         'where "marketGroupID"=%(parent)s order by "typeName"', params)
    # groupid stays the raw path string, as Slim passed it through.
    return json_response({'marketgroups': marketgroups, 'types': types, 'groupid': parent},
                         expires_in=300)


_API_ORDER_SQL = '''
        SELECT "orderID",orders."typeID",issued,orders.volume,"volumeEntered","minVolume",round(price,2) price,orders."stationID",{range}duration,"stationName","typeName","regionName",orders.region,ms.security,sta."solarSystemID"
        FROM orders
        JOIN evesde."staStations" sta on orders."stationID"=sta."stationID"
        JOIN evesde."invTypes" it on orders."typeID"=it."typeID"
        JOIN evesde."mapRegions" mr on mr."regionID"=orders.region
        JOIN evesde."mapSolarSystems" ms on ms."solarSystemID"=sta."solarSystemID"
        WHERE orders."typeID"=%(typeid)s
        AND orders."orderSet"=%(orderset)s
        AND ( orders.region=%(region)s or %(region)s=0)
        AND buy = {buy}
        order by price {direction},region
'''


@bp.route('/api/region/<int:region>/type/<int:typeid>/')
def region_type(region, typeid):
    orderset = orders.latest_orderset()
    params = {'typeid': typeid, 'orderset': orderset, 'region': region}
    sellorders = db.fetch_all(_API_ORDER_SQL.format(range='', buy='False', direction='asc'), params)
    buyorders = db.fetch_all(_API_ORDER_SQL.format(range='range,', buy='True', direction='desc'), params)
    return json_response({'typename': orders.type_name(typeid), 'orderset': orderset,
                          'buyorders': buyorders, 'sellorders': sellorders}, expires_in=300)


@bp.route('/api/history/<int:location>/<int:typeid>/')
def price_history(location, typeid):
    # location: 0 (New Eden), a region or a trade hub station. Snapshots are taken
    # four times a day; months over a year old keep one a day.
    response = json_response({'location': location, 'typeid': typeid,
                              'history': history.series(location, typeid)}, expires_in=1800)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


# Bulk history is bounded by types x days (about 230 bytes per snapshot row, four
# rows a day): 100 types for 100 days, or a dozen types for the whole history.
BULK_HISTORY_MAX_TYPES = 100
BULK_HISTORY_MAX_TYPE_DAYS = 10000
BULK_HISTORY_DEFAULT_DAYS = 90


def _cors_json(data, status=200, expires_in=None):
    response = json_response(data, expires_in=expires_in, status=status)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


def _parse_day(text):
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


class _BadRequest(Exception):
    pass


def _error(message):
    return _cors_json({'error': message}, status=400)


def _typeids_arg(limit):
    """The types=34,35,36 argument as a list of ints, without repeats."""
    typeids = []
    for part in request.args.get('types', '').split(','):
        part = part.strip()
        if part == '':
            continue
        if not part.isascii() or not part.isdigit():
            raise _BadRequest(f'Not a typeID: {part}')
        if int(part) not in typeids:
            typeids.append(int(part))
    if not typeids:
        raise _BadRequest('Give the typeIDs as types=34,35,36')
    if len(typeids) > limit:
        raise _BadRequest(f'At most {limit} types per request')
    return typeids


@bp.route('/api/history/<int:location>/')
def bulk_price_history(location):
    try:
        typeids = _typeids_arg(BULK_HISTORY_MAX_TYPES)
    except _BadRequest as e:
        return _error(str(e))

    end = datetime.datetime.now(datetime.timezone.utc).date()
    if request.args.get('to'):
        end = _parse_day(request.args['to'])
        if end is None:
            return _error('to must be a date, YYYY-MM-DD')
    start = end - datetime.timedelta(days=BULK_HISTORY_DEFAULT_DAYS - 1)
    if request.args.get('from'):
        start = _parse_day(request.args['from'])
        if start is None:
            return _error('from must be a date, YYYY-MM-DD')
    days = (end - start).days + 1
    if days < 1:
        return _error('from is after to')
    if days * len(typeids) > BULK_HISTORY_MAX_TYPE_DAYS:
        return _error(f'Too much at once: types x days can be up to {BULK_HISTORY_MAX_TYPE_DAYS} '
                     f'(this asked for {len(typeids)} x {days}). Ask for fewer types or a shorter range.')

    series = history.bulk_series(location, typeids, start, end)
    return _cors_json({'location': location, 'from': start.isoformat(), 'to': end.isoformat(),
                       'history': {str(typeid): rows for typeid, rows in series.items()}},
                      expires_in=1800)


# One indexed lookup per type, so far more types than the bulk history:
# 1,000 take under 0.2s.
AT_HISTORY_MAX_TYPES = 1000


def _parse_time(text):
    """A UTC date or date and time. A bare date means the end of that day."""
    day = _parse_day(text)
    if day is not None:
        return datetime.datetime.combine(day, datetime.time.max), day.isoformat()
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None, None
    if when.tzinfo is not None:
        when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return when, when.strftime('%Y-%m-%d %H:%M:%S')


@bp.route('/api/history/<int:location>/at/<when>/')
def price_history_at(location, when):
    try:
        typeids = _typeids_arg(AT_HISTORY_MAX_TYPES)
    except _BadRequest as e:
        return _error(str(e))
    at, label = _parse_time(when)
    if at is None:
        return _error('The time must be a UTC date or date and time: 2026-11-01 or 2026-11-01T12:00')
    prices = history.prices_at(location, typeids, at)
    return _cors_json({'location': location, 'at': label,
                       'prices': {str(typeid): row for typeid, row in prices.items()}},
                      expires_in=1800)


def _hubs():
    return cache.cached_lookup('hubs', lambda: {'hubs': history.hub_locations()})['hubs']


@bp.route('/api/hubs/type/<int:typeid>/')
def hubs_type(typeid):
    hubs = _hubs()
    keys = [f'{hub["id"]}|{typeid}|{buy}' for hub in hubs for buy in ('true', 'false')]
    values = iter(cache.mget_chunked(keys))
    result = [{'station': hub['id'], 'name': hub['name'],
               'buy': _aggregate_side(next(values)), 'sell': _aggregate_side(next(values))}
              for hub in hubs]
    return _cors_json({'typeid': typeid, 'typename': orders.type_name(typeid), 'hubs': result},
                      expires_in=300)


@bp.route('/api/')
def api_index():
    files = sorted(glob.glob(f'{ORDERBOOK_DIR}/*.csv.gz'), key=phpcompat.natural_key, reverse=True)
    return render_template('api.phtml', files=files)

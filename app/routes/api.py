import glob

from flask import Blueprint, redirect, render_template, request

from .. import ORDERBOOK_DIR, cache, db, json_response, orders, phpcompat
from .pages import REGION_LIST_SQL

bp = Blueprint('api', __name__)

AGGREGATE_FIELDS = ('weightedAverage', 'max', 'min', 'stddev', 'median', 'volume',
                    'orderCount', 'percentile')


def _location(params):
    for name in ('region', 'station', 'system'):
        if name in params:
            return params[name]
    return None


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
        entry = {}
        for side in ('buy', 'sell'):
            details = (next(values) or '|||||||').split('|')
            details += [None] * (len(AGGREGATE_FIELDS) - len(details))
            entry[side] = {field: phpcompat.php_truthy_or_zero(details[i])
                           for i, field in enumerate(AGGREGATE_FIELDS)}
        result[t] = entry

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


@bp.route('/api/')
def api_index():
    files = sorted(glob.glob(f'{ORDERBOOK_DIR}/*.csv.gz'), key=phpcompat.natural_key, reverse=True)
    return render_template('api.phtml', files=files)

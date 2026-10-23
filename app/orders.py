"""Order-book queries shared by the type/region/hub/station/empire/plex pages.
The SQL is the same as src/routes.php, with the per-page join and filter
factored out."""
import redis

from . import cache, db

LATEST_ORDERSET_TTL = 30

_ORDER_COLUMNS = ('"orderID",orders."typeID",issued,orders.volume,"volumeEntered","minVolume",'
                  'price,orders."stationID",{range}duration,"stationName","typeName","regionName",region')

_ORDER_SQL = '''
        SELECT {columns}
        FROM orders
        JOIN evesde."staStations" sta on orders."stationID"=sta."stationID"
        JOIN evesde."invTypes" it on orders."typeID"=it."typeID"
        JOIN evesde."mapRegions" mr on mr."regionID"=region
        {join}
        WHERE orders."typeID"=%(typeid)s
        AND orders."orderSet"=%(orderset)s
        {where}
        AND buy = {buy}
        order by price {direction},region
'''


def latest_orderset():
    """Cached briefly in Redis: every order-book page needs it, a new orderset
    only arrives every 30 minutes, and old ones are kept, so a slightly stale id
    still has its orders. Falls back to the DB if Redis is unavailable."""
    key = cache.KEY_PREFIX + 'latest_orderset'
    try:
        cached = cache.client().get(key)
    except redis.RedisError:
        cached = None
    if cached is not None:
        return int(cached)
    orderset = db.fetch_one('select max(id) from orderset')['max']
    try:
        cache.client().setex(key, LATEST_ORDERSET_TTL, orderset)
    except redis.RedisError:
        pass
    return orderset


def type_name(typeid):
    row = db.fetch_one('select "typeName" from evesde."invTypes" where "typeID"=%(typeid)s',
                       {'typeid': typeid})
    return row['typeName'] if row else ''


def type_image(typeid):
    """The type's picture on CCP's image server, for link previews. Blueprints
    (and reaction formulas, also category 9) have no 'icon' variant, only 'bp'."""
    row = db.fetch_one('select "categoryID" from evesde."invTypes" it '
                       'join evesde."invGroups" ig on ig."groupID"=it."groupID" '
                       'where it."typeID"=%(typeid)s', {'typeid': typeid})
    variation = 'bp' if row and row['categoryID'] == 9 else 'icon'
    return f'https://images.evetech.net/types/{typeid}/{variation}?size=128'


def region_name(region):
    row = db.fetch_one('select "regionName" from evesde."mapRegions" where "regionID"=%(region)s',
                       {'region': region})
    return row['regionName'] if row else ''


def station_name(station):
    row = db.fetch_one('select "stationName" from evesde."staStations" where "stationID"=%(station)s',
                       {'station': station})
    return row['stationName'] if row else ''


def orders_for_type(typeid, orderset, join='', where='', params=None):
    """Returns (sellorders, buyorders) for one type in one orderset."""
    params = dict(params or {}, typeid=typeid, orderset=orderset)
    sell_sql = _ORDER_SQL.format(columns=_ORDER_COLUMNS.format(range=''), join=join,
                                 where=where, buy='False', direction='asc')
    buy_sql = _ORDER_SQL.format(columns=_ORDER_COLUMNS.format(range='range,'), join=join,
                                where=where, buy='True', direction='desc')
    return db.fetch_all(sell_sql, params), db.fetch_all(buy_sql, params)


def type_page_args(typeid, orderset=None, typename=None, scope='', **query):
    """scope names the page's location (a region, station, ...) in link previews."""
    if orderset is None:
        orderset = latest_orderset()
    sellorders, buyorders = orders_for_type(typeid, orderset, **query)
    return {
        'type': typeid,
        'typename': type_name(typeid) if typename is None else typename,
        'typeimage': type_image(typeid),
        'scope': scope,
        'orderset': orderset,
        'buyorders': buyorders,
        'sellorders': sellorders,
    }

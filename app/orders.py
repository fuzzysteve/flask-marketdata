"""Order-book queries shared by the type/region/hub/station/empire/plex pages.
The SQL is the same as src/routes.php, with the per-page join and filter
factored out."""
from . import db

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
    return db.fetch_one('select max(id) from orderset')['max']


def type_name(typeid):
    row = db.fetch_one('select "typeName" from evesde."invTypes" where "typeID"=%(typeid)s',
                       {'typeid': typeid})
    return row['typeName'] if row else ''


def orders_for_type(typeid, orderset, join='', where='', params=None):
    """Returns (sellorders, buyorders) for one type in one orderset."""
    params = dict(params or {}, typeid=typeid, orderset=orderset)
    sell_sql = _ORDER_SQL.format(columns=_ORDER_COLUMNS.format(range=''), join=join,
                                 where=where, buy='False', direction='asc')
    buy_sql = _ORDER_SQL.format(columns=_ORDER_COLUMNS.format(range='range,'), join=join,
                                where=where, buy='True', direction='desc')
    return db.fetch_all(sell_sql, params), db.fetch_all(buy_sql, params)


def type_page_args(typeid, orderset=None, typename=None, **query):
    if orderset is None:
        orderset = latest_orderset()
    sellorders, buyorders = orders_for_type(typeid, orderset, **query)
    return {
        'type': typeid,
        'typename': type_name(typeid) if typename is None else typename,
        'orderset': orderset,
        'buyorders': buyorders,
        'sellorders': sellorders,
    }

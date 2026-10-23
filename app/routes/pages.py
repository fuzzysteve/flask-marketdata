import json

from flask import Blueprint, render_template

from .. import cache, db, orders
from ..history import chart_locations, hub_locations

bp = Blueprint('pages', __name__)

INDEX_TYPES = (34, 35, 36, 37, 38, 39, 40, 11399, 40520)
INDEX_TYPE_NAMES = ("Tritanium", "Pyrite", "Mexallon", "Isogen", "Nocxium", "Zydrine",
                    "Megacyte", "Morphite", "Skill Injector")
JITA_44 = 60003760
PLEX = 44992

REGION_LIST_SQL = ('select "regionID" as value,"regionName" as label from evesde."mapRegions" '
                   'where "regionID"<11000000 and "regionID" not in (10000004,10000017) '
                   'order by "regionName"')


def _split(value):
    return (value or '').split('|')


@bp.route('/')
def index():
    r = cache.client()
    keys = [f'{JITA_44}|{t}|{buy}' for t in INDEX_TYPES for buy in ('true', 'false')]
    keys += [f'{JITA_44}|{PLEX}|true', f'{JITA_44}|{PLEX}|false', 'fp-buy', 'fp-sell', 'fp-lastupdate']
    values = iter(r.mget(keys))
    # A list rather than a dict: the template loops over values, as Twig did.
    maggs = [{'true': _split(next(values)), 'false': _split(next(values))} for _ in INDEX_TYPES]
    plex = {'true': _split(next(values)), 'false': _split(next(values))}
    fpbuy, fpsell, fplastupdate = next(values), next(values), next(values)
    return render_template('index.phtml',
                           fpbuy=json.loads(fpbuy) if fpbuy else None,
                           fpsell=json.loads(fpsell) if fpsell else None,
                           fplastupdate=fplastupdate,
                           types=INDEX_TYPE_NAMES, maggs=maggs, plex=plex)


@bp.route('/plex/')
def plex():
    # PLEX only trades on the separate virtual PLEX Market (region 19000001), not the
    # regular regional markets, and its orderset can lag the rest of the app's, so use
    # the newest orderset that actually has PLEX data rather than the global max.
    orderset = db.fetch_one('select max("orderSet") from orders where "typeID"=%(typeid)s',
                            {'typeid': PLEX})['max']
    return render_template('type.phtml', history=chart_locations(0),
                           **orders.type_page_args(PLEX, orderset=orderset, typename='PLEX', scope='New Eden',
                                                   preview_station=JITA_44, preview_label='Jita 4-4'))


@bp.route('/type/')
def types():
    return render_template('types.phtml')


@bp.route('/hub/')
def hub():
    return render_template('hub.phtml')


@bp.route('/region/')
def region():
    return render_template('region.phtml')


@bp.route('/station/')
def station():
    return render_template('station.phtml')


@bp.route('/aggregate/')
def aggregate():
    return render_template('aggregate.phtml')


@bp.route('/about/')
def about():
    return render_template('about.phtml')


@bp.route('/browser/')
def browser():
    return render_template('browser.phtml', regions=db.fetch_all(REGION_LIST_SQL))


@bp.route('/type/<int:typeid>/')
def type_orders(typeid):
    return render_template('type.phtml', history=chart_locations(0),
                           **orders.type_page_args(typeid, scope='New Eden',
                                                   preview_station=JITA_44, preview_label='Jita 4-4'))


@bp.route('/region/<int:region>/type/<int:typeid>/')
def region_type(region, typeid):
    return render_template('type.phtml', history=chart_locations(region), **orders.type_page_args(
        typeid, scope=orders.region_name(region),
        where='AND region=%(region)s', params={'region': region}))


@bp.route('/empire/type/<int:typeid>/')
def empire_type(typeid):
    return render_template('type.phtml', **orders.type_page_args(
        typeid, join='join "marketRegion" on orders.region="marketRegion".regionid',
        where='AND regiontype <= 2', scope='Empire'))


@bp.route('/hub/type/<int:typeid>/')
def hub_type(typeid):
    return render_template('type.phtml', history=hub_locations(), **orders.type_page_args(
        typeid, scope='Trade hubs', preview_station=JITA_44, preview_label='Jita 4-4',
        join='join hubstations on orders."stationID"=hubstations.stationID'))


@bp.route('/station/<int:station>/type/<int:typeid>/')
def station_type(station, typeid):
    return render_template('type.phtml', history=chart_locations(station), **orders.type_page_args(
        typeid, scope=orders.station_name(station),
        where='AND orders."stationID"=%(station)s', params={'station': station}))


_HISTORY_SQL = '''
        SELECT "orderID","orderSet",orders."typeID",issued,orders.volume,"volumeEntered","minVolume",price,orders."stationID",{range}duration,"stationName","typeName","regionName"
        FROM orders
        JOIN evesde."staStations" sta on orders."stationID"=sta."stationID"
        JOIN evesde."invTypes" it on orders."typeID"=it."typeID"
        JOIN evesde."mapRegions" mr on mr."regionID"=region
        AND orders."orderID"=%(orderid)s
        AND buy = {buy}
        order by "orderSet" desc
'''


@bp.route('/history/<int:orderid>')
def history(orderid):
    params = {'orderid': orderid}
    return render_template(
        'history.phtml',
        sellorders=db.fetch_all(_HISTORY_SQL.format(range='', buy='False'), params),
        buyorders=db.fetch_all(_HISTORY_SQL.format(range='range,', buy='True'), params))

"""Price history: snapshots of the loaders' Redis aggregates for New Eden (0),
every region and the trade hubs, stored in aggregatehistory.

Snapshots run four times a day (systemd timer, see systemd/). After a year a
month is thinned to the first snapshot of each UTC day. The table is
partitioned by month, so thinning rebuilds one partition and swaps it in,
which returns the space without a VACUUM FULL on the live table."""
import datetime
import logging

from . import cache, db

log = logging.getLogger(__name__)

# Keys are rewritten by every load with a 90-minute expiry. A key with more than
# an hour left was written by the latest load; anything older belongs to an item
# that dropped out of it and would be stale.
FRESH_TTL = 3600
# Refuse a snapshot much smaller than the previous one (a failed or partial load).
MIN_FRACTION = 0.5
KEEP_FULL = datetime.timedelta(days=365)
CHUNK = 10000

SCHEMA_SQL = '''
create table if not exists aggregatehistory (
    snapshot timestamp not null,
    location integer not null,
    "typeID" integer not null,
    buypercentile float8,
    buymax float8,
    buyvolume float8,
    sellpercentile float8,
    sellmin float8,
    sellvolume float8,
    buyorders integer,
    sellorders integer,
    primary key ("typeID", location, snapshot)
) partition by range (snapshot);
create table if not exists aggregatehistory_runs (
    snapshot timestamp primary key,
    "orderSet" integer not null,
    rows integer not null
);
'''

_COLUMNS = ('snapshot', 'location', '"typeID"', 'buypercentile', 'buymax', 'buyvolume',
            'sellpercentile', 'sellmin', 'sellvolume', 'buyorders', 'sellorders')


def ensure_schema(conn):
    conn.execute(SCHEMA_SQL)


def partition_name(month):
    return f'aggregatehistory_{month:%Y%m}'


def _month_start(when):
    return datetime.datetime(when.year, when.month, 1)


def _next_month(month):
    return datetime.datetime(month.year + month.month // 12, month.month % 12 + 1, 1)


def _bounds(month):
    # DDL can't take bind parameters; these are dates this module computes.
    return f"from ('{month:%Y-%m-%d}') to ('{_next_month(month):%Y-%m-%d}')"


def ensure_partition(conn, when):
    month = _month_start(when)
    conn.execute(f'create table if not exists {partition_name(month)} partition of aggregatehistory '
                 f'for values {_bounds(month)}')


def _locations():
    hubs = [row['stationid'] for row in db.fetch_all('select stationid from hubstations')]
    regions = [row['regionID'] for row in db.fetch_all(
        'select "regionID" from evesde."mapRegions" where "regionID"<11000000')]
    return {str(location) for location in [0] + regions + hubs}


def _number(value, cast=float):
    try:
        return cast(float(value))
    except (TypeError, ValueError):
        return None


def parse_aggregate(value):
    """(percentile, max, min, volume, orderCount) from a loader value
    'weightedAverage|max|min|stddev|median|volume|orderCount|percentile'."""
    if not value:
        return None
    fields = value.split('|') + [None] * 8
    return (_number(fields[7]), _number(fields[1]), _number(fields[2]),
            _number(fields[5]), _number(fields[6], int))


def collect(r, locations):
    """{(location, typeID): {'true': fields, 'false': fields}} for fresh keys."""
    keys = [key for key in r.scan_iter(match='*|*|*', count=CHUNK)
            if key.split('|', 1)[0] in locations]
    found = {}
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        pipe = r.pipeline(transaction=False)
        for key in chunk:
            pipe.ttl(key)
            pipe.get(key)
        replies = pipe.execute()
        for key, ttl, value in zip(chunk, replies[0::2], replies[1::2]):
            location, typeid, side = key.split('|')
            if ttl > FRESH_TTL and side in ('true', 'false') and typeid.isdigit():
                parsed = parse_aggregate(value)
                if parsed:
                    found.setdefault((int(location), int(typeid)), {})[side] = parsed
    return found


def rows_for(snapshot, found):
    empty = (None,) * 5
    for (location, typeid), sides in found.items():
        bpct, bmax, _, bvol, borders = sides.get('true', empty)
        spct, _, smin, svol, sorders = sides.get('false', empty)
        yield (snapshot, location, typeid, bpct, bmax, bvol, spct, smin, svol, borders, sorders)


def snapshot():
    """Record the current aggregates once per orderset. Returns rows written."""
    orderset = db.fetch_one('select id, downloaded from orderset order by id desc limit 1')
    when = datetime.datetime.fromisoformat(orderset['downloaded']).replace(microsecond=0)
    with db.pool().connection() as conn:
        ensure_schema(conn)
        if conn.execute('select 1 from aggregatehistory_runs where "orderSet"=%(id)s',
                        {'id': orderset['id']}).fetchone():
            log.info('orderset %s already recorded', orderset['id'])
            return 0
        previous = conn.execute('select rows from aggregatehistory_runs '
                                'order by snapshot desc limit 1').fetchone()

    found = collect(cache.client(), _locations())
    if previous and len(found) < previous['rows'] * MIN_FRACTION:
        raise RuntimeError(f'only {len(found)} fresh aggregates for orderset {orderset["id"]} '
                           f'(previous snapshot had {previous["rows"]}); not recording')
    if not found:
        raise RuntimeError(f'no fresh aggregates for orderset {orderset["id"]}')

    with db.pool().connection() as conn, conn.transaction():
        ensure_partition(conn, when)
        ensure_partition(conn, _next_month(_month_start(when)))
        with conn.cursor().copy(f'copy aggregatehistory ({",".join(_COLUMNS)}) from stdin') as copy:
            for row in rows_for(when, found):
                copy.write_row(row)
        conn.execute('insert into aggregatehistory_runs (snapshot,"orderSet",rows) '
                     'values (%(s)s,%(id)s,%(rows)s)', {'s': when, 'id': orderset['id'], 'rows': len(found)})
    log.info('recorded %s aggregates for orderset %s (%s)', len(found), orderset['id'], when)
    return len(found)


def _partitions(conn):
    rows = conn.execute("select c.relname from pg_inherits i join pg_class c on c.oid=i.inhrelid "
                        "where i.inhparent='aggregatehistory'::regclass order by c.relname").fetchall()
    for row in rows:
        stamp = row['relname'].rsplit('_', 1)[1]
        yield row['relname'], datetime.datetime(int(stamp[:4]), int(stamp[4:]), 1)


def thin(now=None):
    """Thin every month that ended more than a year ago to the first snapshot of
    each UTC day. Already-thinned months are skipped. Returns the months thinned."""
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    thinned = []
    with db.pool().connection() as conn:
        ensure_schema(conn)
        for name, month in list(_partitions(conn)):
            lo, hi = month, _next_month(month)
            if hi > now - KEEP_FULL:
                continue
            params = {'lo': lo, 'hi': hi}
            keep = [row['min'] for row in conn.execute(
                'select min(snapshot) from aggregatehistory_runs where snapshot>=%(lo)s and snapshot<%(hi)s '
                'group by snapshot::date', params).fetchall()]
            total = conn.execute('select count(*) from aggregatehistory_runs '
                                 'where snapshot>=%(lo)s and snapshot<%(hi)s', params).fetchone()['count']
            if total <= len(keep):
                continue
            params['keep'] = keep
            # Build the thinned copy and its index first, so the swap itself is quick.
            conn.execute(f'drop table if exists {name}_thin')
            conn.execute(f'create table {name}_thin (like aggregatehistory)')
            conn.execute(f'insert into {name}_thin select * from {name} where snapshot = any(%(keep)s::timestamp[])', params)
            conn.execute(f'alter table {name}_thin add primary key ("typeID", location, snapshot)')
            # A matching CHECK lets ATTACH skip scanning the partition.
            conn.execute(f"alter table {name}_thin add constraint {name}_bounds "
                         f"check (snapshot >= '{lo:%Y-%m-%d}' and snapshot < '{hi:%Y-%m-%d}')")
            with conn.transaction():
                conn.execute(f'alter table aggregatehistory detach partition {name}')
                conn.execute(f'drop table {name}')
                conn.execute(f'alter table {name}_thin rename to {name}')
                conn.execute(f'alter table aggregatehistory attach partition {name} '
                             f'for values {_bounds(month)}')
                conn.execute(f'alter table {name} drop constraint {name}_bounds')
                conn.execute('delete from aggregatehistory_runs where snapshot>=%(lo)s and snapshot<%(hi)s '
                             'and not snapshot = any(%(keep)s::timestamp[])', params)
            log.info('thinned %s: kept %s of %s snapshots', name, len(keep), total)
            thinned.append(name)
    return thinned


HISTORY_SQL = '''
        SELECT snapshot,buypercentile,buymax,buyvolume,buyorders,sellpercentile,sellmin,sellvolume,sellorders
        FROM aggregatehistory
        WHERE "typeID"=%(typeid)s AND location=%(location)s
        order by snapshot
'''


def series(location, typeid):
    return db.fetch_all(HISTORY_SQL, {'location': location, 'typeid': typeid})


def hub_locations():
    """The snapshotted hubs as chart locations, Jita first."""
    rows = db.fetch_all('select h.stationid,s."stationName" from hubstations h '
                        'join evesde."staStations" s on s."stationID"=h.stationid '
                        'order by h.stationid<>60003760, s."stationName"')
    return [{'id': row['stationid'], 'name': row['stationName']} for row in rows]


def chart_locations(location, name=''):
    """Chart locations for an order page, or [] where no history is kept."""
    if location == 0 or 10000000 <= location < 11000000:
        return [{'id': location, 'name': name}]
    return [hub for hub in hub_locations() if hub['id'] == location]

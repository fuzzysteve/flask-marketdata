"""Price history: snapshots of the loaders' Redis aggregates for New Eden (0),
every region and the trade hubs, stored in aggregatehistory.

Snapshots run four times a day (systemd timer, see systemd/). After a year a
month is thinned to the first snapshot of each UTC day. The table is
partitioned by month, so thinning rebuilds one partition and swaps it in,
which returns the space without a VACUUM FULL on the live table."""
import collections
import datetime
import gzip
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


def _timestamp(text):
    # Timestamps arrive as text (db.py) with trailing zeros of the fraction
    # dropped ('...:00.64108'), which fromisoformat() rejects before Python 3.11.
    return datetime.datetime.strptime(text[:19], '%Y-%m-%d %H:%M:%S')


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
    # In primary-key order ("typeID", location): each snapshot then walks the
    # index once instead of hopping around it, which matters with small shared_buffers.
    empty = (None,) * 5
    for location, typeid in sorted(found, key=lambda key: (key[1], key[0])):
        sides = found[(location, typeid)]
        bpct, bmax, _, bvol, borders = sides.get('true', empty)
        spct, _, smin, svol, sorders = sides.get('false', empty)
        yield (snapshot, location, typeid, bpct, bmax, bvol, spct, smin, svol, borders, sorders)


def snapshot():
    """Record the current aggregates once per orderset. Returns rows written."""
    orderset = db.fetch_one('select id, downloaded from orderset order by id desc limit 1')
    when = _timestamp(orderset['downloaded'])
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

    with db.pool().connection() as conn:
        ensure_partition(conn, _next_month(_month_start(when)))
    record(when, orderset['id'], copy_text(when, found), len(found))
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


# Backfill from the order book dumps (/opt/orderbooks/orderset-<id>.csv.gz),
# recomputing what aggloader-esi.py would have put in Redis for that orderset.

def read_orderbook(path):
    """Yield (typeID, buy, price, volume, stationID, region) from an order book
    dump, which has the columns of the loader's COPY into orders."""
    with gzip.open(path, 'rt') as f:
        for line in f:
            fields = line.rstrip('\n').split('\t')
            yield (int(fields[1]), fields[3] == 'True', float(fields[7]), int(fields[4]),
                   int(fields[8]), int(fields[11]))


def aggregate_levels(levels, buy):
    """(percentile, max, min, volume, orderCount) for one location/type/side, the
    way aggloader-esi.py computes it: over price levels (volume summed per price),
    ignoring volume more than 100x off the best price, with the percentile the
    volume-weighted price of the best 5% of that volume."""
    prices = sorted(levels, reverse=buy)
    best = prices[0]
    volumes = [levels[p] if (p >= best / 100 if buy else p <= best * 100) else 0 for p in prices]
    total = sum(volumes)
    five = total / 20
    applies, cumsum, last = [], 0, 0
    for volume in volumes:
        cumsum += volume
        applies.append(max(volume if cumsum <= five else five - last, 0))
        last = cumsum
    if sum(applies) == 0:
        applies = [0.01] * len(prices)
    percentile = sum(p * a for p, a in zip(prices, applies)) / sum(applies)
    return percentile, max(prices), min(prices), float(total), len(prices)


def aggregate_orderbook(orders, locations):
    """{(location, typeID): {'true': fields, 'false': fields}}, the shape collect()
    returns, for New Eden (0), regions and stations in `locations`."""
    levels = {}
    for typeid, buy, price, volume, station, region in orders:
        for location in (0, region, station):
            if location in locations:
                side = levels.setdefault((location, typeid, buy), {})
                side[price] = side.get(price, 0) + volume
    found = {}
    for (location, typeid, buy), side in levels.items():
        found.setdefault((location, typeid), {})['true' if buy else 'false'] = aggregate_levels(side, buy)
    return found


def _copy_field(value):
    # repr() round-trips a float exactly, so float8 gets the same value.
    return '\\N' if value is None else repr(value) if isinstance(value, float) else str(value)


def copy_text(when, found):
    """The snapshot as COPY text. Built in the backfill workers, so the writer
    only streams bytes rather than converting ~290k rows itself."""
    return ''.join('\t'.join(map(_copy_field, row)) + '\n' for row in rows_for(when, found)).encode()


def record(when, orderset_id, payload, rows):
    """Write one snapshot (rows and its run) in a transaction."""
    with db.pool().connection() as conn, conn.transaction():
        ensure_partition(conn, when)
        with conn.cursor().copy(f'copy aggregatehistory ({",".join(_COLUMNS)}) from stdin') as copy:
            copy.write(payload)
        conn.execute('insert into aggregatehistory_runs (snapshot,"orderSet",rows) '
                     'values (%(s)s,%(id)s,%(rows)s)', {'s': when, 'id': orderset_id, 'rows': rows})


SNAPSHOT_HOURS = (0, 6, 12, 18)
# Loads start at :06 and :36, so the orderset for an 00:06 slot is normally
# exactly there. If that load (or its dump) is missing, the :36 either side will do.
SLOT_TOLERANCE = datetime.timedelta(minutes=40)


def backfill_plan(ordersets, recorded, start, end):
    """[(when, orderset id)] picking, for each 00/06/12/18:06 slot in [start, end),
    the orderset downloaded nearest to it, skipping slots already recorded."""
    plan = []
    day = datetime.datetime(start.year, start.month, start.day)
    while day < end:
        for hour in SNAPSHOT_HOURS:
            slot = day + datetime.timedelta(hours=hour, minutes=6)
            if not start <= slot < end:
                continue
            nearest = min(ordersets, key=lambda o: abs(o[1] - slot), default=None)
            if nearest and abs(nearest[1] - slot) <= SLOT_TOLERANCE and nearest[0] not in recorded:
                plan.append((nearest[1], nearest[0]))
        day += datetime.timedelta(days=1)
    return plan


def _aggregate_dump(path, when, locations):
    found = aggregate_orderbook(read_orderbook(path), locations)
    return copy_text(when, found), len(found)


def backfill(days=30, workers=4, orderbook_dir=None):
    """Fill the last `days` days from the order book dumps, skipping ordersets
    already recorded (live or by an earlier, possibly interrupted, backfill).
    Redis and the orders tables are only read, never written."""
    import concurrent.futures
    import os
    from . import ORDERBOOK_DIR
    orderbook_dir = orderbook_dir or ORDERBOOK_DIR
    with db.pool().connection() as conn:
        ensure_schema(conn)
        recorded = {row['orderSet'] for row in conn.execute('select "orderSet" from aggregatehistory_runs')}
    end = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    start = end - datetime.timedelta(days=days)
    ordersets = [(row['id'], _timestamp(row['downloaded']))
                 for row in db.fetch_all('select id, downloaded from orderset where downloaded>=%(lo)s and downloaded<%(hi)s',
                                         {'lo': start - SLOT_TOLERANCE, 'hi': end + SLOT_TOLERANCE})]
    ordersets = [(orderset_id, when) for orderset_id, when in ordersets
                 if os.path.exists(f'{orderbook_dir}/orderset-{orderset_id}.csv.gz')]
    plan = backfill_plan(ordersets, recorded, start, end)
    log.info('backfilling %s snapshots from %s to %s', len(plan), start, end)
    locations = {int(location) for location in _locations()}
    # Keep only a few results in flight: each is ~25 MB, and an unbounded queue
    # grows whenever the readers outpace the writer.
    pending = collections.deque()

    def write_oldest():
        when, orderset_id, future = pending.popleft()
        payload, rows = future.result()
        record(when, orderset_id, payload, rows)
        log.info('backfilled orderset %s (%s): %s aggregates', orderset_id, when, rows)

    with concurrent.futures.ProcessPoolExecutor(workers) as pool:
        for when, orderset_id in plan:
            pending.append((when, orderset_id, pool.submit(
                _aggregate_dump, f'{orderbook_dir}/orderset-{orderset_id}.csv.gz', when, locations)))
            if len(pending) > workers:
                write_oldest()
        while pending:
            write_oldest()
    return len(plan)

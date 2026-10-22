import contextlib
import datetime

import pytest

from app import db, history


def test_parse_aggregate():
    value = '3.02|4.0|0.01|1.02|3.11|13349923625.0|5|3.79'
    assert history.parse_aggregate(value) == (3.79, 4.0, 0.01, 13349923625.0, 5)
    assert history.parse_aggregate('') is None
    assert history.parse_aggregate('1|2|3') == (None, 2.0, 3.0, None, None)


class _FakePipeline:
    def __init__(self, r):
        self.r, self.calls = r, []

    def ttl(self, key):
        self.calls.append(self.r.ttls[key])

    def get(self, key):
        self.calls.append(self.r.values[key])

    def execute(self):
        return self.calls


class _FakeRedis:
    def __init__(self, entries):
        self.values = {k: v for k, (v, _) in entries.items()}
        self.ttls = {k: t for k, (_, t) in entries.items()}

    def scan_iter(self, match, count):
        return iter(self.values)

    def pipeline(self, transaction):
        return _FakePipeline(self)


def test_collect_keeps_only_fresh_keys_for_wanted_locations():
    r = _FakeRedis({
        '10000002|34|true': ('1|5|1|0|1|100|2|4.5', 5000),
        '10000002|34|false': ('1|9|6|0|1|200|3|6.5', 5000),
        '10000002|35|false': ('1|9|6|0|1|200|3|6.5', 1200),   # left over from an older load
        '30000142|34|false': ('1|9|6|0|1|200|3|6.5', 5000),   # a system: not snapshotted
        '0|36|false': ('1|9|6|0|1|200|3|6.5', 5000),
    })
    found = history.collect(r, {'0', '10000002'})
    assert set(found) == {(10000002, 34), (0, 36)}
    when = datetime.datetime(2026, 10, 22, 6, 6)
    rows = {row[1:3]: row for row in history.rows_for(when, found)}
    assert rows[(10000002, 34)] == (when, 10000002, 34, 4.5, 5.0, 100.0, 6.5, 6.0, 200.0, 2, 3)
    assert rows[(0, 36)][3:6] == (None, None, None)


def _live_db():
    try:
        db.fetch_one('select 1')
        return True
    except Exception:
        return False


@pytest.fixture
def scratch_schema(monkeypatch):
    """history's tables in a throwaway schema, dropped afterwards."""
    with db.pool().connection() as conn:
        conn.execute('drop schema if exists history_test cascade')
        conn.execute('create schema history_test')
        conn.execute('set search_path to history_test, public')

        class _Pool:
            @contextlib.contextmanager
            def connection(self):
                yield conn
        monkeypatch.setattr(db, 'pool', lambda: _Pool())
        try:
            yield conn
        finally:
            conn.execute('reset search_path')
            conn.execute('drop schema history_test cascade')


@pytest.mark.skipif(not _live_db(), reason='needs local Postgres')
def test_thin_keeps_first_snapshot_per_day(scratch_schema):
    conn = scratch_schema
    history.ensure_schema(conn)
    old = [datetime.datetime(2025, 3, day, hour, 6) for day in (1, 2) for hour in (0, 6, 12, 18)]
    recent = [datetime.datetime(2026, 10, 21, hour, 6) for hour in (0, 6)]
    for when in old + recent:
        history.ensure_partition(conn, when)
        conn.execute('insert into aggregatehistory_runs values (%(s)s, 1, 2)', {'s': when})
        for typeid in (34, 35):
            conn.execute('insert into aggregatehistory (snapshot, location, "typeID", sellpercentile) '
                         'values (%(s)s, 0, %(t)s, 4.0)', {'s': when, 't': typeid})

    assert history.thin(now=datetime.datetime(2026, 10, 22)) == ['aggregatehistory_202503']

    kept = [row['snapshot'] for row in conn.execute(
        'select distinct snapshot from aggregatehistory_202503 order by 1').fetchall()]
    assert kept == ['2025-03-01 00:06:00', '2025-03-02 00:06:00']
    assert conn.execute('select count(*) from aggregatehistory').fetchone()['count'] == 8
    assert conn.execute('select count(*) from aggregatehistory_runs').fetchone()['count'] == 4
    # Still attached, with its primary key, and a second run has nothing to do.
    assert conn.execute("select count(*) from pg_inherits where inhrelid='aggregatehistory_202503'::regclass"
                        ).fetchone()['count'] == 1
    assert conn.execute("select count(*) from pg_indexes where tablename='aggregatehistory_202503'"
                        ).fetchone()['count'] == 1
    assert history.thin(now=datetime.datetime(2026, 10, 22)) == []


def test_aggregate_levels_matches_loader_maths():
    # Sell: 100 units at 10, 900 at 11, and an outlier >100x the minimum whose
    # volume is ignored. 5% of 1000 = 50 units, all at 10.
    assert history.aggregate_levels({10.0: 100, 11.0: 900, 2000.0: 5}, buy=False) == (10.0, 2000.0, 10.0, 1000.0, 3)
    # Buy: best first. 5% of 400 = 20: 10 at 9.0 then 10 of the 390 at 8.0.
    pct, high, low, volume, orders = history.aggregate_levels({9.0: 10, 8.0: 390}, buy=True)
    assert (pct, high, low, volume, orders) == (8.5, 9.0, 8.0, 400.0, 2)


def test_aggregate_orderbook_groups_new_eden_regions_and_hubs():
    orders = [(34, False, 5.0, 10, 60003760, 10000002), (34, False, 5.0, 5, 60003760, 10000002),
              (34, False, 6.0, 1, 60000001, 10000002), (34, True, 4.0, 7, 60008494, 10000043)]
    found = history.aggregate_orderbook(orders, {0, 10000002, 10000043, 60003760, 60008494})
    assert found[(60003760, 34)]['false'][3:] == (15.0, 1)      # two orders, one price level
    assert found[(10000002, 34)]['false'][3:] == (16.0, 2)
    assert found[(0, 34)]['true'][1] == 4.0 and found[(0, 34)]['false'][2] == 5.0
    assert (60000001, 34) not in found


def test_backfill_plan_picks_nearest_orderset_per_slot():
    t = lambda h, m: datetime.datetime(2026, 10, 1, h, m)
    ordersets = [(1, t(0, 6)), (2, t(0, 36)), (3, t(6, 50)), (4, t(12, 36)), (5, t(18, 6))]
    plan = [o for _, o in history.backfill_plan(
        ordersets, recorded={5}, start=t(0, 0), end=datetime.datetime(2026, 10, 2))]
    # 06:06 has nothing within 40 minutes (06:50 is 44 away); 12:06 falls back to the
    # 12:36 load; 18:06 is already recorded.
    assert plan == [1, 4]


def test_timestamp_accepts_postgres_text():
    assert history._timestamp('2026-09-23 00:06:00.64108') == datetime.datetime(2026, 9, 23, 0, 6)
    assert history._timestamp('2026-09-23 00:06:00') == datetime.datetime(2026, 9, 23, 0, 6)


@pytest.mark.skipif(not _live_db(), reason='needs local Postgres')
def test_record_copy_text_round_trips(scratch_schema):
    conn = scratch_schema
    history.ensure_schema(conn)
    when = datetime.datetime(2026, 7, 1, 0, 6, 1)
    found = {(60003760, 34): {'true': (3.7744913663901, 3.83, 0.01, 1e10, 12), 'false': (4.1, 999.0, 3.94, 5.0, 3)},
             (0, 35): {'false': (0.1 + 0.2, 7.0, 7.0, 1.0, 1)}}
    history.record(when, 99, history.copy_text(when, found), len(found))
    rows = {(r['location'], r['typeID']): r for r in conn.execute('select * from aggregatehistory').fetchall()}
    jita = rows[(60003760, 34)]
    assert float(jita['buypercentile']) == 3.7744913663901 and jita['buyorders'] == 12
    assert float(rows[(0, 35)]['sellpercentile']) == 0.1 + 0.2
    assert rows[(0, 35)]['buypercentile'] is None and rows[(0, 35)]['buyorders'] is None
    assert conn.execute('select "orderSet", rows from aggregatehistory_runs').fetchone() == {'orderSet': 99, 'rows': 2}

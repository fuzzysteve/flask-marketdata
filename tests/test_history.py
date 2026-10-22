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

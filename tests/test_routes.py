import json

import pytest
import redis

from app import cache, db, orders


@pytest.fixture
def fake_appraisal(monkeypatch):
    monkeypatch.setattr(cache, 'cached_lookup', lambda key, build: {
        'sde:typenamelookup': {'34': {'typeName': 'Tritanium', 'volume': '0.01'}},
        'sde:typeidlookup': {'tritanium': 34},
    }[key])

    def fetch_one(sql, params=None):
        if 'mapRegions' in sql:
            return {'regionName': 'The Forge', 'regionID': 10000002} if params['region'] == 10000002 else None
        if 'from appraisal' in sql:
            return {'list': {'34': '100'}} if params['id'] == 'abc' else None
    monkeypatch.setattr(db, 'fetch_one', fetch_one)
    monkeypatch.setattr(cache, 'mget_chunked', lambda keys: [
        '3.0|4.0|0.01|1|3.2|100|5|3.5', '4.6|300|3.21|1|4.9|100|5|3.9'])


def test_appraisal_json(client, fake_appraisal):
    r = client.get('/appraisal/10000002/abc/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert list(data) == ['region', 'identifier', 'json', 'regionid', 'appraisal', 'total']
    line = data['appraisal'][0]
    assert line['typename'] == 'Tritanium' and line['quantity'] == '100'
    assert line['pbuy'] == 350 and line['msell'] == 321 and line['volume'] == 1
    assert data['total']['psell'] == 390


def test_appraisal_html_and_404s(client, fake_appraisal):
    assert b'Tritanium' in client.get('/appraisal/10000002/abc').data
    assert b'Tritanium' in client.get('/appraisal/10000002/abc/0').data  # "0" is empty in PHP
    assert client.get('/appraisal/10000001/abc').status_code == 404
    assert client.get('/appraisal/10000002/nope').status_code == 404


def test_libraries_only_where_used(client, fake_appraisal):
    page = client.get('/appraisal/10000002/abc').data
    assert b'jquery-3.7.1' in page and b'jquery.dataTables' in page
    for path in ('/about/', '/type/', '/hub/', '/region/', '/station/'):
        page = client.get(path).data
        assert b'jquery' not in page and b'dataTables' not in page, path
    assert b'/js/autocomplete.js' in page


def test_appraisal_post_refused_when_writes_disabled(app, fake_appraisal, monkeypatch):
    app.config['ALLOW_WRITES'] = False
    monkeypatch.setattr(db, 'execute', lambda *a, **k: pytest.fail('must not write'))
    r = app.test_client().post('/appraisal/', data={'region': '10000002', 'pasteblock': 'Tritanium\t5'})
    assert r.status_code == 503


def test_appraisal_post_writes_when_enabled(app, fake_appraisal, monkeypatch):
    app.config['ALLOW_WRITES'] = True
    written = []
    monkeypatch.setattr(db, 'execute', lambda sql, params: written.append(params))
    r = app.test_client().post('/appraisal/', data={'region': '10000002', 'pasteblock': 'Tritanium\t5'})
    assert r.status_code == 302 and r.headers['Location'].startswith('/appraisal/10000002/')
    assert json.loads(written[0]['list']) == {'34': 5}


def test_appraisal_post_bad_input(client, fake_appraisal):
    assert client.post('/appraisal/', data={'region': 'x', 'pasteblock': 'a'}).data == b''
    assert client.post('/appraisal/', data={'region': '1'}).data == b''


def test_static_files_do_not_expose_php(client):
    assert client.get('/css/main.css').status_code == 200
    assert client.get('/index.php').status_code == 404
    assert client.get('/index.php.bak-20261019').status_code == 404
    for icon in ('/favicon.ico', '/favicon.svg', '/apple-touch-icon.png'):
        assert client.get(icon).status_code == 200


def test_aggregate_csv_is_not_content_encoded(client, tmp_path, monkeypatch):
    from app.routes import static
    (tmp_path / 'aggregatecsv.csv.gz').write_bytes(b'\x1f\x8b')
    monkeypatch.setattr(static, 'PUBLIC_DIR', str(tmp_path))
    r = client.get('/aggregatecsv.csv.gz')
    assert r.status_code == 200 and 'Content-Encoding' not in r.headers
    assert r.headers['Content-Type'] == 'application/octet-stream'


def test_api_aggregates_alias(client):
    r = client.get('/api/aggregates/?region=10000002&types=34')
    assert r.status_code == 308
    assert r.headers['Location'] == '/aggregates/?region=10000002&types=34'
    assert client.post('/api/aggregates/').headers['Location'] == '/aggregates/'


def _live_backends_available():
    try:
        cache.client().ping()
        db.fetch_one('select 1')
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _live_backends_available(), reason='needs local Postgres and Redis')
@pytest.mark.parametrize('path', ['/', '/plex/', '/browser/', '/api/', '/type/34/',
                                  '/aggregates/?region=10000002&types=34', '/api/orderset',
                                  '/api/marketgroup/0/', '/api/region/10000002/type/34/'])
def test_live_routes(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers['Cache-Control'] == 'public, max-age=300'


class _FakeRedis:
    def __init__(self, fail=False):
        self.data, self.fail = {}, fail

    def get(self, key):
        if self.fail:
            raise redis.ConnectionError('down')
        return self.data.get(key)

    def setex(self, key, ttl, value):
        if self.fail:
            raise redis.ConnectionError('down')
        self.data[key] = str(value)


def test_latest_orderset_cached(monkeypatch):
    fake, queries = _FakeRedis(), []
    monkeypatch.setattr(cache, 'client', lambda: fake)
    monkeypatch.setattr(db, 'fetch_one', lambda sql, params=None: queries.append(sql) or {'max': 175493})
    assert orders.latest_orderset() == 175493
    assert orders.latest_orderset() == 175493
    assert len(queries) == 1 and fake.data == {'py:latest_orderset': '175493'}


def test_latest_orderset_without_redis(monkeypatch):
    monkeypatch.setattr(cache, 'client', lambda: _FakeRedis(fail=True))
    monkeypatch.setattr(db, 'fetch_one', lambda sql, params=None: {'max': 175493})
    assert orders.latest_orderset() == 175493


def test_price_history_api(client, monkeypatch):
    from app import history
    monkeypatch.setattr(history, 'series', lambda location, typeid: [
        {'snapshot': '2026-10-22 12:06:00', 'buypercentile': '4.5', 'buymax': '5', 'buyvolume': '100',
         'buyorders': 2, 'sellpercentile': None, 'sellmin': None, 'sellvolume': None, 'sellorders': None}])
    r = client.get('/api/history/10000002/34/')
    assert r.status_code == 200 and r.headers['Access-Control-Allow-Origin'] == '*'
    data = json.loads(r.data)
    assert data['location'] == 10000002 and data['typeid'] == 34
    assert data['history'][0]['buypercentile'] == '4.5'


def test_bulk_price_history_api(client, monkeypatch):
    import datetime
    from app import history
    calls = []

    def bulk_series(location, typeids, start, end):
        calls.append((location, typeids, start, end))
        return {typeid: [] for typeid in typeids}
    monkeypatch.setattr(history, 'bulk_series', bulk_series)

    r = client.get('/api/history/60003760/?types=35,34,,35&from=2026-10-01&to=2026-10-31')
    assert r.status_code == 200 and r.headers['Access-Control-Allow-Origin'] == '*'
    data = json.loads(r.data)
    assert data == {'location': 60003760, 'from': '2026-10-01', 'to': '2026-10-31',
                    'history': {'35': [], '34': []}}
    assert calls[-1] == (60003760, [35, 34], datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))

    # Without from, the last 90 days up to to.
    data = json.loads(client.get('/api/history/0/?types=34&to=2026-10-31').data)
    assert data['from'] == '2026-08-03'

    for query in ('', 'types=34,x', 'types=34&from=2026-13-01', 'types=34&from=2026-10-02&to=2026-10-01',
                  'types=' + ','.join(str(t) for t in range(101)),
                  'types=' + ','.join(str(t) for t in range(100)) + '&from=2026-01-01&to=2026-04-11'):
        r = client.get(f'/api/history/0/?{query}')
        assert r.status_code == 400 and 'error' in json.loads(r.data), query
    # 100 types x 100 days is allowed.
    r = client.get('/api/history/0/?types=' + ','.join(str(t) for t in range(100))
                   + '&from=2026-01-01&to=2026-04-10')
    assert r.status_code == 200


def test_bulk_series_groups_rows_by_type(monkeypatch):
    import datetime
    from app import history
    seen = {}

    def fetch_all(sql, params=None):
        seen.update(params)
        return [{'typeID': 34, 'snapshot': 'a'}, {'typeID': 34, 'snapshot': 'b'}, {'typeID': 36, 'snapshot': 'c'}]
    monkeypatch.setattr(db, 'fetch_all', fetch_all)
    result = history.bulk_series(0, [36, 35, 34], datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
    assert list(result) == [36, 35, 34]
    assert result == {36: [{'snapshot': 'c'}], 35: [], 34: [{'snapshot': 'a'}, {'snapshot': 'b'}]}
    # The end day is inclusive.
    assert seen['end'] == datetime.date(2026, 11, 1)


def test_price_history_at_api(client, monkeypatch):
    import datetime
    from app import history
    calls = []

    def prices_at(location, typeids, at):
        calls.append((location, typeids, at))
        return {typeid: {'snapshot': '2026-11-01 18:06:00'} if typeid == 34 else None for typeid in typeids}
    monkeypatch.setattr(history, 'prices_at', prices_at)

    r = client.get('/api/history/60003760/at/2026-11-01/?types=34,35')
    assert r.status_code == 200 and r.headers['Access-Control-Allow-Origin'] == '*'
    assert json.loads(r.data) == {'location': 60003760, 'at': '2026-11-01',
                                  'prices': {'34': {'snapshot': '2026-11-01 18:06:00'}, '35': None}}
    # A bare date means the end of that day.
    assert calls[-1][2] == datetime.datetime(2026, 11, 1, 23, 59, 59, 999999)

    for when, label in (('2026-11-01T12:00', '2026-11-01 12:00:00'),
                        ('2026-11-01 12:00:30Z', '2026-11-01 12:00:30'),
                        ('2026-11-01T14:00+02:00', '2026-11-01 12:00:00')):
        assert json.loads(client.get(f'/api/history/0/at/{when}/?types=34').data)['at'] == label
        assert calls[-1][2] == datetime.datetime.fromisoformat(label)

    for url in ('/api/history/0/at/yesterday/?types=34', '/api/history/0/at/2026-11-01/',
                '/api/history/0/at/2026-11-01/?types=' + ','.join(str(t) for t in range(1001))):
        r = client.get(url)
        assert r.status_code == 400 and 'error' in json.loads(r.data), url
    assert client.get('/api/history/0/at/2026-11-01/?types='
                      + ','.join(str(t) for t in range(1000))).status_code == 200


def test_prices_at_fills_missing_types_with_none(monkeypatch):
    import datetime
    from app import history
    seen = {}

    def fetch_all(sql, params=None):
        seen.update(params)
        return [{'typeID': 34, 'snapshot': 'a'}]
    monkeypatch.setattr(db, 'fetch_all', fetch_all)
    at = datetime.datetime(2026, 11, 1, 12)
    assert history.prices_at(0, [35, 34], at) == {35: None, 34: {'snapshot': 'a'}}
    assert seen['at'] == at and seen['after'] == datetime.datetime(2026, 10, 29, 12)


def test_hubs_type_api(client, monkeypatch):
    from app.routes import api
    monkeypatch.setattr(api, '_hubs', lambda: [{'id': 60003760, 'name': 'Jita'}, {'id': 60008494, 'name': 'Amarr'}])
    monkeypatch.setattr(orders, 'type_name', lambda typeid: 'Tritanium')
    requested = []

    def mget_chunked(keys):
        requested.extend(keys)
        return ['3.0|4.0|0.01|1|3.2|100|5|3.5', '4.6|300|3.21|1|4.9|100|5|3.9', None, None]
    monkeypatch.setattr(cache, 'mget_chunked', mget_chunked)

    r = client.get('/api/hubs/type/34/')
    assert r.status_code == 200 and r.headers['Access-Control-Allow-Origin'] == '*'
    assert requested == ['60003760|34|true', '60003760|34|false', '60008494|34|true', '60008494|34|false']
    data = json.loads(r.data)
    assert data['typeid'] == 34 and data['typename'] == 'Tritanium'
    jita, amarr = data['hubs']
    assert jita['station'] == 60003760 and jita['name'] == 'Jita'
    assert jita['buy']['max'] == '4.0' and jita['sell']['min'] == '3.21' and jita['sell']['percentile'] == '3.9'
    # No orders: every field 0, as in /aggregates/.
    assert set(amarr['buy'].values()) == {0} and set(amarr['sell'].values()) == {0}


def test_api_appraisal_matches_saved_appraisal(client, fake_appraisal):
    saved = json.loads(client.get('/appraisal/10000002/abc/json').data)
    for r in (client.post('/api/appraisal/', data={'region': '10000002', 'pasteblock': 'Tritanium\t100'}),
              client.post('/api/appraisal/', json={'region': 10000002, 'pasteblock': 'Tritanium\t100'})):
        assert r.status_code == 200 and r.headers['Access-Control-Allow-Origin'] == '*'
        assert r.headers['Cache-Control'] == 'no-store'
        data = json.loads(r.data)
        assert list(data) == ['region', 'regionid', 'appraisal', 'total']
        assert data['region'] == 'The Forge' and data['total'] == saved['total']
        # The saved list holds the quantity as it was stored ('100'); the paste gives 100.
        assert data['appraisal'][0]['pbuy'] == saved['appraisal'][0]['pbuy'] == 350


def test_api_appraisal_never_writes(app, fake_appraisal, monkeypatch):
    app.config['ALLOW_WRITES'] = True
    monkeypatch.setattr(db, 'execute', lambda *a, **k: pytest.fail('wrote to the DB'))
    r = app.test_client().post('/api/appraisal/', data={'region': '10000002', 'pasteblock': 'Tritanium\t5'})
    assert r.status_code == 200


def test_api_appraisal_bad_input_and_preflight(client, fake_appraisal):
    for body in ({'pasteblock': 'Tritanium'}, {'region': 'x', 'pasteblock': 'Tritanium'},
                 {'region': '10000002'}, {'region': '10000001', 'pasteblock': 'Tritanium'}):
        r = client.post('/api/appraisal/', data=body)
        assert r.status_code == 400 and 'error' in json.loads(r.data), body
    assert client.post('/api/appraisal/', json=['x']).status_code == 400
    # Nothing recognised is an empty appraisal, not an error.
    data = json.loads(client.post('/api/appraisal/', data={'region': '10000002', 'pasteblock': 'nothing'}).data)
    assert data['appraisal'] == [] and data['total']['psell'] == 0

    r = client.options('/api/appraisal/')
    assert r.status_code == 204 and r.headers['Access-Control-Allow-Origin'] == '*'
    assert r.headers['Access-Control-Allow-Headers'] == 'Content-Type'


def test_chart_locations(monkeypatch):
    from app import history
    monkeypatch.setattr(history, 'hub_locations', lambda: [{'id': 60003760, 'name': 'Jita'}])
    assert history.chart_locations(0) == [{'id': 0, 'name': ''}]
    assert history.chart_locations(10000002) == [{'id': 10000002, 'name': ''}]
    assert history.chart_locations(60003760) == [{'id': 60003760, 'name': 'Jita'}]
    assert history.chart_locations(60000001) == []


def test_link_preview_tags(client, monkeypatch):
    from app.routes import pages
    jita = 'Jita 4 - Moon 4 - Caldari Navy Assembly Plant'
    monkeypatch.setattr(pages, 'chart_locations', lambda location: [])
    monkeypatch.setattr(orders, 'latest_orderset', lambda: 1)
    monkeypatch.setattr(orders, 'type_name', lambda typeid: 'Rifter Blueprint')
    monkeypatch.setattr(orders, 'station_name', lambda station: jita)
    monkeypatch.setattr(db, 'fetch_one', lambda sql, params=None: {'categoryID': 9})
    monkeypatch.setattr(orders, 'orders_for_type', lambda typeid, orderset, **query: (
        [{'price': '2500000.00', 'stationName': jita}], []))
    page = client.get('/station/60003760/type/691/').data.decode()
    assert f'<meta property="og:title" content="Rifter Blueprint · {jita}">' in page
    # One station's page doesn't repeat the station name.
    assert 'content="Sell from 2,500,000.00 ISK (1 order). No buy orders."' in page
    assert 'content="https://images.evetech.net/types/691/bp?size=128"' in page
    assert 'content="https://market.fuzzwork.co.uk/station/60003760/type/691/"' in page


def test_link_preview_quotes_jita_on_hub_page(client, monkeypatch):
    from app.routes import pages
    monkeypatch.setattr(pages, 'hub_locations', lambda: [])
    monkeypatch.setattr(orders, 'latest_orderset', lambda: 1)
    monkeypatch.setattr(orders, 'type_name', lambda typeid: 'Tritanium')
    monkeypatch.setattr(db, 'fetch_one', lambda sql, params=None: {'categoryID': 4})
    monkeypatch.setattr(orders, 'orders_for_type', lambda typeid, orderset, **query: (
        [{'price': '3.15', 'stationID': 60004588, 'stationName': 'Rens'},
         {'price': '3.90', 'stationID': 60003760, 'stationName': 'Jita'}],
        [{'price': '3.73', 'stationID': 60005686, 'stationName': 'Hek'}]))
    page = client.get('/hub/type/34/').data.decode()
    assert 'content="Jita 4-4: Sell from 3.90 ISK (1 order). No buy orders."' in page

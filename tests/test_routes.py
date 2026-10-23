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


def test_chart_locations(monkeypatch):
    from app import history
    monkeypatch.setattr(history, 'hub_locations', lambda: [{'id': 60003760, 'name': 'Jita'}])
    assert history.chart_locations(0) == [{'id': 0, 'name': ''}]
    assert history.chart_locations(10000002) == [{'id': 10000002, 'name': ''}]
    assert history.chart_locations(60003760) == [{'id': 60003760, 'name': 'Jita'}]
    assert history.chart_locations(60000001) == []

import json

import pytest

from app import cache, db


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


def test_oauth_state_check(client):
    assert client.get('/auth/updater?state=x&code=y').status_code == 403
    r = client.get('/authlogin')
    assert r.status_code == 302 and 'state=' in r.headers['Location']
    assert r.headers['Cache-Control'] == 'no-store'


def test_static_files_do_not_expose_php(client):
    assert client.get('/css/main.css').status_code == 200
    assert client.get('/index.php').status_code == 404
    assert client.get('/index.php.bak-20261019').status_code == 404


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

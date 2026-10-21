# Fuzzwork Market Data (Flask)

The web front end for [market.fuzzwork.co.uk](https://market.fuzzwork.co.uk): EVE Online market
order books, aggregates, an appraisal tool and a JSON API. It is a Flask application served by
gunicorn behind nginx.

This repository holds the web application only. The market data is loaded into PostgreSQL and
Redis by the separate loader scripts (see [FuzzMarket](https://github.com/fuzzysteve/FuzzMarket),
`scripts/`).

## Requirements

- Python 3.10 or later
- PostgreSQL with:
  - the `marketdata` tables (`orders`, `orderset`, `appraisal`, `hubstations`, `"marketRegion"`)
  - the EVE Static Data Export in the `evesde` schema
- Redis, populated by the loaders. The aggregate keys look like `<location>|<typeID>|<true|false>`; the home page also reads `fp-buy`, `fp-sell` and `fp-lastupdate`.
- nginx (or another reverse proxy) for TLS

## Installation

```sh
git clone https://github.com/fuzzysteve/flask-marketdata.git
cd flask-marketdata
python3 -m venv .venv          # Debian/Ubuntu: apt install python3-venv first
.venv/bin/pip install -r requirements.txt
```

## Configuration

All configuration is through environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `MARKETDATA_DB_HOST` | `localhost` | PostgreSQL host |
| `MARKETDATA_DB_NAME` | `marketdata` | Database name |
| `MARKETDATA_DB_USER` | `marketdata` | Database user |
| `MARKETDATA_DB_PASSWORD` | *(empty)* | Database password |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | Redis server |
| `PYAPP_SECRET_KEY` | random per start | Signs the session cookie used by EVE SSO login. Set this in production. |
| `PYAPP_ALLOW_WRITES` | `0` | Set to `1` to let the appraisal form save appraisals |
| `PYAPP_SECURE_COOKIES` | `0` | Set to `1` when served over HTTPS |
| `PYAPP_EVE_CLIENT_ID` / `PYAPP_EVE_CLIENT_SECRET` | — | EVE SSO application credentials |
| `PUBLIC_DIR` | `../public` | Directory containing `aggregatecsv.csv.gz` (written by the loaders) |
| `ORDERBOOK_DIR` | `/opt/orderbooks` | Order book dumps served under `/orderbooks/` |
| `HOST` / `PORT` | `127.0.0.1` / `8001` | gunicorn bind address |
| `WORKERS` | `4` | gunicorn worker processes |
| `STATSD_HOST` | *(unset)* | Send gunicorn metrics to a statsd server, e.g. `127.0.0.1:8125` for Netdata |
| `STATSD_PREFIX` | `market` | Prefix for those metrics |

Keep production values in a file readable only by the service account, for example
`/etc/market-python.env`:

```sh
PYAPP_SECRET_KEY=<output of: python3 -c 'import secrets; print(secrets.token_hex(32))'>
PYAPP_ALLOW_WRITES=1
PYAPP_SECURE_COOKIES=1
MARKETDATA_DB_PASSWORD=<password>
WORKERS=8
```

## Running

### Development

```sh
set -a; . /etc/market-python.env; set +a
.venv/bin/flask --app 'app:create_app()' run --port 8001
```

### Production (systemd)

`/etc/systemd/system/market-python.service`:

```ini
[Unit]
Description=Fuzzwork Market (Python/Flask) via gunicorn
After=network.target postgresql.service redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/web/market/pyapp
EnvironmentFile=/etc/market-python.env
ExecStart=/opt/web/market/pyapp/.venv/bin/gunicorn -c gunicorn.conf.py app:create_app()
Restart=on-failure
RestartSec=2
KillMode=mixed
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
```

The `logs/` directory must be writable by the service user:

```sh
mkdir -p logs && chown www-data:www-data logs
systemctl daemon-reload
systemctl enable --now market-python
```

### nginx

Proxy to gunicorn. Cache GET `/aggregates/` for the 300 seconds the application allows. Serve `/orderbooks/` directly.

At the `http` level (for example `/etc/nginx/conf.d/aggregates-cache-python.conf`):

```nginx
proxy_cache_path /var/cache/nginx/aggregates-python levels=1:2 keys_zone=aggregates_python:20m
                 max_size=1g inactive=10m use_temp_path=off;

map $request_uri $skip_aggregates_cache_python {
    ~^/aggregates/(\?|$)  0;
    default               1;
}
```

In the `server` block:

```nginx
location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_connect_timeout 3s;
    proxy_read_timeout 30s;
    proxy_cache aggregates_python;
    proxy_cache_key "$request_method$request_uri";
    proxy_cache_bypass $skip_aggregates_cache_python;
    proxy_no_cache $skip_aggregates_cache_python;
    add_header X-Cache $upstream_cache_status always;
}

location /orderbooks/ {
    alias /opt/orderbooks/;
    try_files $uri =404;
}
```

## Updating

```sh
git pull
.venv/bin/pip install -r requirements.txt
systemctl restart market-python
```

Use a full restart. gunicorn preloads the application, so `systemctl reload` or `kill -HUP` does
not pick up code changes.

## Tests

```sh
.venv/bin/python -m pytest
```

Tests that need a database and Redis are skipped when those are unavailable. To include them,
load the environment file first.

`scripts/compare.py` fetches the same URLs from two deployments and reports differences in
status, headers, JSON and HTML:

```sh
.venv/bin/python scripts/compare.py --php https://old.example --py http://127.0.0.1:8001
```

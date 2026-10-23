# CLAUDE.md

Flask port of the Fuzzwork Market PHP (Slim 3) site. It went live on market.fuzzwork.co.uk on
2026-10-21. Install and run instructions are in README.md.

## Layout

- `app/__init__.py`: `create_app()`, Jinja filters, default `Cache-Control`, `json_response()`
- `app/routes/`: blueprints `pages` (HTML), `api` (JSON + `/aggregates/`), `appraisal`, `static`
- `app/orders.py`: the shared order-book SQL behind the type/region/hub/station/empire/plex pages
- `app/phpcompat.py`: reproduces PHP/Twig behaviour (`number_format`, `date`, `json_encode`, numeric strings)
- `app/appraisal_parser.py`: the paste-parsing regex chain
- `app/history.py`: price history (snapshot, thinning, chart queries); `scripts/history.py` runs the jobs, `app/static/js/history.js` draws the chart
- `systemd/`: the history units, copied to `/etc/systemd/system/`
- `app/templates/*.phtml`: Jinja templates. They were Twig and are still Twig-compatible.
- `app/static/`: CSS/JS, `favicon.ico`/`.svg`, `apple-touch-icon.png`. Static files are served by Flask (only listed paths; see `routes/static.py`).
- `tests/`, plus `scripts/compare.py` for diffing two deployments

## Rules

- **Output parity with the old PHP site matters.** API clients (Google Sheets scripts, Power Query) depend on the exact JSON.
  - Numeric DB values are strings (`"4.00"`), because `app/db.py` loads numeric, float and timestamp columns as text.
  - JSON goes through `phpcompat.php_json`: escaped slashes, `\u` escapes, and `378.0` encoded as `378`.
  - `/aggregates/` returns `0` (an int) for missing fields.
  - Don't "fix" these behaviours without being asked.
- **Known PHP quirks are kept deliberately** and commented in code. For example, the `, Qty:` pattern never matches because lines are lowercased first. Ask before changing them.
- **Templates:** Jinja loops over a dict's keys, but Twig looped over its values, so pass lists where templates loop. A filter can't be followed by a subscript: write `(x|split('/'))[3]`. Autoescape is on for `.phtml`.
- **Front-end libraries** (CDN), loaded only by the pages that use them: jQuery 3.7.1 + DataTables 1.13.11 (`datatables.phtml`, included by `browser.phtml` and `displayappraisal.phtml`), DataTables Responsive 2.5.0 (`browser.phtml`), uPlot 1.6.32 (price chart, `type.phtml`, from jsDelivr: it isn't on cdnjs). `base.phtml` loads none, and no other page uses jQuery. The search boxes use `static/js/autocomplete.js` (plain JS; it replaced jQuery UI on 2026-10-23). Each tag has an `integrity` hash, so a version change needs a new hash: `curl -s <url> | openssl dgst -sha384 -binary | openssl base64 -A`. Stay on DataTables 1.x: 2.x changes the API these pages use.
- **DataTables tables:** every header row belongs inside `<thead>` and each body row needs one cell per column. A stray row between `</thead>` and `<tbody>` gets its own tbody, DataTables then manages only that row, and 1.13 raises an "Incorrect column count" alert (this was the appraisal totals row; it now uses `orderCellsTop`).
- **Analytics:** none. The old Universal Analytics tag was removed on 2026-10-22.
- **Redis:** market keys (`<location>|<typeID>|<true|false>`, `fp-*`) are written by the loaders. Treat them as read-only. Keys this app writes use the `py:` prefix (`app/cache.py`): `py:sde:*` lookups (1h) and `py:latest_orderset` (30s; a new orderset arrives every 30 minutes and old ones are kept). Order pages fall back to the DB if Redis is down.
- **DB writes:** the web app only writes in `POST /appraisal/`, and only when `PYAPP_ALLOW_WRITES=1`. The history jobs (below) are the only other writers, to `aggregatehistory*` tables they own.
- **Price history** (`aggregatehistory`, partitioned by month as `aggregatehistory_YYYYMM`, plus `aggregatehistory_runs`): snapshots of the Redis aggregates for New Eden (`0`), every region and the 5 hubs, one row per location+type with buy and sell together.
  - Only keys with more than an hour of their 90-minute TTL left are taken (written by the latest load). A snapshot with under half the previous run's rows fails instead of recording.
  - The snapshot time is the orderset's `downloaded` time. A run for an orderset that's already recorded is a no-op.
  - Thinning: once a month ended over a year ago it is rebuilt keeping the first snapshot of each UTC day and swapped in (detach/attach), which frees the space without `VACUUM FULL`.
  - Storage (measured): about 288k rows, 37 MB with the index, per snapshot. That is ~54 GB for the first year at 4/day, then ~13.5 GB per extra year once thinned.
  - Backfill: `scripts/history.py backfill --days N` recomputes past snapshots from the `/opt/orderbooks/orderset-<id>.csv.gz` dumps in Python (a port of `aggloader-esi.py`'s pandas maths, checked to match its Redis output exactly). It never touches Redis or the orders tables, skips ordersets already recorded (so it can be re-run or resumed), and falls back to the :36 load when a slot's :06 dump is missing. 2025-01-01 to 2026-10-22 was backfilled this way: 4 a day from 2026-04-25, but only one dump a day (mostly 12:06) survives before that, so earlier days have one snapshot (`--tolerance 180`). One slot, 2026-08-24, had no dump. In total: 1,201 snapshots covering every day, 42 GB. Postgres had only 128 MB `shared_buffers` then, so COPY rows go in primary-key order, and only `workers` results are kept in flight (an unbounded queue reached 17 GB).
  - `/api/history/<location>/<typeID>/` serves it. It's documented for users on the API page (`api.phtml`, "Price history"), with a sample response; keep that in step if the response or coverage changes. Charts are on the type (New Eden), PLEX, region, hub (with a hub picker) and hub-station pages, with range buttons (default 3M), log scale, a volume-on-market panel, CSV download of the visible range, and breaks at gaps over 30 hours. `history.js` treats every control as optional, because static files go live on write while template changes wait for a restart.
- **Public repository.** Never commit credentials. Configuration comes from environment variables (see README).
- **Style:** match the surrounding code. SQL stays close to the original queries, with psycopg `%(name)s` parameters.

## Commands

```sh
.venv/bin/python -m pytest                                   # DB/Redis tests skip without env
set -a; . /etc/market-python.env; set +a; .venv/bin/python -m pytest   # full run
systemctl restart market-python                              # required after code changes
journalctl -u market-python; tail logs/error.log logs/access.log
```

gunicorn uses `preload_app`, so HUP or reload does **not** load new code. Always restart.

**Browser testing:** start headless Chrome with the `chrome-devtools-mcp` skill (Chrome and Node 22 in `/opt/node22` are installed for it), and stop it afterwards. To test unreleased code, run a second instance with its own pid file, so logrotate's USR1 never targets it:

```sh
set -a; . /etc/market-python.env; set +a
STATSD_HOST= WORKERS=2 PORT=8002 .venv/bin/gunicorn -c gunicorn.conf.py --pid <scratch>/test.pid \
  --access-logfile <scratch>/test-access.log --error-logfile <scratch>/test-error.log 'app:create_app()'
```

HTML is sent with `Cache-Control: public, max-age=300`, so reload with the cache bypassed after changing a template.

## Production

- **Service:** systemd unit `market-python`. It runs as `www-data` from `/opt/web/market/pyapp` and binds `127.0.0.1:8001`. The env file is `/etc/market-python.env` (root:www-data, 640).
- **nginx:** `/etc/nginx/sites-available/default` (443 server) proxies to gunicorn. The `/aggregates/` cache is defined in `/etc/nginx/conf.d/aggregates-cache-python.conf`; about 22% of requests hit it, and most misses are distinct type lists, so normalising the key isn't worth it. `/orderbooks/` is served by nginx from `/opt/orderbooks`, and `/aggregatecsv.csv.gz` (`location =` block, from the server root `/opt/web/market/public`). Both go out as raw gzip with `application/octet-stream` and no `Content-Encoding`, as on the PHP site. The Flask routes for them do the same, for when nginx isn't in front. `location /` sets `proxy_buffer_size 32k` / `proxy_buffers 16 16k`: the cache key (the full URL) shares that buffer, and long `/aggregates/?types=...` URLs (up to ~8k) 502 with the 4k default. gunicorn's `limit_request_line = 8190` exists for the same URLs.
- **Rollback to PHP:** the PHP site in `/opt/web/market` (`src/`, `templates/`, `public/`) is kept, and its nginx block is commented out in `default`. php-fpm (8.1, on 127.0.0.1:9000) and `phpsessionclean.timer` are stopped and disabled. To switch back, run `systemctl enable --now php8.1-fpm phpsessionclean.timer`, restore `default.bak-20261021-prepython`, and reload nginx. Don't delete the PHP code or that block without asking. Netdata's php-fpm monitoring was removed as well: the `/fpm-status` block (backup at `/etc/nginx/local-status.conf.bak-20261021`) and the python.d `phpfpm` collector (`phpfpm: no`). Restore both if you want php-fpm monitored again.
- **Postgres** (14, `postgresql@14-main`): `shared_buffers = 8GB` and `effective_cache_size = 40GB` are set in `/etc/postgresql/14/main/conf.d/fuzzwork-memory.conf` (2026-10-22; they were the 128 MB / 4 GB defaults). `effective_cache_size` is a planner hint and only needs `systemctl reload postgresql@14-main`. Changing `shared_buffers` needs a Postgres restart, then `systemctl restart market-python` so the app's pool doesn't hand out dead connections. Do it between loads (they run at :06 and :36 and take ~2.5 minutes).
- **History timers:** `market-history-snapshot.timer` runs at 00:20, 06:20, 12:20 and 18:20 UTC, about 14 minutes after a load, and never in the 11:xx downtime hour. The service takes a shared `flock` on `/opt/pricegetter/loadPrices.lock` so it waits out a running loader. `market-history-thin.timer` runs daily at 03:50. Both run as `www-data` with the app's env file; check them with `systemctl list-timers market-history*` and `journalctl -u market-history-snapshot`.
- **Logs:** `/etc/logrotate.d/market-python` rotates `logs/*.log` daily and keeps 14 compressed copies, then sends gunicorn `USR1` (via `logs/gunicorn.pid`) to reopen them. `access.log` duplicates nginx's log and grows by roughly 750 MB a day before compression.
- **Monitoring (Netdata):**
  - gunicorn statsd metrics (`STATSD_HOST=127.0.0.1:8125`) are charted by `/etc/netdata/statsd.d/gunicorn.conf`.
  - Worker processes are tracked by the `gunicorn` group in `/etc/netdata/apps_groups.conf`.
  - nginx timings (`rt=`, `urt=`) and cache status (`cs=`) are parsed by the custom format in `/etc/netdata/python.d/web_log.conf`.
  - The alarm `gunicorn_log_errors` (`/etc/netdata/health.d/gunicorn.conf`) fires on gunicorn critical/error/exception log messages. 5xx responses are covered by the stock web_log `1m_internal_errors` alarm.
- **No auth:** the site has no logins or sessions. The EVE SSO routes (`/authlogin`, `/auth/updater`) were removed on 2026-10-23: they exchanged a code and discarded the token. The structure-market loader keeps its own refresh token in `/opt/pricegetter`.

# CLAUDE.md

Flask port of the Fuzzwork Market PHP (Slim 3) site. It went live on market.fuzzwork.co.uk on
2026-10-21. Install and run instructions are in README.md.

## Layout

- `app/__init__.py`: `create_app()`, Jinja filters, default `Cache-Control`, `json_response()`
- `app/routes/`: blueprints `pages` (HTML), `api` (JSON + `/aggregates/`), `appraisal`, `auth` (EVE SSO), `static`
- `app/orders.py`: the shared order-book SQL behind the type/region/hub/station/empire/plex pages
- `app/phpcompat.py`: reproduces PHP/Twig behaviour (`number_format`, `date`, `json_encode`, numeric strings)
- `app/appraisal_parser.py`: the paste-parsing regex chain
- `app/templates/*.phtml`: Jinja templates. They were Twig and are still Twig-compatible.
- `tests/`, plus `scripts/compare.py` for diffing two deployments

## Rules

- **Output parity with the old PHP site matters.** API clients (Google Sheets scripts, Power Query) depend on the exact JSON.
  - Numeric DB values are strings (`"4.00"`), because `app/db.py` loads numeric, float and timestamp columns as text.
  - JSON goes through `phpcompat.php_json`: escaped slashes, `\u` escapes, and `378.0` encoded as `378`.
  - `/aggregates/` returns `0` (an int) for missing fields.
  - Don't "fix" these behaviours without being asked.
- **Known PHP quirks are kept deliberately** and commented in code. For example, the `, Qty:` pattern never matches because lines are lowercased first. Ask before changing them.
- **Templates:** Jinja loops over a dict's keys, but Twig looped over its values, so pass lists where templates loop. A filter can't be followed by a subscript: write `(x|split('/'))[3]`. Autoescape is on for `.phtml`.
- **Redis:** market keys (`<location>|<typeID>|<true|false>`, `fp-*`) are written by the loaders. Treat them as read-only. Keys this app writes use the `py:` prefix (`app/cache.py`).
- **DB writes:** only `POST /appraisal/` writes, and only when `PYAPP_ALLOW_WRITES=1`.
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

## Production

- **Service:** systemd unit `market-python`. It runs as `www-data` from `/opt/web/market/pyapp` and binds `127.0.0.1:8001`. The env file is `/etc/market-python.env` (root:www-data, 640).
- **nginx:** `/etc/nginx/sites-available/default` (443 server) proxies to gunicorn. The `/aggregates/` cache is defined in `/etc/nginx/conf.d/aggregates-cache-python.conf`. `/orderbooks/` is served by nginx from `/opt/orderbooks`.
- **Rollback to PHP:** the PHP site in `/opt/web/market` (`src/`, `templates/`, `public/`) is kept, and its nginx block is commented out in `default`. php-fpm (8.1, on 127.0.0.1:9000) and `phpsessionclean.timer` are stopped and disabled. To switch back, run `systemctl enable --now php8.1-fpm phpsessionclean.timer`, restore `default.bak-20261021-prepython`, and reload nginx. Don't delete the PHP code or that block without asking. Netdata's php-fpm monitoring was removed as well: the `/fpm-status` block (backup at `/etc/nginx/local-status.conf.bak-20261021`) and the python.d `phpfpm` collector (`phpfpm: no`). Restore both if you want php-fpm monitored again.
- **EVE SSO credentials:** read from `PYAPP_EVE_CLIENT_ID` / `PYAPP_EVE_CLIENT_SECRET`, falling back to the PHP site's `src/secretreal.php`.

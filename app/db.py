"""Postgres access. Values are loaded as text, matching what PDO handed the PHP
site (numeric "4.00", timestamp "2026-09-30 15:07:42"), so JSON output and
template formatting stay identical."""
import os
import threading

from psycopg.conninfo import make_conninfo
from psycopg.types.string import TextLoader
from psycopg_pool import ConnectionPool

_TEXT_TYPES = ('numeric', 'float4', 'float8', 'timestamp', 'timestamptz', 'date')


class Row(dict):
    """A dict that also allows row.column. Jinja tries getattr() before
    row['column']; letting it succeed avoids an AttributeError per lookup,
    which is a measurable cost on the big order-book pages."""
    __slots__ = ()

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def row_factory(cursor):
    names = [column.name for column in cursor.description or ()]

    def make_row(values):
        return Row(zip(names, values))
    return make_row


_pool = None
_pool_pid = None
_lock = threading.Lock()


def _conninfo():
    return make_conninfo(
        host=os.environ.get('MARKETDATA_DB_HOST', 'localhost'),
        dbname=os.environ.get('MARKETDATA_DB_NAME', 'marketdata'),
        user=os.environ.get('MARKETDATA_DB_USER', 'marketdata'),
        password=os.environ.get('MARKETDATA_DB_PASSWORD', ''),
    )


def _configure(conn):
    for type_name in _TEXT_TYPES:
        conn.adapters.register_loader(type_name, TextLoader)
    conn.autocommit = True


def pool():
    # Created lazily per process, so gunicorn --preload doesn't share sockets across forks.
    global _pool, _pool_pid
    if _pool is None or _pool_pid != os.getpid():
        with _lock:
            if _pool is None or _pool_pid != os.getpid():
                _pool = ConnectionPool(_conninfo(), min_size=1, max_size=4,
                                       configure=_configure, kwargs={'row_factory': row_factory},
                                       open=True)
                _pool_pid = os.getpid()
    return _pool


def fetch_all(sql, params=None):
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql, params=None):
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql, params=None):
    with pool().connection() as conn:
        conn.execute(sql, params)

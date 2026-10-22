#!/usr/bin/env python3
"""Price history jobs, run by the systemd timers in systemd/.

    .venv/bin/python scripts/history.py snapshot   # record the current aggregates
    .venv/bin/python scripts/history.py thin       # thin months older than a year
    .venv/bin/python scripts/history.py backfill --days 30   # from /opt/orderbooks dumps
    .venv/bin/python scripts/history.py backfill --start 2026-05-01 --end 2026-06-01 --tolerance 180

Needs the same environment as the app (/etc/market-python.env)."""
import argparse
import datetime
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import history  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('job', choices=('snapshot', 'thin', 'backfill'))
    parser.add_argument('--days', type=int, default=30, help='backfill: how far back')
    parser.add_argument('--workers', type=int, default=4, help='backfill: parallel dump readers')
    parser.add_argument('--start', type=datetime.datetime.fromisoformat, help='backfill: from (UTC), instead of --days')
    parser.add_argument('--end', type=datetime.datetime.fromisoformat, help='backfill: up to (UTC, exclusive); default now')
    parser.add_argument('--tolerance', type=int, default=40,
                        help='backfill: minutes a dump may be from its 00/06/12/18:06 slot (older months kept one a day)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.job == 'snapshot':
        history.snapshot()
    elif args.job == 'thin':
        history.thin()
    else:
        history.backfill(days=args.days, workers=args.workers, start=args.start, end=args.end,
                         tolerance=datetime.timedelta(minutes=args.tolerance))


if __name__ == '__main__':
    main()

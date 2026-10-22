#!/usr/bin/env python3
"""Price history jobs, run by the systemd timers in systemd/.

    .venv/bin/python scripts/history.py snapshot   # record the current aggregates
    .venv/bin/python scripts/history.py thin       # thin months older than a year

Needs the same environment as the app (/etc/market-python.env)."""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import history  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('job', choices=('snapshot', 'thin'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.job == 'snapshot':
        history.snapshot()
    else:
        history.thin()


if __name__ == '__main__':
    main()

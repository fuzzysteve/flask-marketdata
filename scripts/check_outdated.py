#!/usr/bin/env python3
"""Report outdated packages in the venv (and known vulnerabilities in the installed versions)
to a Discord webhook. Run daily by systemd/market-outdated-check.timer.

    .venv/bin/python scripts/check_outdated.py            # post to $DISCORD_WEBHOOK_URL
    .venv/bin/python scripts/check_outdated.py --dry-run  # print the message instead

Says nothing when everything is current. Stdlib only, so it works whatever state the venv is in."""
import argparse
import importlib.metadata
import json
import logging
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Discord's Cloudflare front rejects urllib's default User-Agent.
USER_AGENT = 'fuzzwork-market-outdated-check (+https://market.fuzzwork.co.uk/)'
DISCORD_LIMIT = 2000


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def normalise(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def pinned_names():
    """{normalised name: '' for requirements.txt, ' (dev)' for requirements-dev.txt}"""
    names = {}
    for fname, label in (('requirements.txt', ''), ('requirements-dev.txt', ' (dev)')):
        try:
            with open(os.path.join(ROOT, fname)) as f:
                for line in f:
                    m = re.match(r'\s*([A-Za-z0-9_.-]+)', line)
                    if m and not line.lstrip().startswith(('#', '-')):
                        names.setdefault(normalise(m.group(1)), label)
        except FileNotFoundError:
            pass
    return names


def version_key(v):
    return tuple(int(p) if p.isdigit() else 0 for p in re.findall(r'\d+', v))


def check():
    pinned = pinned_names()
    outdated, vulnerable, failed = [], [], []
    seen = set()
    for dist in importlib.metadata.distributions():
        name = dist.metadata['Name']
        if normalise(name) in seen:
            continue
        seen.add(normalise(name))
        installed = dist.version
        try:
            latest = get_json(f'https://pypi.org/pypi/{name}/json')['info']['version']
            vulns = get_json(f'https://pypi.org/pypi/{name}/{installed}/json').get('vulnerabilities') or []
        except Exception as e:
            logging.warning('%s: %s', name, e)
            failed.append(name)
            continue
        mark = pinned.get(normalise(name), ' (dependency)')
        if version_key(latest) > version_key(installed):
            major = ' **major**' if version_key(latest)[:1] > version_key(installed)[:1] else ''
            outdated.append(f'`{name}` {installed} → {latest}{major}{mark}')
        reported = set()
        for v in vulns:
            # PyPI lists one advisory under each of its IDs (GHSA, PYSEC); report it once.
            idset = {v.get('id', '?'), *(v.get('aliases') or [])}
            if idset & reported:
                continue
            reported |= idset
            ids = ', '.join(sorted(idset))
            fixed = ', '.join(v.get('fixed_in') or []) or 'no fix yet'
            vulnerable.append(f'`{name}` {installed}{mark}: {ids} (fixed in {fixed})')
    return sorted(outdated, key=str.lower), vulnerable, failed


def message(outdated, vulnerable, failed):
    parts = []
    if vulnerable:
        parts.append('**:warning: Known vulnerabilities**\n' + '\n'.join(vulnerable))
    if outdated:
        parts.append('**Outdated packages**\n' + '\n'.join(outdated))
    if failed:
        parts.append('Could not check: ' + ', '.join(failed))
    if not parts:
        return None
    text = '**market.fuzzwork.co.uk** pyapp venv\n' + '\n\n'.join(parts)
    if len(text) > DISCORD_LIMIT:
        text = text[:DISCORD_LIMIT - 20] + '\n… (truncated)'
    return text


def post(webhook, text):
    body = json.dumps({'content': text, 'allowed_mentions': {'parse': []}}).encode()
    req = urllib.request.Request(webhook, data=body, method='POST',
                                 headers={'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    urllib.request.urlopen(req, timeout=20).close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='print the message instead of posting it')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    text = message(*check())
    if text is None:
        logging.info('everything is up to date')
    elif args.dry_run:
        print(text)
    else:
        webhook = os.environ.get('DISCORD_WEBHOOK_URL')
        if not webhook:
            sys.exit('DISCORD_WEBHOOK_URL is not set')
        post(webhook, text)
        logging.info('posted to Discord')


if __name__ == '__main__':
    main()

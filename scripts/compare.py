#!/usr/bin/env python3
"""Fetch the same paths from the live PHP site and the Python app and report
differences. JSON is compared structurally; HTML with whitespace collapsed.

    .venv/bin/python scripts/compare.py [--php URL] [--py URL] [path ...]

Market data refreshes every ~30 minutes and nginx caches PHP responses, so a
diff right after an orderset change can be a timing artefact: rerun to confirm."""
import argparse
import difflib
import json
import re
import sys

import requests

DEFAULT_PATHS = [
    '/', '/plex/', '/type/', '/hub/', '/region/', '/station/', '/aggregate/', '/about/',
    '/browser/', '/api/', '/appraisal/',
    '/type/34/', '/region/10000002/type/34/', '/empire/type/34/', '/hub/type/34/',
    '/station/60003760/type/34/', '/history/7433732415',
    '/aggregates/?region=10000002&types=34,35,36,37,38,39,40',
    '/aggregates/?station=60003760&types=34,44992,,abc',
    '/aggregates/?types=0', '/aggregates/?region=&types=34',
    '/api/orderset', '/api/typeids', '/api/regionids', '/api/stationids',
    '/api/marketgroup/0/', '/api/marketgroup/4/', '/api/region/10000002/type/34/',
    '/api/region/0/type/44992/', '/appraisal/10000002/doesnotexist', '/no/such/page',
]


def normalise_html(text):
    text = text.replace('&#039;', '&#39;')  # Twig and Jinja escape ' differently
    text = text.replace('https://market.fuzzwork.co.uk/', '/')  # pyapp uses relative links
    return re.sub(r'\s+', ' ', text).replace('> <', '><').strip()


def fetch(base, path):
    return requests.get(base + path, timeout=60, allow_redirects=False,
                        headers={'User-Agent': 'fuzzwork-market-compare'})


def compare(php_base, py_base, path):
    php, py = fetch(php_base, path), fetch(py_base, path)
    problems = []
    if php.status_code != py.status_code:
        problems.append(f'status {php.status_code} != {py.status_code}')
        return problems
    if php.status_code >= 400:
        return problems
    for header in ('Cache-Control', 'Access-Control-Allow-Origin'):
        if php.headers.get(header) != py.headers.get(header):
            problems.append(f'{header}: {php.headers.get(header)!r} != {py.headers.get(header)!r}')
    if ('Expires' in php.headers) != ('Expires' in py.headers):
        problems.append('Expires header presence differs')

    if 'json' in php.headers.get('Content-Type', ''):
        a, b = php.json(), py.json()
        if a != b:
            problems.append('JSON differs:\n' + '\n'.join(difflib.unified_diff(
                json.dumps(a, indent=1, sort_keys=True).splitlines(),
                json.dumps(b, indent=1, sort_keys=True).splitlines(), 'php', 'py', n=1, lineterm=''))[:3000])
        elif php.content != py.content:
            problems.append('JSON equal but bytes differ (key order / number formatting)')
    else:
        a, b = normalise_html(php.text), normalise_html(py.text)
        if a != b:
            diff = difflib.unified_diff(a.replace('><', '>\n<').splitlines(),
                                        b.replace('><', '>\n<').splitlines(), 'php', 'py', n=0, lineterm='')
            problems.append('HTML differs:\n' + '\n'.join(list(diff)[:40]))
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--php', default='https://market.fuzzwork.co.uk')
    parser.add_argument('--py', default='http://127.0.0.1:8001')
    parser.add_argument('paths', nargs='*')
    args = parser.parse_args()

    failures = 0
    for path in args.paths or DEFAULT_PATHS:
        problems = compare(args.php, args.py, path)
        print(('DIFF ' if problems else 'OK   ') + path)
        for problem in problems:
            print('     ' + problem.replace('\n', '\n     '))
        failures += bool(problems)
    print(f'\n{failures} of {len(args.paths or DEFAULT_PATHS)} paths differ')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())

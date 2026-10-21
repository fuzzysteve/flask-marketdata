"""Helpers that reproduce PHP/Twig behaviour the old site relied on, so the
Python port renders and serialises the same output."""
import datetime
import functools
import json
import re
from decimal import Decimal, ROUND_HALF_UP

_NUMERIC_PREFIX = re.compile(r'^[ \t\n\r\v\f]*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)')
_PHP_INT_KEY = re.compile(r'^(0|-?[1-9]\d*)$')
_PLAIN_DECIMAL = re.compile(r'^(-?)(\d+)(?:\.(\d*))?$', re.ASCII)


def php_number(value):
    """Leading-numeric conversion as PHP arithmetic does it ("100," -> 100,
    "3.5x" -> 3.5, "" / None / "abc" -> 0). Returns int or float."""
    if value is None or value is False:
        return 0
    if value is True:
        return 1
    if isinstance(value, (int, float)):
        return value
    match = _NUMERIC_PREFIX.match(str(value))
    if not match:
        return 0
    text = match.group(1)
    if '.' in text or 'e' in text or 'E' in text:
        return float(text)
    number = int(text)
    return number if -2**63 <= number < 2**63 else float(number)


def php_mul(a, b):
    """PHP `*`: int*int stays int, anything else is float."""
    a, b = php_number(a), php_number(b)
    if isinstance(a, int) and isinstance(b, int):
        return a * b
    return float(a) * float(b)


def php_truthy_or_zero(value):
    """`$x ? $x : 0` for a string from explode()."""
    return value if value not in (None, '', '0') else 0


def _group(digits, sep):
    return f'{int(digits):,}'.replace(',', sep)


# Order-book pages repeat the same volumes and prices thousands of times.
@functools.lru_cache(maxsize=65536)
def number_format(value, decimals=0, dec_point='.', thousands_sep=','):
    """Twig's number_format filter (casts to float, rounds half away from zero)."""
    # Fast path: DB text like "277185" or "4.00" needs no rounding, so lay the
    # digits out directly. Big order-book pages call this tens of thousands of times.
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str):
        match = _PLAIN_DECIMAL.match(value)
        # Past 15 digits PHP's float cast loses precision, so take the slow path.
        if match and len(match.group(3) or '') <= decimals and len(match.group(2)) <= 15:
            sign, whole, frac = match.groups()
            frac = (frac or '').ljust(decimals, '0')
            if sign and not whole.strip('0') and not frac.strip('0'):
                sign = ''
            return sign + _group(whole, thousands_sep) + (dec_point + frac if decimals > 0 else '')
    try:
        number = float(php_number(value))
    except (TypeError, ValueError):
        number = 0.0
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(repr(number)).quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    sign = '-' if rounded < 0 else ''
    whole, _, frac = f'{abs(rounded):f}'.partition('.')
    whole = f'{int(whole):,}'.replace(',', thousands_sep)
    return sign + whole + (dec_point + frac if decimals > 0 else '')


_PHP_DATE_FORMATS = {
    'Y': '%Y', 'y': '%y', 'm': '%m', 'd': '%d', 'H': '%H', 'i': '%M', 's': '%S',
    'M': '%b', 'F': '%B', 'D': '%a', 'l': '%A',
}


def date_filter(value, fmt='F j, Y H:i'):
    """Twig's date filter for the ISO timestamps the loaders store (UTC)."""
    if not value:
        dt = datetime.datetime.utcnow()
    elif isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value))
        except ValueError:
            return ''
    out = []
    for char in fmt:
        if char in _PHP_DATE_FORMATS:
            out.append(dt.strftime(_PHP_DATE_FORMATS[char]))
        elif char == 'j':
            out.append(str(dt.day))
        elif char == 'n':
            out.append(str(dt.month))
        elif char == 'G':
            out.append(str(dt.hour))
        else:
            out.append(char)
    return ''.join(out)


def split_filter(value, delimiter):
    return str(value).split(delimiter)


def _php_floats(obj):
    """json_encode drops the fraction from integral floats (378.0 -> 378)."""
    if isinstance(obj, float) and obj.is_integer() and abs(obj) < 1e15:
        return int(obj)
    if isinstance(obj, dict):
        return {k: _php_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_php_floats(v) for v in obj]
    return obj


def php_json(obj):
    """json_encode() with PHP's default flags: compact, \\u-escaped, slashes escaped."""
    encoded = json.dumps(_php_floats(obj), separators=(',', ':'), ensure_ascii=True)
    return encoded.replace('/', '\\/')


def php_array(mapping):
    """PHP arrays keyed 0..n-1 (after int-key coercion) encode as JSON lists."""
    keys = list(mapping)
    if all(_PHP_INT_KEY.match(str(k)) for k in keys) and \
            [int(k) for k in keys] == list(range(len(keys))):
        return list(mapping.values())
    return mapping


def natural_key(text):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', text)]

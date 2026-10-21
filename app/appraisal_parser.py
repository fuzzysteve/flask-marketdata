"""Turns a pasted cargo scan / asset list / contract into {typeID: quantity}.

A line-for-line port of the regex chain in src/routes.php (POST /appraisal/):
patterns are tried in order and the first one that matches decides the line,
even if its name isn't a known type."""
import re

from .phpcompat import php_number

_DIGIT_COMMA = re.compile(r'(\d),(\d)', re.ASCII)
_DIGIT_DOT = re.compile(r'(\d)\.(\d)', re.ASCII)
_SKIP = re.compile(r'blueprint copy')

# (pattern, name group, quantity group or None for 1, strip commas from quantity)
_PATTERNS = [
    (r'^(30 day pilot.*)\t(\d+)\t(.*)$', 1, 2, False),
    (r'^(.*) \((\d+) units\)$', 1, 2, False),
    (r'^(.*)\t.*\t(\d+)$', 1, 2, False),
    (r'^(\d+) (.*)$', 2, 1, False),
    # The PHP stripped commas here but then overwrote the result; keep that behaviour.
    (r'^(.*)\t([\d\.,]+)\t', 1, 2, False),
    (r'^(.*):\s+([\d.,]+)', 1, 2, True),
    (r'^(.*)\t([\d.,]+)', 1, 2, True),
    (r'^\[(.*),.*]', 1, None, False),
    # Never matches: lines are lowercased first. Ported as-is from the PHP.
    (r'^(.*), Qty: (\d+)', 1, 2, False),
    (r'^.*\t(.*)\t.*', 1, None, False),
    (r'^(.*)', 1, None, False),
]
_PATTERNS = [(re.compile(p, re.ASCII), n, q, s) for p, n, q, s in _PATTERNS]

# PHP's trim() character set.
_PHP_TRIM = ' \t\n\r\0\x0b'


def _quantity(text, strip_commas):
    if strip_commas:
        text = text.replace(',', '')
    value = php_number(text)
    return int(value) if float(value).is_integer() else value


def parse(pasteblock, typeid_lookup):
    """typeid_lookup maps lowercased type name -> typeID."""
    inventory = {}
    for entry in pasteblock.split('\n'):
        entry = _DIGIT_COMMA.sub(r'\1\2', entry).lower()
        entry = _DIGIT_DOT.sub(r'\1\2', entry).lower()
        line = entry.strip(_PHP_TRIM)
        if _SKIP.search(line):
            continue
        for pattern, name_group, qty_group, strip_commas in _PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            typeid = typeid_lookup.get(match.group(name_group))
            if typeid is not None:
                quantity = 1 if qty_group is None else _quantity(match.group(qty_group), strip_commas)
                inventory[typeid] = inventory.get(typeid, 0) + quantity
            break
    return inventory

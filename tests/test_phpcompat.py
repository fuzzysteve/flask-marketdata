from app import phpcompat


def test_number_format_matches_php():
    # Expected values produced by PHP's number_format().
    assert phpcompat.number_format('4.00', 2, '.', ',') == '4.00'
    assert phpcompat.number_format(1234567.891, 2, '.', ',') == '1,234,567.89'
    assert phpcompat.number_format(2.675, 2) == '2.68'
    assert phpcompat.number_format(1.005, 2) == '1.01'
    assert phpcompat.number_format(-0.001, 2) == '0.00'
    assert phpcompat.number_format('13841924917.0', 0) == '13,841,924,917'
    assert phpcompat.number_format(None, 2) == '0.00'
    assert phpcompat.number_format('', 0) == '0'


def test_php_number():
    assert phpcompat.php_number('100,') == 100
    assert phpcompat.php_number('3.5x') == 3.5
    assert phpcompat.php_number('abc') == 0
    assert phpcompat.php_number(None) == 0


def test_php_mul_keeps_int_when_both_ints():
    assert phpcompat.php_mul('5', '3') == 15
    assert isinstance(phpcompat.php_mul('5', '3'), int)
    assert phpcompat.php_mul('5', '3.78') == 5 * 3.78


def test_php_json_matches_json_encode():
    # json_encode(["a"=>"x/y é","f"=>378.0,"g"=>0.1+0.2]) in PHP 8
    assert phpcompat.php_json({'a': 'x/y é', 'f': 378.0, 'g': 0.1 + 0.2}) == \
        '{"a":"x\\/y \\u00e9","f":378,"g":0.30000000000000004}'


def test_php_array_sequential_keys_become_list():
    assert phpcompat.php_array({'0': 'a', '1': 'b'}) == ['a', 'b']
    assert phpcompat.php_array({'34': 'a', '0': 'b'}) == {'34': 'a', '0': 'b'}
    assert phpcompat.php_array({'00': 'a'}) == {'00': 'a'}


def test_date_filter():
    assert phpcompat.date_filter('2026-10-21T16:38:26.503424', 'Y-m-d H:i') == '2026-10-21 16:38'


def test_natural_sort():
    files = ['orderset-9.csv.gz', 'orderset-10.csv.gz', 'orderset-100.csv.gz']
    assert sorted(files, key=phpcompat.natural_key, reverse=True)[0] == 'orderset-100.csv.gz'

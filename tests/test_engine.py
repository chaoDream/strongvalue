from decimal import Decimal
import pytest
from backend.engine import takeoff, calculate, DomainError, number
from backend.reports import uppercase_money


def test_geometry_with_explicit_deduction():
    result = takeoff('slab', {'length': '10', 'width': '5', 'thickness': '.3', 'deduction': '1.25'})
    assert Decimal(result['quantity']) == Decimal('13.75')
    assert result['formula'] and result['rule_version']
    wall = takeoff('walls', {'length': '10', 'width': '5', 'thickness': '.25', 'height': '3'})
    assert Decimal(wall['quantity']) == Decimal('21.75')


@pytest.mark.parametrize('params', [
    {'length': '0', 'width': 5, 'thickness': 1},
    {'length': '-1', 'width': 5, 'thickness': 1},
    {'length': 'NaN', 'width': 5, 'thickness': 1},
    {'length': 'Infinity', 'width': 5, 'thickness': 1},
    {'length': '1', 'width': 5, 'thickness': 1, 'deduction': 6},
])
def test_invalid_geometry(params):
    with pytest.raises(DomainError):
        takeoff('slab', params)


def test_no_invented_rebar_or_invalid_walls():
    with pytest.raises(DomainError):
        takeoff('rebar', {})
    with pytest.raises(DomainError):
        takeoff('walls', {'length': 2, 'width': 2, 'thickness': 1, 'height': 3})


@pytest.mark.parametrize('value', ['1e999999999', '0.1234567890123456789', '1e-19'])
def test_precision_and_exponent_limits_are_explicit(value):
    with pytest.raises(DomainError):
        number(value)


def test_price_rounding_order_and_reproducibility():
    items = [{'quantity': '3', 'quotas': [{'code': 'MANUAL-1', 'coefficient': '1', 'resources': [{'consumption': '1', 'price': '1.005'}]}]}]
    rules = {'fees': [{'name': 'test fee', 'basis': 'subtotal', 'rate': '.1'}, {'name': 'test cumulative', 'basis': 'running', 'rate': '.03'}]}
    result = calculate(items, rules)
    assert result['lines'][0]['unit_price'] == '1.01'
    assert result['subtotal'] == '3.03'
    assert result['fees'][0]['amount'] == '0.30'
    assert result['fees'][1]['amount'] == '0.10'
    assert result['total'] == '3.43'
    assert result == calculate(items, rules)


@pytest.mark.parametrize('value,expected', [('0', '零元整'), ('10001.05', '壹万零壹元零伍分'), ('2982485.80', '贰佰玖拾捌万贰仟肆佰捌拾伍元捌角'), ('100000001', '壹亿零壹元整')])
def test_uppercase(value, expected):
    assert uppercase_money(value) == expected

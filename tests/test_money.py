from decimal import Decimal

import pytest

from rate_parity.money import MoneyError, parse_money


@pytest.mark.parametrize("text, expected", [
    ("₹ 7,906.00", "7906.00"),
    ("₹7,906", "7906"),
    ("Rs. 7906", "7906"),
    ("Rs 7906/-", "7906"),
    ("INR 7,906", "7906"),
    ("inr 7,906.5", "7906.5"),
    ("7,906", "7906"),
    ("1,23,456", "123456"),
    ("123,456", "123456"),
    ("₹ 7,906", "7906"),
    ("- ₹ 500", "500"),
    ("−₹500", "500"),
    ("0", "0"),
])
def test_parses_indian_formats(text, expected):
    assert parse_money(text) == Decimal(expected)


def test_returns_decimal():
    assert isinstance(parse_money("₹ 10.10"), Decimal)


@pytest.mark.parametrize("text", [
    None, "", "Free", "$ 100", "USD 100", "€100", "7.906,00", "7 906",
    "₹7,906 ₹6,500", "7906 / night", "1,2345", "7906.123", "₹", "Rs. 7,90,6",
])
def test_rejects_ambiguous(text):
    with pytest.raises(MoneyError):
        parse_money(text)

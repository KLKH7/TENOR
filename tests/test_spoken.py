from datetime import date

import pytest

from concentrate.core.spoken import (
    say_code,
    say_date,
    say_int,
    say_money,
    say_ordinal,
    say_thousands,
    spoken_numbers,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("three hundred twelve k", [312000]),
        ("twenty five dollars", [25]),
        ("PAY two twelve", [2, 12]),
        ("October fourteenth", [14]),
        ("the twenty first", [21]),
        ("a hundred and fifty seats", [150]),
        ("$25 a seat and 312k ARR", [25, 312000]),
        ("about forty two thousand signups", [42000]),
        ("twelve hundred", [1200]),
        ("no numbers here", []),
    ],
)
def test_spoken_numbers(text: str, expected: list[int]) -> None:
    assert spoken_numbers(text) == expected


def test_roundtrip_all_small_ints_and_ordinals() -> None:
    for n in range(1000):
        assert spoken_numbers(say_int(n)) == [n]
    for d in range(1, 32):
        assert spoken_numbers(say_ordinal(d)) == [d]


def test_renderers() -> None:
    assert say_money(25) == "twenty five dollars"
    assert say_thousands(312) == "three hundred twelve k"
    assert say_code("PAY", 212) == "PAY two twelve"
    assert say_date(date(2026, 11, 2)) == "November second"

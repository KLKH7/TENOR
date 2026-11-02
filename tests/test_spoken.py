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


def test_written_form_matches_asr_smart_formatting() -> None:
    from concentrate.core.spoken import written

    cases = {
        "So twenty five dollars a seat.": "So $25 a seat.",
        "we're at three hundred twelve k ARR": "we're at 312K ARR",
        "launches on October fourteenth.": "launches on October 14.",
        "not on the fourteenth": "not on the 14th",
        "PAY two twelve is done": "PAY 212 is done",
        "forty percent of them": "40% of them",
        "two or three times, wait a second": "two or three times, wait a second",
        "one hundred and five people": "105 people",
    }
    for spoken, want in cases.items():
        assert written(spoken) == want
        assert written(want) == want  # idempotent

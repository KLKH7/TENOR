"""Spoken-form rendering and parsing of numbers, money and dates.

Speech-to-text output spells numbers out ("three hundred twelve k", "twenty five dollars",
"PAY two twelve"). The synthetic generator uses :func:`say_int` & co. to produce such text, and
:func:`spoken_numbers` recovers the values (a small inverse text normalization) so validators and,
later, the enhancement layer can compare against normalized gold values.
"""

from __future__ import annotations

import re
from datetime import date

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
]  # fmt: skip
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORDINAL_ONES = {
    "one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
    "nine": "ninth", "twelve": "twelfth",
}  # fmt: skip
_MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
]  # fmt: skip
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def say_int(n: int) -> str:
    """Spell a non-negative integer below one million: 312 -> 'three hundred twelve'."""
    if n < 0 or n >= 1_000_000:
        raise ValueError(f"out of range: {n}")
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {say_int(rest)}" if rest else "")
    thousands, rest = divmod(n, 1000)
    return f"{say_int(thousands)} thousand" + (f" {say_int(rest)}" if rest else "")


def say_ordinal(n: int) -> str:
    """Spell an ordinal: 14 -> 'fourteenth', 21 -> 'twenty first'."""
    words = say_int(n).split()
    last = words[-1]
    if last in _ORDINAL_ONES:
        words[-1] = _ORDINAL_ONES[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return " ".join(words)


def say_money(dollars: int) -> str:
    """25 -> 'twenty five dollars'."""
    return f"{say_int(dollars)} dollar{'s' if dollars != 1 else ''}"


def say_thousands(k: int) -> str:
    """Speech-style thousands: 312 -> 'three hundred twelve k'."""
    return f"{say_int(k)} k"


def say_code(prefix: str, number: int) -> str:
    """Ticket codes as people say them: ('PAY', 212) -> 'PAY two twelve'."""
    if 100 <= number <= 999 and number % 100 >= 10:
        head, tail = divmod(number, 100)
        return f"{prefix} {_ONES[head]} {say_int(tail)}"
    return f"{prefix} {say_int(number)}"


def say_date(d: date) -> str:
    """date(2026, 10, 14) -> 'October fourteenth'."""
    return f"{_MONTHS[d.month - 1]} {say_ordinal(d.day)}"


def weekday_name(d: date) -> str:
    return _WEEKDAYS[d.weekday()]


# -- parsing (ITN) ---------------------------------------------------------------------------

_WORD_VALUES: dict[str, int] = {w: i for i, w in enumerate(_ONES)}
_WORD_VALUES.update({w: i * 10 for i, w in enumerate(_TENS) if w})
_ORDINAL_VALUES: dict[str, int] = {}
for _i in range(1, 32):
    _ORDINAL_VALUES[say_ordinal(_i).split()[-1]] = _i % 10 if _i > 20 and _i % 10 else _i
_NUMBER_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def _flush(current: int, total: int) -> int:
    return total + current


def spoken_numbers(text: str) -> list[int]:
    """Extract integers from text containing spelled-out and/or digit numbers.

    Handles 'three hundred twelve k' -> 312000, 'twenty five' -> 25, '$25' -> 25, '312k' ->
    312000, and ordinals ('fourteenth' -> 14). Adjacent unrelated numbers are split when a
    smaller-magnitude rule is violated ('two twelve' is returned as [2, 12]; callers that know a
    code follows, e.g. ticket IDs, can join them).
    """
    tokens = _NUMBER_TOKEN.findall(text.lower().replace(",", ""))
    out: list[int] = []
    total, current, active, last_small = 0, 0, False, 1000
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok.replace(".", "").isdigit():
            if active:
                out.append(total + current)
                total, current, active = 0, 0, False
            value = float(tok)
            if nxt in {"k", "thousand"}:
                value *= 1000
            out.append(int(value))
            continue
        if tok in _WORD_VALUES or tok in _ORDINAL_VALUES:
            v = _WORD_VALUES.get(tok, _ORDINAL_VALUES.get(tok, 0))
            magnitude = 10 if v >= 20 else 1
            if active and (magnitude >= last_small or (v < 10 and current % 10 != 0)):
                out.append(total + current)
                total, current = 0, 0
            current += v
            active = True
            last_small = magnitude if v >= 20 else (1 if v < 10 else 2)
            if tok in _ORDINAL_VALUES and tok not in _WORD_VALUES:
                out.append(total + current)
                total, current, active, last_small = 0, 0, False, 1000
            continue
        if tok == "hundred":  # "a hundred and fifty" starts a number on "hundred"
            current = max(current, 1) * 100
            active, last_small = True, 100
            continue
        if active and tok in {"thousand", "k"}:
            total = (total + max(current, 1)) * 1000
            current, last_small = 0, 1000
            if tok == "k" or nxt not in _WORD_VALUES:
                out.append(total)
                total, active = 0, False
            continue
        if active and tok == "and":
            continue
        if active:
            out.append(total + current)
            total, current, active, last_small = 0, 0, False, 1000
    if active:
        out.append(total + current)
    return out

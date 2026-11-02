"""String and sound similarity for names: folding, Jaro-Winkler, and a Double Metaphone subset.

ASR mishears names in two ways: it spells a name differently ("Meghan" for Megan) or it
substitutes a real word that *sounds* like the name ("Coffee" for Kofi, "Thomas" for Tomás).
Jaro-Winkler catches the first kind (most characters agree, especially at the start); Double
Metaphone catches the second (the words reduce to the same consonant skeleton).
"""

from __future__ import annotations

import unicodedata


def fold(s: str) -> str:
    """Lowercase and strip diacritics: 'Tomás' -> 'tomas', 'Nuñez' -> 'nunez'."""
    decomposed = unicodedata.normalize("NFD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def jaro(a: str, b: str) -> float:
    """Jaro similarity in [0, 1]: shared characters (within a window) minus transpositions."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(0, max(len(a), len(b)) // 2 - 1)
    a_hit = [False] * len(a)
    b_hit = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        lo, hi = max(0, i - window), min(len(b), i + window + 1)
        for j in range(lo, hi):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_seq = [c for c, hit in zip(a, a_hit, strict=True) if hit]
    b_seq = [c for c, hit in zip(b, b_hit, strict=True) if hit]
    transpositions = sum(x != y for x, y in zip(a_seq, b_seq, strict=True)) // 2
    m = float(matches)
    return (m / len(a) + m / len(b) + (m - transpositions) / m) / 3.0


def jaro_winkler(a: str, b: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity: Jaro boosted by up to 4 shared leading characters."""
    j = jaro(a, b)
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return j + prefix * prefix_weight * (1.0 - j)


_VOWELS = set("aeiouy")


def double_metaphone(word: str, max_len: int = 4) -> tuple[str, str]:
    """(primary, secondary) phonetic codes for ``word`` (a practical Double Metaphone subset).

    Implements the rules that matter for spoken names: silent initial letters (KN, GN, PN, WR,
    PS), initial vowels as 'A', soft/hard C and G, CH/SH/TH/PH/GH digraphs, doubled letters,
    J and TH ambiguity (secondary codes), and Spanish/Portuguese letters via diacritic folding.
    '0' stands for the 'th' sound and 'X' for 'sh'/'ch', as in the original algorithm.
    """
    w = fold(word)
    w = "".join(c for c in w if c.isalpha())
    if not w:
        return "", ""
    primary: list[str] = []
    secondary: list[str] = []

    def add(p: str, s: str | None = None) -> None:
        primary.append(p)
        secondary.append(p if s is None else s)

    def at(i: int) -> str:
        return w[i] if 0 <= i < len(w) else ""

    i = 0
    if w[:2] in {"gn", "kn", "pn", "wr", "ps"}:
        i = 1
    if w[0] == "x":
        add("S")
        i = 1
    elif w[0] in _VOWELS:
        add("A")
        i = 1
    elif w[:2] == "wh":
        add("A")
        i = 2
    while i < len(w) and len("".join(primary)) < max_len:
        c = w[i]
        nxt = at(i + 1)
        if c in _VOWELS:
            i += 1
            continue
        if c == "b":
            add("P")
        elif c == "c":
            if nxt == "h":
                add("X", "K")
                i += 1
            elif nxt in {"i", "e", "y"}:
                add("S")
            elif nxt == "k" or nxt == "c":
                add("K")
                i += 1
            else:
                add("K")
        elif c == "d":
            if nxt == "g" and at(i + 2) in {"i", "e", "y"}:
                add("J")
                i += 2
            else:
                add("T")
        elif c == "g":
            if nxt == "h":
                if at(i + 2) in _VOWELS or i == 0:
                    add("K")
                i += 1
            elif nxt == "n":
                add("N")
                i += 1
            elif nxt in {"i", "e", "y"}:
                add("J", "K")
            else:
                add("K")
        elif c == "h":
            if (i == 0 or at(i - 1) in _VOWELS) and nxt in _VOWELS:
                add("H")
        elif c == "j":
            add("J", "A" if i == 0 else "H")
        elif c == "p":
            if nxt == "h":
                add("F")
                i += 1
            else:
                add("P")
        elif c == "q":
            add("K")
        elif c == "s":
            if nxt == "h":
                add("X")
                i += 1
            elif nxt == "c" and at(i + 2) == "h":
                add("SK")
                i += 2
            elif nxt == "i" and at(i + 2) in {"o", "a"}:
                add("X", "S")
            else:
                add("S")
        elif c == "t":
            if nxt == "h":
                add("0", "T")
                i += 1
            elif nxt == "i" and at(i + 2) in {"o", "a"}:
                add("X")
            else:
                add("T")
        elif c == "v":
            add("F")
        elif c == "w":
            if nxt in _VOWELS:
                add("A", "F") if i == 0 else add("F")
        elif c == "x":
            add("KS")
        elif c == "z":
            add("S")
        else:  # f k l m n r
            add(c.upper())
        # Doubled consonants sound once ("Hannah", "Coffee").
        i += 1
        while i < len(w) and w[i] == c and c not in _VOWELS:
            i += 1
    return "".join(primary)[:max_len], "".join(secondary)[:max_len]


def sounds_alike(a: str, b: str) -> bool:
    """True if any Double Metaphone code of ``a`` equals any code of ``b``."""
    pa, sa = double_metaphone(a)
    pb, sb = double_metaphone(b)
    return bool(pa) and bool({pa, sa} & {pb, sb})


def name_similarity(token: str, name: str) -> float:
    """How well a heard token matches a directory name part, in [0, 1].

    Exact (diacritic-insensitive) match scores 1. Otherwise Jaro-Winkler on the folded strings,
    lifted when the two also sound alike, so "Coffee" ~ "Kofi" and "Thomas" ~ "Tomás" score
    high, while look-alikes that sound different ("Dana" vs "Dan") stay lower.
    """
    a, b = fold(token), fold(name)
    if a == b:
        return 1.0
    jw = jaro_winkler(a, b)
    if sounds_alike(a, b):
        return 0.55 + 0.45 * jw
    return 0.9 * jw

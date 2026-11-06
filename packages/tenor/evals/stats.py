"""Paired significance tests, multiple-comparison correction and sample-size planning.

Every system answers the *same* questions, so comparisons are paired: for each question we know
whether A was right and whether B was right. Paired tests use that (the noise from "this
question is just hard" cancels out), which makes them far more sensitive than checking whether
two independent confidence intervals overlap.

* ``mcnemar_exact``: does A beat B more often than chance? Only the questions where exactly one
  system is right (the *discordant* pairs) carry information; under "no difference" each is a
  fair coin flip, so the p-value is an exact binomial tail.
* ``paired_bootstrap``: a 95% CI for accuracy(A) - accuracy(B), resampling questions (not
  answers), so each resample keeps A's and B's answers to the same question together.
* ``holm``: Holm-Bonferroni step-down correction across a family of tests (all system pairs).
* ``mcnemar_sample_size`` / ``mcnemar_mde``: how many questions a paired comparison needs to
  detect a given accuracy gap (Connor 1987 normal approximation), and the reverse.

All functions are pure; randomness takes an explicit seed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

_Z = NormalDist()


def binom_two_sided_p(k: int, n: int) -> float:
    """Exact two-sided p-value of seeing ``k`` heads in ``n`` fair coin flips (doubled tail)."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    tail: float = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


@dataclass(frozen=True)
class PairedCounts:
    """2x2 table of paired outcomes for systems A and B over the same questions."""

    both: int  # both right
    a_only: int  # A right, B wrong
    b_only: int  # A wrong, B right
    neither: int

    @property
    def n(self) -> int:
        return self.both + self.a_only + self.b_only + self.neither

    @property
    def discordant(self) -> int:
        return self.a_only + self.b_only


def paired_counts(a: Sequence[bool], b: Sequence[bool]) -> PairedCounts:
    if len(a) != len(b):
        raise ValueError("paired outcomes must have equal length")
    pairs = list(zip(a, b, strict=True))
    return PairedCounts(
        both=sum(x and y for x, y in pairs),
        a_only=sum(x and not y for x, y in pairs),
        b_only=sum(y and not x for x, y in pairs),
        neither=sum(not x and not y for x, y in pairs),
    )


def mcnemar_exact(a: Sequence[bool], b: Sequence[bool]) -> float:
    """Exact (binomial) McNemar p-value for paired correct/incorrect outcomes."""
    c = paired_counts(a, b)
    return binom_two_sided_p(c.a_only, c.discordant)


def paired_bootstrap(
    a: Sequence[bool], b: Sequence[bool], seed: int, n_boot: int = 2000
) -> tuple[float, float, float]:
    """(mean difference, 2.5th, 97.5th percentile) of accuracy(A) - accuracy(B).

    Questions are resampled with replacement; each resample keeps both systems' answers to a
    question together, which is what makes it paired.
    """
    if len(a) != len(b):
        raise ValueError("paired outcomes must have equal length")
    if not a:
        return (math.nan, math.nan, math.nan)
    d = [int(x) - int(y) for x, y in zip(a, b, strict=True)]
    n = len(d)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(d, k=n)) / n for _ in range(n_boot))
    return sum(d) / n, means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (same order as the input).

    Sort p ascending; the i-th smallest (0-based) is multiplied by (m - i), then a running
    maximum keeps the adjusted values monotone. Controls the chance of *any* false positive in
    the family at alpha, and is uniformly more powerful than plain Bonferroni.
    """
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


# -- power / sample size -----------------------------------------------------------------------


def mcnemar_sample_size(
    delta: float, discordance: float, alpha: float = 0.05, power: float = 0.8
) -> int:
    """Questions needed for a paired (McNemar) test to detect an accuracy gap ``delta``.

    ``discordance`` is the share of questions where exactly one system is right (psi). It must
    be at least ``delta`` (a gap of delta needs at least that many disagreements); smaller
    values are raised to ``delta``. Connor (1987):
    n = (z_{1-a/2} sqrt(psi) + z_{power} sqrt(psi - delta^2))^2 / delta^2.
    """
    if delta <= 0:
        raise ValueError("delta must be positive")
    psi = min(1.0, max(discordance, delta))
    za, zb = _Z.inv_cdf(1 - alpha / 2), _Z.inv_cdf(power)
    return math.ceil((za * math.sqrt(psi) + zb * math.sqrt(psi - delta**2)) ** 2 / delta**2)


def mcnemar_mde(n: int, discordance: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Minimum detectable accuracy gap with ``n`` paired questions (inverse of the above)."""
    if n <= 0:
        return math.nan
    lo, hi = 1e-4, 1.0
    if mcnemar_sample_size(hi, discordance, alpha, power) > n:
        return math.nan  # not even a 100-point gap is detectable
    for _ in range(60):
        mid = (lo + hi) / 2
        if mcnemar_sample_size(mid, discordance, alpha, power) <= n:
            hi = mid
        else:
            lo = mid
    return hi


def unpaired_sample_size(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Questions *per system* for an unpaired two-proportion z-test (for contrast only)."""
    delta = abs(p1 - p2)
    if delta == 0:
        raise ValueError("p1 and p2 must differ")
    pbar = (p1 + p2) / 2
    za, zb = _Z.inv_cdf(1 - alpha / 2), _Z.inv_cdf(power)
    num = za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil(num**2 / delta**2)


def independent_discordance(p1: float, p2: float) -> float:
    """Discordance if the two systems' errors were independent: p1(1-p2) + p2(1-p1)."""
    return p1 * (1 - p2) + p2 * (1 - p1)

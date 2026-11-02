"""Checking the judge: re-judge a saved report with a second model, and hand-check a sample.

The primary judge is also the answer model, so it could favour answers in its own style
(self-preference) or be lenient in ways that inflate every system. Two checks make that
measurable instead of assumed:

* **Second judge.** Re-grade every saved answer with a different model, using the identical
  judge prompt, then report agreement (raw and Cohen's kappa), the confusion matrix, each
  system's accuracy under both judges, and whether the system ranking changes.
* **Hand check.** Export a blind, stratified sample (verdicts hidden) for a person to label, then
  score both judges against the human labels.
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from concentrate.evals.scoring import JudgeOutput, Verdict, judge_request_for
from concentrate.llm.client import LLMRequest

VERDICTS: tuple[Verdict, ...] = ("correct", "partially_correct", "incorrect")
SHORT_LABELS = {
    "c": "correct",
    "p": "partially_correct",
    "partial": "partially_correct",
    "i": "incorrect",
}
HAND_COLUMNS = ("item", "question", "gold", "answer", "abstained", "your_verdict")


@dataclass(frozen=True)
class SavedAnswer:
    """One (system, question) answer from a report's ``.jsonl``, with the primary verdict."""

    system: str
    qid: str
    category: str
    question: str
    gold: str
    unanswerable: bool
    answer: str
    abstained: bool
    verdict: str

    @property
    def key(self) -> str:
        return f"{self.system}/{self.qid}"


def load_report(jsonl: Path) -> list[SavedAnswer]:
    rows = []
    for line in jsonl.read_text().splitlines():
        d = json.loads(line)
        rows.append(
            SavedAnswer(
                system=d["system"],
                qid=d["qid"],
                category=d["category"],
                question=d["question"],
                gold=d["gold"],
                unanswerable=bool(d["unanswerable"]),
                answer=d["answer"]["answer"],
                abstained=bool(d["answer"]["abstain"]),
                verdict=d["verdict"],
            )
        )
    return rows


def rejudge_request(a: SavedAnswer) -> LLMRequest:
    """The same judge call the eval made, rebuilt from the saved fields."""
    return judge_request_for(a.question, a.gold, a.unanswerable, a.answer, a.abstained)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Agreement between two raters, corrected for the agreement expected by chance.

    kappa = (p_observed - p_chance) / (1 - p_chance); 1 = perfect, 0 = no better than chance.
    """
    n = len(a)
    if n == 0 or n != len(b):
        raise ValueError("need two equal-length, non-empty label lists")
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    chance = sum(ca[k] * cb[k] for k in ca.keys() | cb.keys()) / (n * n)
    return 1.0 if chance == 1 else (observed - chance) / (1 - chance)


def _binary(v: str) -> str:
    return "correct" if v == "correct" else "not_correct"


def compare(primary: Sequence[str], second: Sequence[str]) -> dict[str, Any]:
    """Agreement stats between two verdict lists (3-way and on the headline correct/not)."""
    pb, sb = [_binary(v) for v in primary], [_binary(v) for v in second]
    n = len(primary)
    return {
        "n": n,
        "agreement": sum(x == y for x, y in zip(pb, sb, strict=True)) / n,
        "kappa": cohen_kappa(pb, sb),
        "agreement_3way": sum(x == y for x, y in zip(primary, second, strict=True)) / n,
        "kappa_3way": cohen_kappa(primary, second),
        "confusion": {
            p: {
                s: sum(x == p and y == s for x, y in zip(primary, second, strict=True))
                for s in VERDICTS
            }
            for p in VERDICTS
        },
    }


def per_system(
    answers: Sequence[SavedAnswer], second: Sequence[str]
) -> dict[str, dict[str, float]]:
    acc: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for a, v in zip(answers, second, strict=True):
        acc[a.system].append((a.verdict == "correct", v == "correct"))
    return {
        s: {
            "primary": sum(p for p, _ in xs) / len(xs),
            "second": sum(q for _, q in xs) / len(xs),
            "n": len(xs),
        }
        for s, xs in sorted(acc.items())
    }


def ranking(systems: dict[str, dict[str, float]], judge: str) -> list[str]:
    return sorted(systems, key=lambda s: (-systems[s][judge], s))


def judge_check_markdown(
    answers: Sequence[SavedAnswer],
    second: Sequence[JudgeOutput],
    *,
    primary_model: str,
    second_model: str,
    report_name: str,
) -> str:
    verdicts = [o.verdict for o in second]
    stats = compare([a.verdict for a in answers], verdicts)
    systems = per_system(answers, verdicts)
    r1, r2 = ranking(systems, "primary"), ranking(systems, "second")
    lines = [
        f"# Judge check: {report_name}",
        "",
        f"Primary judge `{primary_model}` vs second judge `{second_model}`, same prompt, "
        f"{stats['n']} answers.",
        "",
        f"- **Agreement on correct / not correct:** {stats['agreement']:.1%} "
        f"(Cohen's kappa {stats['kappa']:.2f})",
        f"- **Agreement on the 3-way verdict:** {stats['agreement_3way']:.1%} "
        f"(kappa {stats['kappa_3way']:.2f})",
        f"- **System ranking:** {'unchanged' if r1 == r2 else 'CHANGED'} "
        f"({' > '.join(r1)}{'' if r1 == r2 else ' vs ' + ' > '.join(r2)})",
        "",
        "| System | n | Accuracy (primary) | Accuracy (second) | Δ |",
        "|---|---|---|---|---|",
    ]
    for s, m in systems.items():
        lines.append(
            f"| {s} | {m['n']:.0f} | {m['primary']:.1%} | {m['second']:.1%} | "
            f"{m['second'] - m['primary']:+.1%} |"
        )
    lines += [
        "",
        "Confusion (rows: primary, columns: second):",
        "",
        "| | " + " | ".join(VERDICTS) + " |",
    ]
    lines.append("|---" * (len(VERDICTS) + 1) + "|")
    for p in VERDICTS:
        lines.append(
            f"| {p} | " + " | ".join(str(stats["confusion"][p][s]) for s in VERDICTS) + " |"
        )
    disagree = [
        (a, o)
        for a, o in zip(answers, second, strict=True)
        if _binary(a.verdict) != _binary(o.verdict)
    ]
    if disagree:
        lines += ["", f"## Disagreements ({len(disagree)})", ""]
        for a, o in disagree:
            lines.append(
                f"- `{a.key}` ({a.category}): primary **{a.verdict}**, second **{o.verdict}**: "
                f"{o.reason}"
            )
    return "\n".join(lines) + "\n"


# -- hand check ---------------------------------------------------------------------------------


def hand_sample(answers: Sequence[SavedAnswer], n: int, seed: int) -> list[SavedAnswer]:
    """A stratified random sample: round-robin over systems so each is represented."""
    rng = random.Random(seed)
    by_system: dict[str, list[SavedAnswer]] = defaultdict(list)
    for a in answers:
        by_system[a.system].append(a)
    for xs in by_system.values():
        rng.shuffle(xs)
    picked: list[SavedAnswer] = []
    while len(picked) < min(n, len(answers)):
        for s in sorted(by_system):
            if by_system[s] and len(picked) < n:
                picked.append(by_system[s].pop())
    rng.shuffle(picked)  # so labels aren't read in system order
    return picked


def write_hand_check(sample: Sequence[SavedAnswer], sheet: Path, key: Path) -> None:
    """Blind sheet for the human (no system, no verdicts) plus a separate answer key."""
    sheet.parent.mkdir(parents=True, exist_ok=True)
    with sheet.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HAND_COLUMNS)
        for i, a in enumerate(sample, 1):
            gold = "UNANSWERABLE" if a.unanswerable else a.gold
            w.writerow([i, a.question, gold, a.answer, "yes" if a.abstained else "", ""])
    key.write_text(json.dumps({str(i): a.key for i, a in enumerate(sample, 1)}, indent=2))


def score_hand_check(
    sheet: Path, key: Path, judges: dict[str, dict[str, str]]
) -> dict[str, dict[str, Any]]:
    """Agreement of each judge (``{name: {answer key: verdict}}``) with the human labels."""
    mapping = json.loads(key.read_text())
    human: dict[str, str] = {}
    with sheet.open(newline="") as f:
        for row in csv.DictReader(f):
            label = SHORT_LABELS.get(
                row["your_verdict"].strip().lower(), row["your_verdict"].strip().lower()
            )
            if label:
                if label not in VERDICTS:
                    raise ValueError(f"item {row['item']}: unknown verdict {row['your_verdict']!r}")
                human[mapping[row["item"]]] = label
    if not human:
        raise ValueError(f"no labels filled in {sheet}")
    out = {}
    for name, verdicts in judges.items():
        keys = [k for k in human if k in verdicts]
        out[name] = compare([human[k] for k in keys], [verdicts[k] for k in keys])
    return out

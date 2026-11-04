"""The question kit on top of any product: record answers, import hand-written test questions,
and write reports that are safe to commit.

Privacy by construction: answers, questions and per-question reports live under ``data/real/``
(gitignored). The only writer meant for committed files is :func:`write_committable`, which
accepts aggregate metrics and run metadata and refuses anything per-question.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from concentrate.agents.baselines import Answer, SystemResult
from concentrate.blackbox.base import Product, ProductAnswer
from concentrate.blackbox.products import parse_granola_answer

REAL_DIR = Path("data/real")
ANSWERS_DIR = REAL_DIR / "answers"  # answers/<product>/<qid>-<hash>.json
LEGACY_DIRS = {"granola": REAL_DIR / "granola"}  # recorded before the black-box track


def _qhash(question: str) -> str:
    return hashlib.sha256(question.encode()).hexdigest()[:10]


class ProductSystem:
    """A product as a benchmark ``System``: each answer is asked once, recorded under
    ``record_dir`` (raw response, parsed answer, latency, timestamp) and replayed afterwards, so
    scores are reproducible and the product is not re-queried. A hosted product costs us $0 (it
    runs on its side); ``cost_usd`` is non-zero only when the product reports spend billed to our
    own key (Concentrate's answer model). Tokens are not visible from outside, so they are 0."""

    def __init__(
        self,
        product: str,
        live: Product | None,
        record_dir: Path | None = None,
        *,
        system_name: str | None = None,
        legacy_dirs: list[Path] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.product = product
        self.live = live
        self.record_dir = record_dir or ANSWERS_DIR / product
        self.legacy_dirs = legacy_dirs if legacy_dirs is not None else _legacy(product)
        self.name = system_name or product
        self.prompt_ids: tuple[str, ...] = (f"{product}:black-box",)
        self.clock = clock

    def record_path(self, q: Any) -> Path:
        return self.record_dir / f"{q.id}-{_qhash(q.question)}.json"

    def _record(self, q: Any) -> dict[str, Any]:
        path = self.record_path(q)
        for p in (path, *(d / path.name for d in self.legacy_dirs)):
            if p.exists():
                return dict(json.loads(p.read_text()))
        if self.live is None:
            raise RuntimeError(f"no recorded {self.product} answer for {q.id} and no connection")
        start = self.clock()
        ans = self.live.ask(q.question)
        rec = {
            "qid": q.id,
            "question": q.question,
            "product": self.product,
            **ans.model_dump(),
            "latency_s": self.clock() - start,  # end to end, including transport
            "asked_at": datetime.now(UTC).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=1))
        return rec

    def answer(self, q: Any) -> ProductAnswer:
        """The recorded (or freshly asked) answer to ``q``."""
        rec = self._record(q)
        if "text" not in rec:  # a pre-black-box Granola recording: raw only
            text, cited = parse_granola_answer(rec["raw"])
            rec = {**rec, "text": text, "cited_ids": cited}
        return ProductAnswer(
            text=rec["text"],
            cited_ids=rec.get("cited_ids", []),
            latency_s=float(rec["latency_s"]),
            raw=rec["raw"],
            abstained=rec.get("abstained"),
            cost_usd=float(rec.get("cost_usd", 0.0)),
        )

    def run(self, q: Any) -> SystemResult:
        a = self.answer(q)
        # No abstain flag (Granola): the judge reads the text, and the judge prompt already
        # treats "the notes don't say" as declining. Abstain metrics are then n/a.
        return SystemResult(
            answer=Answer(answer=a.text, abstain=bool(a.abstained), citations=a.cited_ids),
            latency_s=a.latency_s,
            cost_usd=a.cost_usd,
            tool_calls=1,
            retrieved=a.cited_ids,
        )

    def estimate(self, q: Any) -> tuple[int, int]:
        return 0, 0

    def recorded(self, questions: list[Any]) -> int:
        """How many of ``questions`` already have a recorded answer."""
        return sum(
            any(
                (d / self.record_path(q).name).exists()
                for d in (self.record_dir, *self.legacy_dirs)
            )
            for q in questions
        )


def _legacy(product: str) -> list[Path]:
    return [LEGACY_DIRS[product]] if product in LEGACY_DIRS else []


# -- importing hand-written test questions ---------------------------------------------------------

_FIELD = re.compile(r"^(Q|A|KEY|KIND):[ \t]*(.*)$")


class TemplateError(ValueError):
    pass


def parse_test_markdown(text: str, categories: tuple[str, ...]) -> list[dict[str, Any]]:
    """Question blocks (``Q:`` / ``A:`` / ``KEY:`` / ``KIND:`` lines) from the test template.

    Skips fenced code (the example block), blocks whose ``Q:`` is empty (the blank template),
    and stops at the first ``---`` rule (below it: the dev questions to avoid). KEY terms are
    separated by ``;`` (or ``,``); KIND may use spaces ("who said") and defaults to
    ``unanswerable`` when the answer is UNANSWERABLE. Raises :class:`TemplateError` naming
    the line of the first malformed block.
    """
    blocks: list[tuple[int, dict[str, str]]] = []
    cur: dict[str, str] = {}
    start = 0
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.strip() == "---":
            break
        m = _FIELD.match(line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "Q":
            if cur:
                blocks.append((start, cur))
            cur, start = {}, n
        cur[key] = value
    if cur:
        blocks.append((start, cur))

    out = []
    for line_no, b in blocks:
        q = b.get("Q", "")
        if not q:
            if any(b.get(k) for k in ("A", "KEY", "KIND")):
                raise TemplateError(f"line {line_no}: an answer with no question")
            continue
        gold = b.get("A", "")
        if not gold:
            raise TemplateError(f"line {line_no}: question has no A: (gold answer)")
        unanswerable = gold.strip().upper() == "UNANSWERABLE"
        kind = re.sub(r"[\s-]+", "_", b.get("KIND", "").strip().lower())
        kind = kind or ("unanswerable" if unanswerable else "")
        if kind not in categories:
            raise TemplateError(
                f"line {line_no}: KIND {b.get('KIND', '')!r} is not one of the kinds"
            )
        if unanswerable != (kind == "unanswerable"):
            raise TemplateError(f"line {line_no}: unanswerable questions need A: UNANSWERABLE")
        terms = [t.strip() for t in re.split(r"[;,]", b.get("KEY", "")) if t.strip()]
        out.append(
            {
                "category": kind,
                "question": q,
                "gold_answer": "UNANSWERABLE" if unanswerable else gold,
                "key_terms": [] if unanswerable else terms,
            }
        )
    return out


# -- reports that are safe to commit ---------------------------------------------------------------

# Per-question fields: any of these keys anywhere in a committable report is refused.
PER_QUESTION_KEYS = frozenset(
    {
        "qid",
        "question",
        "gold",
        "gold_answer",
        "answer",
        "answers",
        "reason",
        "raw",
        "text",
        "evidence",
        "evidence_note_ids",
        "citations",
        "cited_ids",
        "retrieved",
        "key_terms",
        "rows",
        "details",
    }
)
_ID = re.compile(
    r"\b(?:not_[A-Za-z0-9]{6,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-\w{12})"
)
MAX_STRING = 120  # longer strings look like content, not metadata


class NotCommittable(ValueError):
    """The report holds per-question content; it must stay under data/real/."""


def check_committable(obj: Any, forbidden_text: Sequence[str] = (), path: str = "$") -> None:
    """Raise :class:`NotCommittable` if ``obj`` holds per-question content: a per-question key,
    a meeting ID, a long free-text string, or any of ``forbidden_text`` (the questions, gold
    answers and product answers of the run, checked case-insensitively)."""
    needles = [t.lower() for t in forbidden_text if len(t.strip()) >= 12]
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in PER_QUESTION_KEYS:
                raise NotCommittable(f"{path}.{k}: per-question field")
            check_committable(str(k), needles, f"{path}.<key>")
            check_committable(v, needles, f"{path}.{k}")
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            check_committable(v, needles, f"{path}[{i}]")
    elif isinstance(obj, str):
        if len(obj) > MAX_STRING:
            raise NotCommittable(f"{path}: {len(obj)}-char string looks like content")
        if _ID.search(obj):
            raise NotCommittable(f"{path}: contains a meeting ID")
        low = obj.lower()
        if any(n in low for n in needles):
            raise NotCommittable(f"{path}: contains question/answer text from the run")


def committable(report: dict[str, Any]) -> dict[str, Any]:
    """The aggregate part of a black-box report: run metadata and per-product metrics."""
    keep = (
        "date",
        "git",
        "model",
        "seed",
        "split",
        "products",
        "questions",
        "prompts",
        "spent_usd",
        "stopped_early",
        "category_counts",
        "skipped_unrecorded",
    )
    return {
        "meta": {k: report["meta"].get(k) for k in keep if k in report["meta"]},
        "metrics": report["metrics"],
    }


def write_committable(
    report: dict[str, Any], path: Path, forbidden_text: list[str] | None = None
) -> Path:
    """Write the aggregate of ``report`` to ``path`` after :func:`check_committable` passes.
    Refuses (without writing) on any per-question content."""
    agg = committable(report)
    check_committable(agg, forbidden_text or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agg, indent=2, default=str) + "\n")
    return path

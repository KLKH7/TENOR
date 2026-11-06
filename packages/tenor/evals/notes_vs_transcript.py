"""``tenor notes-vs-transcript``: tag every question, then re-analyse saved runs by tag.

Writes two committed files, both safe to publish:

* ``evals/tags/notes_tags.json``: per-question tags for the **synthetic** sets (generated, not
  private), the portable rule's agreement with the generator reference, the hand-check, and
  **counts only** for the real slice;
* ``evals/reports/notes_vs_transcript.json``: accuracy / confident error by tag for the final
  synthetic test report, the retirement curve runs and (aggregates only) the real slice.

The real section needs the private ``data/real`` files; without them the previous real
section is kept. Write-up: ``docs/archive/northwind/NOTES_VS_TRANSCRIPT.md``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tenor.evals import by_tag
from tenor.evals.analyze import load_rows
from tenor.evals.notes_tags import (
    TAGS,
    Tag,
    TaggedQuestion,
    agreement,
    counts,
    note_notes_text,
    rule_tag,
    tag_synthetic,
)
from tenor.evals.runner import load_corpus, load_questions
from tenor.synth.world import World

TAGS_FILE = Path("evals/tags/notes_tags.json")
OUT_FILE = Path("evals/reports/notes_vs_transcript.json")
SYNTHETIC_SETS = ("northwind-xl", "northwind-xl-v2", "northwind-xl-v2.1")
LARGE = Path("data/fixtures/large")
TEST_REPORT = Path("evals/reports/20261103T002456Z.jsonl")
RETIREMENT = (
    Path("evals/reports/retirement/20261102T222859Z.jsonl"),
    Path("evals/reports/retirement/20261102T224523Z.jsonl"),
)
REAL_RUNS = (
    Path("data/real/reports/20261103T004019Z.jsonl"),  # Granola's agent + Concentrate
    Path("data/real/reports/20261103T013240Z.jsonl"),  # top-k, all 47
)
REAL_DB = Path("data/real.db")

NOTES_ONLY = "b1_summaries"
TEST_PAIRS = [(s, NOTES_ONLY) for s in ("b0_topk", "b3_agentic", "concentrate", "b2o_oracle")]
RETIREMENT_PAIRS = [
    ("concentrate@inf", "concentrate@0"),
    ("concentrate@inf", NOTES_ONLY),
    ("concentrate@0", NOTES_ONLY),
]
REAL_PAIRS = [("granola_agent", "concentrate")]
CE_UPPER_BOUND = ("granola_agent",)  # no abstain flag

# Hand-read check (2026-11-03): 20 held-out *test* questions of northwind-xl, 10 per generator
# tag (seed 42, ``hand_sample``), read against the gold meetings' notes by the author of this
# module: "could a careful reader give the gold answer from these notes alone?" Done after the
# taggers were tuned on dev; nothing was changed afterwards.
HAND_CHECK: dict[str, tuple[Tag, str]] = {
    "q023": (
        "transcript_needed",
        "position is in the notes; gold also names Sam Patel, who is not",
    ),
    "q036": ("transcript_needed", "no SSO bullet in the follow-up notes"),
    "q066": ("transcript_needed", "bug count missing"),
    "q132": ("transcript_needed", "admin-features count missing"),
    "q196": ("notes_sufficient", "CSV export bullet; 'open' is implied"),
    "q233": ("notes_sufficient", "NPS: 46"),
    "q240": ("notes_sufficient", "Support backlog: 63"),
    "q275": ("notes_sufficient", "CSV export request; 'open' is implied"),
    "q486": ("notes_sufficient", "typed notes: HIPAA BAA delivered"),
    "q506": ("transcript_needed", "target date missing"),
    "q533": ("notes_sufficient", "$25/seat/month"),
    "q560": ("transcript_needed", "Atlas status missing"),
    "q568": ("notes_sufficient", "Omar Haddad: no hire"),
    "q664": ("transcript_needed", "the 'at risk' change is not in the Sep 15 notes"),
    "q693": ("transcript_needed", "no Done line for the Android build"),
    "q729": ("transcript_needed", "no Done line for the latency benchmark"),
    "q755": ("transcript_needed", "no Done line for the doc review"),
    "q783": ("notes_sufficient", "Done: growth plan draft"),
    "q796": ("transcript_needed", "13 of 14 items; 'project retro notes' missing"),
    "q842": ("notes_sufficient", "target moved to Dec 04"),
}


def synthetic(fixtures: Path) -> list[TaggedQuestion]:
    world = World.model_validate_json((fixtures / "world.json").read_text())
    return tag_synthetic(load_questions(fixtures, "all"), load_corpus(fixtures), world)


def hand_sample(tagged: list[TaggedQuestion], seed: int = 42, k: int = 10) -> list[str]:
    """The hand-check sample: ``k`` test questions per generator tag (answerable only)."""
    import random

    rng = random.Random(seed)
    test = [t for t in tagged if t.split == "test"]
    picked = [
        *rng.sample([t for t in test if t.generator == "notes_sufficient"], k),
        *rng.sample([t for t in test if t.generator == "transcript_needed"], k),
    ]
    return sorted(t.qid for t in picked)


def hand_agreement(tagged: list[TaggedQuestion]) -> dict[str, Any]:
    by = {t.qid: t for t in tagged}
    rows = [(by[q], h) for q, (h, _) in sorted(HAND_CHECK.items())]
    return {
        "n": len(rows),
        "generator_agrees": sum(t.generator == h for t, h in rows),
        "rule_agrees": sum(t.rule == h for t, h in rows),
        "disagreements": [
            {"qid": t.qid, "hand": h, "generator": t.generator, "rule": t.rule,
             "note": HAND_CHECK[t.qid][1]}
            for t, h in rows
            if t.generator != h or t.rule != h
        ],
    }  # fmt: skip


def real_tags(db: Path = REAL_DB) -> dict[str, Tag]:
    """Portable-rule tags of the real questions (owner key terms vs Granola's own notes).

    PRIVATE: the result maps real question IDs to tags; callers keep it in memory and publish
    only counts."""
    from tenor.core.store import NoteStore
    from tenor.evals.real_slice import load_questions as load_real

    store = NoteStore(db)
    try:
        text = {n.note_id: note_notes_text(n) for n in store.iter_notes()}
    finally:
        store.close()
    out: dict[str, Tag] = {}
    for q in load_real():
        items = ["UNANSWERABLE"] if q.unanswerable else q.key_terms
        out[q.id] = rule_tag(items, (text[m] for m in q.evidence_note_ids if m in text))
    return out


def _section(
    rows: list[dict[str, Any]], tags: Mapping[str, str], pairs: list[tuple[str, str]], seed: int
) -> dict[str, Any]:
    return by_tag.to_json(by_tag.by_tag(rows, tags, seed), by_tag.tag_gaps(rows, tags, pairs, seed))


def build(seed: int = 0, *, real: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """(tags file, analysis file) as JSON-ready dicts."""
    tagged = {name: synthetic(LARGE / name) for name in SYNTHETIC_SETS if (LARGE / name).exists()}
    v1 = tagged["northwind-xl"]
    primary = {t.qid: t.tag for t in v1}
    rule = {t.qid: t.rule for t in v1}
    tags_out: dict[str, Any] = {
        "definition": "docs/archive/northwind/NOTES_VS_TRANSCRIPT.md",
        "primary": "generator reference (synthetic); portable rule (real)",
        "synthetic": {
            name: {
                "counts": counts(ts),
                "counts_by_split": counts(ts, by="split"),
                "agreement_rule_vs_generator": {
                    sp: agreement(t for t in ts if t.split == sp) for sp in ("dev", "test")
                },
                "tags": {t.qid: {"generator": t.generator, "rule": t.rule} for t in ts},
            }
            for name, ts in tagged.items()
        },
        "hand_check": hand_agreement(v1),
    }
    test_rows = load_rows(TEST_REPORT)
    analysis: dict[str, Any] = {
        "seed": seed,
        "test_report": {
            "report": TEST_REPORT.stem,
            "primary": _section(test_rows, primary, TEST_PAIRS, seed),
            "rule_tags": _section(test_rows, rule, TEST_PAIRS, seed),
        },
        "retirement": {
            p.stem: _section(load_rows(p), primary, RETIREMENT_PAIRS, seed) for p in RETIREMENT
        },
    }
    if real and REAL_DB.exists() and all(p.exists() for p in REAL_RUNS):
        rt = real_tags()
        tags_out["real"] = {"counts": _real_counts(rt), "tagger": "portable rule (key terms)"}
        analysis["real"] = {p.stem: _section(load_rows(p), rt, REAL_PAIRS, seed) for p in REAL_RUNS}
    else:  # keep the last published real aggregates
        old_tags = json.loads(TAGS_FILE.read_text()) if TAGS_FILE.exists() else {}
        old = json.loads(OUT_FILE.read_text()) if OUT_FILE.exists() else {}
        if "real" in old_tags:
            tags_out["real"] = old_tags["real"]
        if "real" in old:
            analysis["real"] = old["real"]
    return tags_out, analysis


def _real_counts(rt: dict[str, Tag]) -> dict[str, dict[str, int]]:
    """Counts by real category: the only real output that is ever written."""
    from tenor.evals.real_slice import load_questions as load_real

    cat = {q.id: q.category for q in load_real()}
    out: dict[str, Counter[str]] = {}
    for qid, t in rt.items():
        for key in (cat[qid], "all"):
            out.setdefault(key, Counter())[t] += 1
    return {k: {t: v[t] for t in TAGS} for k, v in sorted(out.items())}


def to_markdown(tags: dict[str, Any], a: dict[str, Any]) -> str:
    """The generated tables behind ``docs/archive/northwind/NOTES_VS_TRANSCRIPT.md``."""
    lines = ["# Notes-sufficient vs transcript-needed: generated tables", "",
             "Regenerate: `uv run tenor notes-vs-transcript`. Write-up and caveats: "
             "`docs/archive/northwind/NOTES_VS_TRANSCRIPT.md`.",
             "", "## Tag counts", ""]  # fmt: skip
    lines += ["| Set | Split | notes-sufficient | transcript-needed | unanswerable | "
              "rule vs generator (test): agreement · κ |", "|---|---|---|---|---|---|"]  # fmt: skip
    for name, s in tags["synthetic"].items():
        ag = s["agreement_rule_vs_generator"]["test"]
        for sp in ("dev", "test"):
            c = s["counts_by_split"][sp]
            extra = f"{100 * ag['agreement']:.0f}% · {ag['kappa']:.2f}" if sp == "test" else ""
            lines.append(f"| {name} | {sp} | {c['notes_sufficient']} | "
                         f"{c['transcript_needed']} | {c['unanswerable']} | {extra} |")  # fmt: skip
    if "real" in tags:
        c = tags["real"]["counts"]["all"]
        lines.append(f"| real (portable rule) | dev | {c['notes_sufficient']} | "
                     f"{c['transcript_needed']} | {c['unanswerable']} | n/a |")  # fmt: skip
    h = tags["hand_check"]
    lines += ["", f"Hand check ({h['n']} held-out test questions): generator reference agrees "
              f"on {h['generator_agrees']}, portable rule on {h['rule_agrees']}.", ""]  # fmt: skip

    def section(title: str, s: dict[str, Any]) -> list[str]:
        cells = [by_tag.TagCell(**c) for c in s["cells"]]
        gaps = [by_tag.TagGap(**g) for g in s["gaps"]]
        return ["", f"## {title}", "", *by_tag.cells_markdown(cells, ce_upper_bound=CE_UPPER_BOUND),
                "", *by_tag.gaps_markdown(gaps)]  # fmt: skip

    t = a["test_report"]
    lines += section(f"Synthetic test report {t['report']} (generator tags)", t["primary"])
    lines += section(f"Synthetic test report {t['report']} (portable-rule tags)", t["rule_tags"])
    for stem, s in a["retirement"].items():
        lines += section(f"Retirement curve run {stem} (dev, generator tags)", s)
    for stem, s in a.get("real", {}).items():
        lines += section(f"Real slice {stem} (aggregates only, portable-rule tags)", s)
    return "\n".join(lines) + "\n"


def write(seed: int = 0, *, real: bool = True) -> list[Path]:
    tags, a = build(seed, real=real)
    TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TAGS_FILE.write_text(json.dumps(tags, indent=1, sort_keys=True) + "\n")
    OUT_FILE.write_text(json.dumps(a, indent=1, sort_keys=True) + "\n")
    md = OUT_FILE.with_suffix(".md")
    md.write_text(to_markdown(tags, a))
    return [TAGS_FILE, OUT_FILE, md]

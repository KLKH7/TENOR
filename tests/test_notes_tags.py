"""Notes-sufficient / transcript-needed tags, confident error and the by-tag re-analysis."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from concentrate.evals import by_tag as bt
from concentrate.evals import metrics as mx
from concentrate.evals import notes_tags as nt
from concentrate.evals import notes_vs_transcript as nvt
from concentrate.evals.runner import load_corpus, load_questions
from concentrate.synth.world import World

NORTHWIND = Path("data/fixtures/northwind")
XL = Path("data/fixtures/large/northwind-xl")


def _row(system: str, qid: str, verdict: str = "correct", **kw: Any) -> dict[str, Any]:
    return {"system": system, "qid": qid, "verdict": verdict, "correct": verdict == "correct",
            "abstained": False, "unanswerable": False} | kw  # fmt: skip


# -- the portable rule -------------------------------------------------------------------------


def test_notes_text_has_title_date_summary_and_typed_notes() -> None:
    t = nt.notes_text("Pricing review", datetime(2026, 9, 29, 10, tzinfo=UTC),
                      "### Pricing\n- Price raised to **$25**", "typed bit")  # fmt: skip
    assert "Pricing review" in t and "Tuesday, September 29, 2026 (2026-09-29)" in t
    assert "$25" in t and "**" not in t and "typed bit" in t


def test_rule_values_people_dates_and_statuses() -> None:
    summary = "- Megan owns Kite\n- Done: audit logs (Sam)\n- Backlog: 62"
    text = nt.notes_text("Sync", datetime(2026, 9, 22, tzinfo=UTC), summary, None)
    assert nt.rule_tag(["megan"], [text]) == "notes_sufficient"  # first name is enough
    assert nt.rule_tag(["2026-09-22"], [text]) == "notes_sufficient"  # meeting date in header
    assert nt.rule_tag(["done", "62"], [text]) == "notes_sufficient"
    assert nt.rule_tag(["63"], [text]) == "transcript_needed"
    assert nt.rule_tag(["slipped"], [text]) == "transcript_needed"
    # "open"/"dropped" are absences: recoverable when the commitment itself is in the notes
    assert nt.rule_tag(["open"], [text], anchors=["audit logs"]) == "notes_sufficient"
    assert nt.rule_tag(["open"], [text], anchors=["cutover script"]) == "transcript_needed"
    assert nt.rule_tag(["UNANSWERABLE"], [text]) == "unanswerable"
    assert nt.rule_tag([], [text]) == "unanswerable"


def test_agreement_and_kappa() -> None:
    T = nt.TaggedQuestion
    ts = [T("a", "c", "dev", "notes_sufficient", "notes_sufficient"),
          T("b", "c", "dev", "transcript_needed", "transcript_needed"),
          T("c", "c", "dev", "notes_sufficient", "transcript_needed"),
          T("d", "c", "dev", "transcript_needed", "notes_sufficient"),
          T("e", "c", "dev", "unanswerable", "unanswerable")]  # fmt: skip
    a = nt.agreement(ts)
    assert a["n"] == 4 and a["agreement"] == 0.5 and a["kappa"] == pytest.approx(0.0)
    assert nt.counts(ts)["all"] == {"notes_sufficient": 2, "transcript_needed": 2,
                                    "unanswerable": 1}  # fmt: skip


# -- the generator reference on the committed fixture -----------------------------------------


@pytest.fixture(scope="module")
def northwind_tags() -> list[nt.TaggedQuestion]:
    world = World.model_validate_json((NORTHWIND / "world.json").read_text())
    return nt.tag_synthetic(load_questions(NORTHWIND, "all"), load_corpus(NORTHWIND), world)


def test_generator_reference_matches_planted_bullets(
    northwind_tags: list[nt.TaggedQuestion],
) -> None:
    world = World.model_validate_json((NORTHWIND / "world.json").read_text())
    notes = {n.note_id: nt.note_notes_text(n) for n in load_corpus(NORTHWIND)}
    nid = {m.key: m.note_id for m in world.meetings}
    qs = {q.id: q for q in load_questions(NORTHWIND, "all")}
    facts = {f.id: f for f in world.facts}
    for t in northwind_tags:
        q = qs[t.qid]
        if t.generator == "unanswerable":
            assert q.answer_norm == ["UNANSWERABLE"]
        # a dropped planted fact bullet always means transcript-needed
        for s in q.sources:
            f = facts.get(s)
            if f is not None:
                kept = nt._flat(f.bullet) in nt._flat(notes[nid[f.evidence[0].meeting]])
                if not kept and nt._typed_form(f.bullet) not in nt._flat(
                    notes[nid[f.evidence[0].meeting]]
                ):
                    assert t.generator == "transcript_needed", t.qid
    assert {t.generator for t in northwind_tags} >= {"notes_sufficient", "transcript_needed"}


def test_tagging_is_deterministic(northwind_tags: list[nt.TaggedQuestion]) -> None:
    world = World.model_validate_json((NORTHWIND / "world.json").read_text())
    again = nt.tag_synthetic(load_questions(NORTHWIND, "all"), load_corpus(NORTHWIND), world)
    assert again == northwind_tags


# -- confident error and the by-tag re-analysis -----------------------------------------------


def test_confident_error_counts_answered_incorrect_only() -> None:
    rows = [_row("s", "1"), _row("s", "2", "incorrect"),
            _row("s", "3", "incorrect", abstained=True),  # declined: not confident
            _row("s", "4", "partially_correct"),  # incomplete, not wrong
            _row("s", "5", "incorrect", unanswerable=True)]  # answered an unanswerable  # fmt: skip
    assert mx.confident_error(rows) == pytest.approx(2 / 5)
    (a,) = mx.abstain_stats(rows)
    assert a.confident_error == pytest.approx(2 / 5) and a.abstained_answer_existed == 1
    assert mx.confident_error([]) is None


def test_by_tag_cells_and_paired_gaps() -> None:
    tags = {"1": "notes_sufficient", "2": "notes_sufficient", "3": "transcript_needed",
            "4": "transcript_needed", "5": "unanswerable"}  # fmt: skip
    rows = [_row("reader", q) for q in "12345"]
    rows += [_row("notes", "1"), _row("notes", "2"), _row("notes", "3", "incorrect"),
             _row("notes", "4", "incorrect", abstained=True), _row("notes", "5"),
             _row("notes", "x")]  # untagged: skipped  # fmt: skip
    cells = {(c.system, c.tag): c for c in bt.by_tag(rows, tags, seed=1)}
    tn = cells[("notes", "transcript_needed")]
    assert tn.n == 2 and tn.accuracy == 0 and tn.confident_error == 0.5
    assert tn.abstained_answer_existed == 1
    assert cells[("notes", "all")].n == 5
    gaps = {g.tag: g for g in bt.tag_gaps(rows, tags, [("reader", "notes")], seed=1)}
    assert gaps["transcript_needed"].diff == 1.0 and gaps["transcript_needed"].a_only == 2
    assert gaps["notes_sufficient"].diff == 0.0
    md = "\n".join(bt.cells_markdown(list(cells.values()), ce_upper_bound=["notes"]))
    assert "≤ 50%" in md


# -- committed outputs -------------------------------------------------------------------------


def test_committed_tags_file_has_no_real_question_ids() -> None:
    tags = json.loads(nvt.TAGS_FILE.read_text())
    real = json.dumps(tags.get("real", {}))
    assert "tags" not in tags.get("real", {})
    assert set(tags["real"]["counts"]["all"]) == set(nt.TAGS)
    out = json.loads(nvt.OUT_FILE.read_text())
    for section in out.get("real", {}).values():
        assert {"cells", "gaps"} == set(section)
    assert '"q' not in real  # counts only, keyed by category


@pytest.mark.skipif(not XL.exists(), reason="northwind-xl is gitignored (regenerate with synth)")
def test_committed_synthetic_tags_and_hand_sample_reproduce() -> None:
    tagged = nvt.synthetic(XL)
    committed = json.loads(nvt.TAGS_FILE.read_text())["synthetic"]["northwind-xl"]["tags"]
    assert {t.qid: {"generator": t.generator, "rule": t.rule} for t in tagged} == committed
    assert nvt.hand_sample(tagged) == sorted(nvt.HAND_CHECK)

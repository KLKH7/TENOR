"""The deterministic payload classifiers behind docs/results/MCP_PAYLOAD.md (synthetic text)."""

from __future__ import annotations

from pathlib import Path

import pytest

from concentrate.blackbox.kit import check_committable
from concentrate.evals import mcp_payload as M

DISCLAIMER = "<transcript_note>This answer comes from meeting notes.</transcript_note>"
LINK = "[[1]](https://notes.granola.ai/d/abc123)"


def raw(body: str) -> str:
    return f"{body} {LINK}\n\n{DISCLAIMER}"


def test_profile_counts_note_links_and_disclaimer() -> None:
    p = M.profile(raw("Dana owns the rollout.") + " [[2]](https://notes.granola.ai/d/xyz789)", 7.5)
    assert p.citations == 2 and p.distinct_cited == 2
    assert p.has_disclaimer and p.span_refs == 0 and not p.mentions_transcript
    assert p.latency_s == 7.5 and p.words == 4
    assert not (p.hedge or p.coverage_caveat or p.gap_statement or p.abstain)


def test_disclaimer_is_not_a_transcript_mention_or_span() -> None:
    p = M.profile(raw("Yes."), 1.0)
    assert not p.mentions_transcript and p.span_refs == 0


@pytest.mark.parametrize(
    "body",
    ["See chunk_ids [3, 4].", "At [12:05] she said so.", "Line [not_ab12:0042] says it."],
)
def test_span_references_are_detected(body: str) -> None:
    assert M.profile(raw(body), 1.0).span_refs >= 1


def test_quotes_and_verbatim_match() -> None:
    body = "She said “we ship on Friday” and called it “fine”."
    p = M.profile(raw(body), 1.0, source_text="Okay. We ship on Friday, right?")
    assert p.quotes == 1  # the one-word quote is too short to count
    assert p.verbatim_quotes == 1
    assert M.profile(raw(body), 1.0, source_text="nothing like it").verbatim_quotes == 0


@pytest.mark.parametrize(
    ("body", "flag"),
    [
        ("The vendor might be the second one.", "hedge"),
        ("It appears to have been dropped.", "hedge"),
        ("Not done yet, based on the available meetings.", "coverage_caveat"),
        ("Nothing changed, based on the latest update.", "coverage_caveat"),
        ("The meeting did not name the client.", "gap_statement"),
        ("There\u2019s no later confirmation it was sent.", "gap_statement"),
        ("I found no later meeting record.", "gap_statement"),
        ("I couldn\u2019t find any accessible meeting notes about that.", "abstain"),
    ],
)
def test_flags(body: str, flag: str) -> None:
    p = M.profile(raw(body), 1.0)
    assert getattr(p, flag), body
    assert p.flagged


def test_plain_negative_statement_is_not_a_flag() -> None:
    # "not delete them" is content, not a caveat; "may" inside a word is not a hedge.
    p = M.profile(raw("Staff could edit entries but not delete them; Mayfield agreed."), 1.0)
    assert not p.flagged


def test_strict_flags_exclude_gap_statements() -> None:
    p = M.profile(raw("The meeting did not name the client."), 1.0)
    assert p.flagged and not p.flagged_strict


@pytest.mark.parametrize(
    ("verdict", "flagged", "unanswerable", "expected"),
    [
        ("correct", False, False, "useful_comprehensive"),
        ("correct", True, True, "useful_comprehensive"),
        ("partially_correct", False, False, "useful_incomplete"),
        ("partially_correct", True, False, "incomplete_clearly"),
        ("incorrect", True, False, "incomplete_clearly"),
        ("incorrect", False, False, "wrong_confident"),
        ("incorrect", False, True, "wrong_confident"),
        ("partially_correct", False, True, "wrong_confident"),
    ],
)
def test_bucket_rule(verdict: str, flagged: bool, unanswerable: bool, expected: str) -> None:
    assert M.bucket(verdict, flagged, unanswerable) == expected


def test_stability_identical_answers() -> None:
    a = ("Dana owns it.", ["m1"], 8.0)
    s = M.stability([a, a, a], ["Dana"], unanswerable=False)
    assert s.key_terms_stable and s.flags_stable and s.citations_identical
    assert s.citation_jaccard == 1.0 and s.latency_sd == 0.0 and not s.needs_human_check


def test_stability_detects_attribution_flip() -> None:
    answers = [("Dana owns it.", ["m1"], 7.0), ("Sam owns it.", ["m1", "m2"], 9.0)]
    s = M.stability(answers, ["Dana"], unanswerable=False)
    assert not s.key_terms_stable and s.any_key_term_flip and s.needs_human_check
    assert not s.citations_identical and s.citation_jaccard == 0.5
    assert s.latency_sd == 1.0


def test_stability_flags_change_on_unanswerable() -> None:
    answers = [("The meeting did not name it.", [], 5.0), ("It was the second one.", ["m1"], 5.0)]
    s = M.stability(answers, [], unanswerable=True)
    assert s.key_terms_stable and not s.flags_stable and s.needs_human_check


def test_jaccard_and_key_terms() -> None:
    assert M.jaccard([], []) == 1.0
    assert M.jaccard(["a"], ["b"]) == 0.0
    assert M.key_term_hits("Ship on FRIDAY.", ["friday", "Monday"]) == (True, False)


def test_stratified_sample_takes_errors_first_and_is_deterministic() -> None:
    rows = [{"qid": f"q{i:02d}", "category": f"c{i % 3}", "verdict": "correct"} for i in range(12)]
    rows += [{"qid": "bad1", "category": "c0", "verdict": "incorrect"}]
    rows += [{"qid": "u1", "category": "unanswerable", "verdict": "correct"}]
    s = M.stratified_sample(rows, n=5, seed=1)
    assert s == M.stratified_sample(rows, n=5, seed=1)
    assert s[0] == "bad1" and "u1" in s and len(s) == len(set(s)) == 5
    assert {r["category"] for r in rows if r["qid"] in s} == {"c0", "c1", "c2", "unanswerable"}


def test_aggregate_is_committable() -> None:
    p = M.profile(raw("The meeting did not name the client."), 6.0).model_dump()
    rows = [
        {
            "category": "who_said",
            "tag": "notes_sufficient",
            "verdict": v,
            "profile": p,
            "bucket": M.bucket(v, True, False),
            "bucket_strict": M.bucket(v, False, False),
            "bucket_judge2": M.bucket(v, True, False),
        }
        for v in ("correct", "incorrect")
    ]
    s = M.stability([("x", [], 1.0), ("x", [], 3.0)], [], unanswerable=True).model_dump()
    stab = [
        {"category": "who_said", "tag": "unanswerable", "stability": s, "repeat_latencies": [3.0]}
    ]
    agg = M.aggregate(rows, stab, {"date": "2026-11-04"})
    check_committable(agg, ["The meeting did not name the client."])
    assert agg["four_types"]["overall"]["incomplete_clearly"]["k"] == 1
    assert agg["four_types"]["overall_strict_flags"]["wrong_confident"]["k"] == 1
    assert agg["profile"]["overall"]["latency_p50"] == 6.0


def test_write_private_refuses_outside_data_real(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        M.write_private(tmp_path / "rows.jsonl", [{"qid": "x"}])

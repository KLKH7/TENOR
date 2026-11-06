"""The agent-interface track's deterministic classifiers and scoring (synthetic text only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tenor.blackbox.base import ProductAnswer
from tenor.blackbox.kit import check_committable
from tenor.evals import agent_interface as A

DISCLAIMER = (
    "<transcript_note>This answer comes from meeting notes. Before answering, call "
    "search_transcripts with keywords from the question.</transcript_note>"
)
LINK = "[[1]](https://notes.granola.ai/d/abc123)"
granola = A.offline_parser("granola")
concentrate = A.offline_parser("concentrate")


def g(body: str, latency: float = 1.0) -> ProductAnswer:
    """A Granola-shaped payload, parsed by Granola's adapter."""
    return granola(f"{body} {LINK}\n\n{DISCLAIMER}", latency)


def c(body: str, cites: list[str], abstain: bool = False) -> ProductAnswer:
    """A Concentrate ask_memory-shaped payload, parsed by Concentrate's adapter."""
    ans = {"answer": body, "abstain": abstain, "citations": cites}
    return concentrate(A.ask_memory_payload(ans), 2.0)


def test_granola_payload_is_meeting_level_and_instructs_the_caller() -> None:
    p = A.profile(g("Dana owns the rollout.", 7.5))
    assert p.granularity == "meeting" and p.meetings_cited == 1 and p.line_refs == 0
    assert p.instructs_caller and not p.mentions_transcript  # the note is outside the answer
    assert not p.abstain_field and p.latency_s == 7.5 and p.words == 4
    assert not p.flagged


def test_concentrate_payload_is_line_level_with_an_abstain_field() -> None:
    p = A.profile(c("Dana owns it [not_ab12cd:0042].", ["not_ab12cd:0042", "not_ab12cd:0050"]))
    assert p.granularity == "line" and p.line_refs == 2 and p.meetings_cited == 1
    assert p.abstain_field and not p.abstain and not p.instructs_caller
    assert p.words == 3  # the bracketed line ID is a marker, not prose
    q = A.profile(c("The material does not say.", [], abstain=True))
    assert q.granularity == "none" and q.abstain and q.flagged


def test_abstain_field_wins_over_prose() -> None:
    # A machine flag is authoritative; the prose heuristic is only for products without one.
    assert not A.profile(c("I couldn't find any notes on it.", [], abstain=False)).abstain
    assert A.profile(g("I couldn\u2019t find any accessible meeting notes about that.")).abstain


@pytest.mark.parametrize("body", ["See chunk_ids [3, 4].", "At [12:05] she said so."])
def test_span_references_count_as_line_level(body: str) -> None:
    p = A.profile(g(body))
    assert p.span_refs >= 1 and p.granularity == "line"


def test_quotes_and_verbatim_match() -> None:
    a = g("She said \u201cwe ship on Friday\u201d and called it \u201cfine\u201d.")
    p = A.profile(a, source_text="Okay. We ship on Friday, right?")
    assert p.quotes == 1 and p.verbatim_quotes == 1  # the one-word quote is too short
    assert A.profile(a, source_text="nothing like it").verbatim_quotes == 0


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
    ],
)
def test_text_flags(body: str, flag: str) -> None:
    p = A.profile(g(body))
    assert getattr(p, flag), body
    assert p.flagged


def test_plain_negative_statement_is_not_a_flag() -> None:
    # "not delete them" is content, not a caveat; "may" inside a word is not a hedge.
    assert not A.profile(
        g("Staff could edit entries but not delete them; Mayfield agreed.")
    ).flagged


def test_strict_flags_exclude_gap_statements() -> None:
    p = A.profile(g("The meeting did not name the client."))
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
    assert A.bucket(verdict, flagged, unanswerable) == expected


def test_stability_identical_answers() -> None:
    a = g("Dana owns it.", 8.0)
    s = A.stability([a, a, a], ["Dana"], unanswerable=False)
    assert s.key_terms_stable and s.flags_stable and s.citations_identical
    assert s.citation_jaccard == 1.0 and s.latency_sd == 0.0 and not s.needs_human_check


def test_stability_detects_attribution_flip() -> None:
    a = g("Dana owns it.", 7.0)
    b = granola(f"Sam owns it. {LINK} [[2]](https://notes.granola.ai/d/xyz789)", 9.0)
    s = A.stability([a, b], ["Dana"], unanswerable=False)
    assert not s.key_terms_stable and s.any_key_term_flip and s.needs_human_check
    assert not s.citations_identical and s.citation_jaccard == 0.5 and s.latency_sd == 1.0


def test_stability_flag_change_on_unanswerable() -> None:
    s = A.stability([g("The meeting did not name it."), g("It was the second one.")], [], True)
    assert s.key_terms_stable and not s.flags_stable and s.needs_human_check


def test_jaccard_and_key_terms() -> None:
    assert A.jaccard([], []) == 1.0 and A.jaccard(["a"], ["b"]) == 0.0
    assert A.key_term_hits("Ship on FRIDAY.", ["friday", "Monday"]) == (True, False)


def test_stratified_sample_takes_errors_first_and_is_deterministic() -> None:
    rows = [{"qid": f"q{i:02d}", "category": f"c{i % 3}", "verdict": "correct"} for i in range(12)]
    rows += [{"qid": "bad1", "category": "c0", "verdict": "incorrect"}]
    rows += [{"qid": "u1", "category": "unanswerable", "verdict": "correct"}]
    s = A.stratified_sample(rows, n=5, seed=1)
    assert s == A.stratified_sample(rows, n=5, seed=1)
    assert s[0] == "bad1" and "u1" in s and len(s) == len(set(s)) == 5
    assert {r["category"] for r in rows if r["qid"] in s} == {"c0", "c1", "c2", "unanswerable"}


class _Q:
    def __init__(self, qid: str, category: str, key_terms: list[str]) -> None:
        self.id, self.category, self.key_terms = qid, category, key_terms
        self.question = f"question {qid}?"
        self.gold_answer = "UNANSWERABLE" if category == "unanswerable" else "Dana"

    @property
    def unanswerable(self) -> bool:
        return self.category == "unanswerable"


class _FakeProduct:
    name = "fake"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def ask(self, question: str) -> ProductAnswer:
        self.asked.append(question)
        return ProductAnswer(text="Dana.", cited_ids=["m1"], latency_s=1.0, raw="Dana.")


def test_record_repeats_respects_the_cap_and_reuses_recordings(tmp_path: Path) -> None:
    qs = [_Q("q1", "who_said", ["Dana"]), _Q("q2", "decision", ["x"])]
    prod = _FakeProduct()
    assert A.record_repeats(prod, qs, ["q1", "q2"], 2, max_calls=3, root=tmp_path) == 3
    assert A.record_repeats(prod, qs, ["q1", "q2"], 2, max_calls=3, root=tmp_path) == 1
    assert len(prod.asked) == 4
    assert [a.text for a in A.load_repeats("fake", "q1", 2, tmp_path)] == ["Dana.", "Dana."]


def test_score_entry_and_aggregate_are_committable(tmp_path: Path) -> None:
    root = tmp_path / "data" / "real" / "agent_interface"
    qs = [_Q("q1", "who_said", ["Dana"]), _Q("q2", "unanswerable", [])]
    entry = A.Entry(
        product="fake",
        interface="ask via a fake MCP",
        payload_source="synthetic",
        judge_report="r.jsonl",
        answers={"q1": g("Dana owns the rollout."), "q2": g("It was the blue vendor.")},
        verdicts={"q1": ("correct", None), "q2": ("incorrect", None)},
    )
    A.record_repeats(_FakeProduct(), qs, ["q1", "q2"], 2, 10, root)
    rows, stab, sample = A.score_entry(entry, qs, {}, tmp_path / "none.db", 2, 2, 0, root)
    assert {r["bucket"] for r in rows} == {"useful_comprehensive", "wrong_confident"}
    assert len(stab) == 2 and sorted(sample) == ["q1", "q2"]
    assert (root / "fake" / "needs_human_check.md").exists()
    agg = A.aggregate({"fake": (entry, rows, stab)}, {"date": "2026-11-04"})
    check_committable(agg, [q.question for q in qs] + [a.text for a in entry.answers.values()])
    block = agg["products"]["fake"]
    assert block["four_types"]["overall"]["wrong_confident"]["k"] == 1
    assert block["four_types"]["overall_judge2"] is None  # no second judge in the run
    assert block["profile"]["overall"]["granularity"]["meeting"]["k"] == 2
    assert block["stability"]["questions"] == 2


def test_verdicts_and_report_answers_round_trip(tmp_path: Path) -> None:
    rep = tmp_path / "r.jsonl"
    row = {
        "system": "concentrate",
        "qid": "q1",
        "verdict": "correct",
        "latency_s": 3.0,
        "answer": {
            "answer": "Dana [not_ab12cd:0042].",
            "abstain": False,
            "citations": ["not_ab12cd:0042"],
        },
    }
    rep.write_text(json.dumps(row) + "\n" + json.dumps({**row, "system": "other"}) + "\n")
    assert A.verdicts_from_report(rep, "concentrate") == {"q1": ("correct", None)}
    ans = A.answers_from_report(rep, "concentrate", A.ask_memory_payload, concentrate)
    assert ans["q1"].cited_ids == ["not_ab12cd"] and ans["q1"].abstained is False


def test_offline_parser_never_calls_out() -> None:
    with pytest.raises(RuntimeError):
        A._NoNetwork().call("query_granola_meetings", {})


def test_write_private_refuses_outside_data_real(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        A.write_private(tmp_path / "rows.jsonl", [{"qid": "x"}])

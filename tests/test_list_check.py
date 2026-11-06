"""E1 list-then-check: chunking, dedupe and merge repair, the judge's item mapping, scoring
math, sign tests, the quote and determinism (no network: a scripted fake backend)."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tenor.evals import list_check as E
from tenor.llm.client import Completion, Ledger, LLMClient, LLMRequest
from tests.memory_helpers import make_note


def _lines(n: int, text: str = "word " * 10) -> list[E.Line]:
    return [E.Line(i, "Them", f"00:{i:02d}", f"{i} {text}".strip()) for i in range(n)]


# -- chunking ---------------------------------------------------------------------------------


def test_chunks_cover_every_line_in_order_with_overlap() -> None:
    lines = _lines(40)
    size = len(lines[0].render()) + 1
    chunks = E.chunk_lines(lines, max_chars=size * 10, overlap=2)
    assert len(chunks) > 1
    assert all(len("\n".join(x.render() for x in c)) < size * 10 for c in chunks)
    seen = [x.index for c in chunks for x in c]
    assert sorted(set(seen)) == list(range(40))
    for prev, nxt in itertools.pairwise(chunks):
        assert [x.index for x in nxt[:2]] == [x.index for x in prev[-2:]]


def test_chunk_edge_cases() -> None:
    assert E.chunk_lines([], 100) == []
    one = _lines(3)
    assert E.chunk_lines(one, 10_000) == [one]
    huge = [E.Line(0, "Them", "00:00", "x" * 500), *_lines(2)[1:]]
    chunks = E.chunk_lines(huge, max_chars=100, overlap=0)
    assert chunks[0] == [huge[0]] and [x.index for c in chunks for x in c] == [0, 1]


def test_transcript_lines_use_channel_labels_and_relative_time() -> None:
    note = make_note("n1", "2026-11-05", "AMI X", [("them", None, "hello"), ("me", None, "hi")])
    lines = E.transcript_lines(note)
    assert [(x.speaker, x.start) for x in lines] == [("Them", "00:00"), ("Me", "00:10")]
    assert lines[1].render() == "[L0001 Me 00:10] hi"


def test_merge_turns_joins_same_speaker_runs() -> None:
    ls = [
        E.Line(0, "PM", "00:00", "a"),
        E.Line(1, "PM", "00:01", "b"),
        E.Line(2, "ME", "00:02", "c"),
    ]
    assert E.merge_turns(ls) == [E.Line(0, "PM", "00:00", "a b"), ls[2]]


def test_quote_found_is_normalized_and_spans_lines() -> None:
    ls = [E.Line(0, "Them", "", "We'll go with, um, rubber"), E.Line(1, "Them", "", "buttons.")]
    assert E.quote_found("we'll go with um rubber buttons", ls)
    assert E.quote_found("We\u2019ll go with", ls)
    assert not E.quote_found("titanium buttons", ls)
    assert not E.quote_found("", ls)


# -- candidates, dedupe, merge ----------------------------------------------------------------


def _cand(item: str, quote: str, lines: tuple[int, ...] = (0,), chunk: int = 0) -> E.Candidate:
    return E.Candidate(item, quote, lines, "Them", chunk, True)


def test_candidates_drop_out_of_chunk_lines_and_read_speaker_off_the_line() -> None:
    chunk = [E.Line(5, "Them", "", "rubber buttons it is"), E.Line(6, "Me", "", "ok")]
    out = E.EnumerateOut.model_validate(
        {
            "items": [
                {"item": "rubber buttons", "quote": "rubber buttons", "lines": [99, 6, 5, 6]},
                {"item": "made up", "quote": "not said", "lines": [42]},
            ]
        }
    )
    a, b = E.candidates(out, chunk, 0, chunk)
    assert a.lines == (6, 5) and a.speaker == "Me" and a.found
    assert b.lines == () and b.speaker == "?" and not b.found


def test_exact_dedupe_removes_overlap_repeats_only() -> None:
    cands = [
        _cand("Red case", "we go red", (3,), 0),
        _cand("The case will be red", "we go red", (3,), 1),  # same quote + lines (overlap)
        _cand("red case.", "other words", (9,), 1),  # same normalized item text
        _cand("Red case", "we go red", (4,), 1),  # same item text, kept? no: text repeat
        _cand("Rubber buttons", "rubber", (5,), 1),
    ]
    kept = E.exact_dedupe(cands)
    assert [c.item for c in kept] == ["Red case", "Rubber buttons"]


def test_apply_groups_repairs_the_partition() -> None:
    cands = [_cand(f"c{i}", f"q{i}", (i,)) for i in range(5)]
    items = E.apply_groups(cands, [([3, 1], "merged 1+3"), ([1, 7], "dup"), ([], "empty")])
    assert [(it.id, it.members, it.text) for it in items] == [
        ("I1", (0,), "c0"),
        ("I2", (1, 3), "merged 1+3"),
        ("I3", (2,), "c2"),
        ("I4", (4,), "c4"),
    ]
    assert items[1].quote == "q1" and items[1].quotes == ("q1", "q3")
    assert sorted(m for it in items for m in it.members) == list(range(5))


def test_render_list_drop_and_mark() -> None:
    items = E.apply_groups([_cand("A", "a"), _cand("B", "b", (1,))], [])
    ok = {"I1": True, "I2": False}
    assert E.render_list(items) == "- A\n- B"
    assert E.render_list(items, ok, drop=True) == "- A"
    assert E.render_list(items, ok) == f"- A\n- B {E.UNCONFIRMED}"
    assert E.kept_ids(items, E.LD, ok) == ["I1"] and E.kept_ids(items, E.LM, ok) == ["I1", "I2"]
    assert E.render_list([]) == "(no items found)"


def test_context_lines_window() -> None:
    lines = _lines(20)
    it = E.Item("I1", "x", "q", (5, 12), "Them", True, (0,), ("q",))
    assert [x.index for x in E.context_lines(it, lines, 1)] == [4, 5, 6, 11, 12, 13]


# -- judge mapping ----------------------------------------------------------------------------


def _jout(items: list[tuple[str, list[str], bool]]) -> E.JudgeOut:
    return E.JudgeOut(
        answer_items=[
            E.JudgedItem(id=i, text=i, gold=g, in_transcript=t, reason="") for i, g, t in items
        ]
    )


def test_parse_mapping_numbered_answer() -> None:
    out = _jout([("A2", ["G1", "G9", "G1"], True), ("A1", [], False)])
    got = E.parse_mapping(out, ["G1", "G2"], ["A1", "A2"])
    assert [(j.id, j.gold) for j in got] == [("A1", []), ("A2", ["G1"])]


@pytest.mark.parametrize(
    "items",
    [
        [("A1", [], True)],  # missing A2
        [("A1", [], True), ("A2", [], True), ("A3", [], True)],  # extra
        [("A1", [], True), ("A1", [], True)],  # duplicate
    ],
)
def test_parse_mapping_rejects_wrong_ids(items: list[tuple[str, list[str], bool]]) -> None:
    with pytest.raises(E.JudgeParseError):
        E.parse_mapping(_jout(items), ["G1"], ["A1", "A2"])


def test_parse_mapping_free_text_renumbers() -> None:
    got = E.parse_mapping(_jout([("x", ["G2"], True), ("y", [], False)]), ["G1", "G2"], None)
    assert [(j.id, j.gold) for j in got] == [("A1", ["G2"]), ("A2", [])]


def test_judge_answer_block_shapes() -> None:
    assert E.judge_answer_block("free").startswith("System answer (free text):")
    assert E.judge_answer_block([]) == "System answer: (no items)"
    assert "A1. x" in E.judge_answer_block([("A1", "x")])


# -- scoring ----------------------------------------------------------------------------------


def _judged(rows: list[tuple[str, list[str], bool]]) -> list[E.JudgedItem]:
    return list(_jout(rows).answer_items)


def test_score_math() -> None:
    judged = _judged(
        [
            ("I1", ["G1"], True),
            ("I2", ["G1", "G2"], True),  # covers two gold items
            ("I3", [], True),  # true but not in gold
            ("I4", [], False),  # wrong
        ]
    )
    s = E.score(judged, ["G1", "G2", "G3"])
    assert (s.n_items, s.covered, s.gold_hits, s.any_hits) == (4, 2, 2, 3)
    assert s.recall == pytest.approx(2 / 3)
    assert s.precision_gold == pytest.approx(0.5) and s.precision_any == pytest.approx(0.75)
    assert s.f1_gold == pytest.approx(2 * 0.5 * (2 / 3) / (0.5 + 2 / 3))
    kept = E.score(judged, ["G1", "G2", "G3"], keep=["I1", "I3"])
    assert (kept.n_items, kept.covered, kept.precision_any) == (2, 1, 1.0)


def test_score_empty_answer_has_no_precision() -> None:
    s = E.score([], ["G1"])
    assert s.precision_gold is None and s.recall == 0.0 and s.f1_any == 0.0
    assert E.score([], []).recall == 0.0


def test_mark_precision_splits_by_verdict() -> None:
    judged = _judged([("I1", ["G1"], True), ("I2", [], True), ("I3", [], False)])
    m = E.mark_precision(judged, {"I1": True, "I2": False})  # I3 missing = unconfirmed
    assert m == {
        "confirmed": {"items": 1, "gold_hits": 1, "any_hits": 1},
        "unconfirmed": {"items": 2, "gold_hits": 0, "any_hits": 1},
    }


def test_sign_test_counts_and_skips_undefined() -> None:
    a = {"q1": 0.5, "q2": 0.5, "q3": 0.5, "q4": None, "q5": 1.0}
    b = {"q1": 1.0, "q2": 0.5, "q3": 0.0, "q4": 1.0, "q5": 1.0, "q6": 1.0}
    t = E.sign_test(a, b)
    assert (t["n"], t["wins"], t["losses"], t["ties"]) == (4, 1, 1, 2)
    assert t["sign_p"] == 1.0


# -- selection --------------------------------------------------------------------------------


class _Q:
    def __init__(self, qid: str, kind: str, split: str) -> None:
        self.id, self.type, self.split = qid, kind, split


def test_list_questions_and_pilot_pick_one_per_type() -> None:
    qs = [
        _Q("a1", "action_what", "dev"),
        _Q("a2", "action_what", "dev"),
        _Q("d1", "decision", "dev"),
        _Q("p1", "problem", "dev"),
        _Q("w1", "who_said", "dev"),
        _Q("t1", "decision", "test"),
    ]
    lq = E.list_questions(qs)
    assert [q.id for q in lq] == ["a1", "a2", "d1", "p1", "t1"]
    pick = E.pilot_questions(lq, 3, seed=42)
    assert {q.type for q in pick} == {"decision", "action_what", "problem"}
    assert [q.id for q in pick] == [q.id for q in E.pilot_questions(lq, 3, seed=42)]
    assert E.pilot_questions(lq, 2, 42)[0].id in {"d1", "a1", "a2"}
    assert len(E.pilot_questions(lq, 10, 42)) == 4  # only 4 DEV list questions


def test_gold_for_keeps_sentence_order_and_roster() -> None:
    from tenor.ami.questions import AmiQuestion

    q = AmiQuestion(
        id="q", type="decision", template="t", meetings=["M"], question="?", gold_answer="x",
        gold_sentence_ids=["s2", "s1"], evidence_source="none", fact_group="q", split="dev",
        subset="recorded", seed=0,
    )  # fmt: skip
    g = E.gold_for(q, {"s1": "one", "s2": "two"}, "Roster: PM = A.")
    assert g.items == (("G1", "two"), ("G2", "one")) and g.ids == ["G1", "G2"]
    assert g.render() == "G1. two\nG2. one\n\nRoster: PM = A."


# -- end to end with a scripted backend -------------------------------------------------------


class Scripted:
    """Enumerate: one candidate per chunk line containing "decide" (quote = the line's text,
    plus one invented quote per chunk). Merge: singletons. Verify: invented quotes fail.
    Judge: items whose text mentions "red" state G1; "invented" items are false."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls.append(request.prompt_id)
        u = request.user
        if request.prompt_id == E.ENUMERATE_PROMPT:
            rows = re.findall(r"^\[L(\d{4}) \w+ [\d:]*\] (.*)$", u, flags=re.M)
            items = [
                {"item": f"Decided: {t}", "quote": t, "lines": [int(i)]}
                for i, t in rows
                if "decide" in t
            ]
            items.append({"item": "invented thing", "quote": "never said", "lines": []})
            out: Any = {"items": items}
        elif request.prompt_id == E.MERGE_PROMPT:
            n = len(re.findall(r"^\d+\. ", u.split("Candidates:\n", 1)[1], flags=re.M))
            out = {"groups": [{"members": [i], "item": ""} for i in range(n)]}
        elif request.prompt_id == E.VERIFY_PROMPT:
            ok = "never said" not in u
            out = {"reasoning": "r", "verdict": "supported" if ok else "not_supported"}
        else:
            block = u.split("System answer", 1)[1]
            rows = re.findall(r"^(A\d+)\. (.*)$", block, flags=re.M)
            if not rows:  # free text: one item per sentence
                rows = [(f"A{i}", t) for i, t in enumerate(block.split(":\n", 1)[1].split(". "))]
            out = {
                "answer_items": [
                    {
                        "id": i,
                        "text": t,
                        "gold": ["G1"] if "red" in t else [],
                        "in_transcript": "invented" not in t,
                        "reason": "",
                    }
                    for i, t in rows
                ]
            }
        return Completion(json.dumps(out), model, 1000, 100, "end_turn")


def _ctx() -> E.Context:
    texts = ["hello", "we decide the case is red", "filler " * 20, "we decide on buttons", "bye"]
    transcript = [E.Line(i, "Them", f"00:{i:02d}", t) for i, t in enumerate(texts)]
    ref = [E.Line(0, "Project Manager", "00:00", "case red, buttons")]
    gold = E.Gold((("G1", "The case is red."), ("G2", "Rubber buttons.")), "Roster.")
    return E.Context("q1", "What was decided?", "decision", "dev", transcript, ref, gold)


def _run(tmp: Path, chunk_chars: int = 200) -> tuple[dict[str, Any], Scripted, Ledger]:
    backend, ledger = Scripted(), Ledger()
    reader = LLMClient(backend, model="claude-opus-5-5", cache_dir=tmp / "c", ledger=ledger)
    judge = LLMClient(backend, model="claude-sonnet-5-5", cache_dir=tmp / "c", ledger=ledger)
    answer = "The case is red. Buttons are blue"
    rec = E.run_question(_ctx(), reader, judge, lambda: answer, chunk_chars=chunk_chars)
    return rec, backend, ledger


def test_run_question_end_to_end(tmp_path: Path) -> None:
    rec, backend, _ = _run(tmp_path, chunk_chars=10_000)
    assert backend.calls.count(E.ENUMERATE_PROMPT) == 1
    assert backend.calls.count(E.JUDGE_PROMPT) == 2  # N and L only
    assert len(rec["items"]) == 3 and rec["confirmed"] == {"I1": True, "I2": True, "I3": False}
    s = rec["scores"]
    assert s["L"]["n_items"] == 3 and s["LD"]["n_items"] == 2 and s["LM"]["n_items"] == 3
    assert s["L"]["recall"] == s["LD"]["recall"] == 0.5
    assert s["LD"]["precision_any"] == 1.0 and s["L"]["precision_any"] == pytest.approx(2 / 3)
    assert rec["marks"]["unconfirmed"] == {"items": 1, "gold_hits": 0, "any_hits": 0}
    assert rec["answers"]["LM"].endswith(E.UNCONFIRMED)
    assert s["N"]["n_items"] == 2 and s["N"]["recall"] == 0.5
    summary = E.summarize([rec])
    assert summary["conditions"]["L"]["n_questions"] == 1
    assert summary["items_quote_found"] == {"found": 2, "items": 3}
    assert set(summary["paired"]) == {"L vs N", "LD vs L", "LM vs L", "LM vs LD"}


def test_run_is_deterministic_and_replays_from_cache(tmp_path: Path) -> None:
    rec1, _, ledger1 = _run(tmp_path)
    rec2, _, ledger2 = _run(tmp_path)  # same cache dir: every call replays
    assert json.dumps(rec1, sort_keys=True) == json.dumps(rec2, sort_keys=True)
    assert ledger1.spent_usd() > 0 and ledger2.spent_usd() == 0
    E.write_json(tmp_path / "a.json", E.summarize([rec1]))
    E.write_json(tmp_path / "b.json", E.summarize([rec2]))
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_overlapping_chunks_do_not_double_list(tmp_path: Path) -> None:
    rec, backend, _ = _run(tmp_path, chunk_chars=120)
    assert backend.calls.count(E.ENUMERATE_PROMPT) > 1
    texts = [it["text"] for it in rec["items"]]
    assert len(texts) == len(set(texts))
    assert sum("decide the case" in t for t in texts) == 1


# -- quote and re-pricing ---------------------------------------------------------------------


def test_quote_prices_every_stage_and_skips_cached(tmp_path: Path) -> None:
    reader = LLMClient(model="claude-opus-5-5", cache_dir=tmp_path, offline=True)
    judge = LLMClient(model="claude-sonnet-5-5", cache_dir=tmp_path, offline=True)

    def count(texts: list[str]) -> list[int]:
        return [len(t) // 4 for t in texts]

    lines = E.quote([_ctx()], reader, judge, count, count, chunk_chars=200)
    stages = [x.stage.split(" (")[0] for x in lines]
    assert stages == ["L map", "L merge", "LD/LM verify", "judge"]
    assert all(0 < x.usd_expected <= x.usd_high for x in lines)
    assert lines[0].exact and not lines[1].exact
    md = E.quote_markdown([("t", lines)], "test")
    assert "**total**" in md
    only_n = E.quote([_ctx()], reader, judge, count, count, conditions=[E.N])
    assert [x.stage for x in only_n] == [f"judge ({E.JUDGE_PROMPT})"]  # no N requests passed


def test_reprice_scales_by_chunks_reference_and_questions() -> None:
    c = _ctx()
    big = E.Context(**{**c.__dict__, "transcript": c.transcript * 4, "reference": c.reference * 3})
    ledger = [
        {"stage": "e1.enumerate", "cost_usd": 0.02},
        {"stage": "e1.judge", "cost_usd": 0.01},
        {"stage": "eval.notes_only", "cost_usd": 0.01},
        {"stage": "other", "cost_usd": 0.01},
    ]
    r = E.reprice(ledger, [c], [c, big], chunk_chars=200, free_stages=["eval.notes_only"])
    pc = len(E.chunk_lines(c.transcript, 200))
    fc = pc + len(E.chunk_lines(big.transcript, 200))
    st = r["stages"]
    assert st["e1.enumerate"]["full_usd"] == pytest.approx(0.02 * fc / pc)
    assert st["e1.judge"]["full_usd"] == pytest.approx(0.01 * 4)  # 1 + 3 references
    assert st["eval.notes_only"]["full_usd"] == 0
    assert st["other"]["full_usd"] == pytest.approx(0.02)  # by questions
    assert r["full_usd_with_margin"] == pytest.approx(E.MARGIN * r["full_usd"])


def test_quote_is_exact_and_free_after_a_cached_run(tmp_path: Path) -> None:
    _run(tmp_path)  # fills the cache (chunk_chars=200)
    reader = LLMClient(model="claude-opus-5-5", cache_dir=tmp_path / "c", offline=True)
    judge = LLMClient(model="claude-sonnet-5-5", cache_dir=tmp_path / "c", offline=True)

    def count(texts: list[str]) -> list[int]:
        return [len(t) // 4 for t in texts]

    texts = {"q1": "The case is red. Buttons are blue"}
    lines = E.quote([_ctx()], reader, judge, count, count, chunk_chars=200, notes_texts=texts)
    assert all(x.exact and x.usd_high == 0 and x.calls == 0 for x in lines)
    assert [x.cached for x in lines] == [len(E.chunk_lines(_ctx().transcript, 200)), 1, 3, 2]


def test_chat_condition_is_judged_as_free_text_and_aggregate_is_committable(
    tmp_path: Path,
) -> None:
    from tenor.blackbox.kit import check_committable

    backend, ledger = Scripted(), Ledger()
    reader = LLMClient(backend, model="claude-opus-5-5", cache_dir=tmp_path, ledger=ledger)
    judge = LLMClient(backend, model="claude-sonnet-5-5", cache_dir=tmp_path, ledger=ledger)
    rec = E.run_question(
        _ctx(), reader, judge, lambda: "Buttons are blue", chat_answer="The case is red. invented"
    )
    assert backend.calls.count(E.JUDGE_PROMPT) == 3  # C, N, L
    assert rec["scores"]["C"]["recall"] == 0.5 and rec["scores"]["C"]["n_items"] == 2
    summary = E.summarize([rec, {**rec, "qid": "q2"}], seed=1)
    assert list(summary["paired"])[:2] == ["L vs C", "LD vs C"]
    assert summary["conditions"]["C"]["ci_recall"] == (0.5, 0.5)
    assert summary["by_category"]["decision"]["L"]["n_questions"] == 2
    assert summary["lm_marks"]["unconfirmed"]["ci_precision_any"] == E.wilson_ci(0, 2)
    result = {"meta": {"date": "d", "questions": ["q1", "q2"], "ledger_by_stage": {}}}
    agg = E.aggregate({**result, "summary": summary})
    assert agg["meta"]["n_questions"] == 2 and "questions" not in agg["meta"]
    check_committable(agg, E.private_texts([rec], ["What was decided?"]))


def test_wilson_and_bootstrap_ci() -> None:
    assert E.wilson_ci(0, 0) is None
    lo, hi = E.wilson_ci(14, 15)  # type: ignore[misc]
    assert 0.68 < lo < 0.71 and 0.98 < hi <= 1.0
    assert E.bootstrap_ci([], 0) is None
    ci = E.bootstrap_ci([0.0, 1.0] * 10, seed=3)
    assert ci == E.bootstrap_ci([0.0, 1.0] * 10, seed=3)
    assert ci is not None and ci[0] < 0.5 < ci[1]


def test_staged_run_caches_map_then_merge(tmp_path: Path) -> None:
    backend, ledger = Scripted(), Ledger()
    reader = LLMClient(backend, model="claude-opus-5-5", cache_dir=tmp_path, ledger=ledger)
    E.run_stage(reader, _ctx(), "map", chunk_chars=120)
    assert set(backend.calls) == {E.ENUMERATE_PROMPT}
    E.run_stage(reader, _ctx(), "merge", chunk_chars=120)
    assert backend.calls.count(E.MERGE_PROMPT) == 1
    assert backend.calls.count(E.ENUMERATE_PROMPT) == len(E.chunk_lines(_ctx().transcript, 120))
    with pytest.raises(ValueError):
        E.run_stage(reader, _ctx(), "verify")

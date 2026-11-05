"""AMI question builder and the evidence-compression harness, on hand-made meetings (no network,
no corpus files except the last test, which skips without the AMI zip)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from concentrate.ami.corpus import (
    DEFAULT_ZIP,
    AmiMeeting,
    DialogueAct,
    Speaker,
    SummarySentence,
    Word,
)
from concentrate.ami.questions import (
    QUESTION_MEETINGS,
    build_questions,
    closed_book_prompts,
    load_questions,
    roles_in,
    split_subject,
    write_jsonl,
)
from concentrate.evals import compression as C
from concentrate.llm.client import Completion, LLMClient, LLMRequest, TurnCompletion, TurnRequest

ROLES = [("A", "PM", "P1"), ("B", "UI", "P2"), ("C", "ID", "P3"), ("D", "ME", "P4")]


def _meeting(
    mid: str, letters: dict[str, str], sentences: list[tuple[str, str, list[int]]]
) -> AmiMeeting:
    """Four speakers; ``letters`` maps participant -> NXT letter (it changes between sessions).
    Each speaker says 6 acts of 12 words; ``sentences`` are (section, text, linked act indices)."""
    speakers = tuple(
        Speaker(agent=letters[p], role=r, participant=p, channel=i)
        for i, (_, r, p) in enumerate(ROLES)
    )
    acts, words = [], []
    t = 0.0
    for k in range(24):
        sp = speakers[k % 4]
        text = " ".join(f"{mid.lower()}w{k}x{j}" for j in range(12))
        acts.append(
            DialogueAct(
                da_id=f"{mid}.{sp.agent}.da{k}", agent=sp.agent, start=t, end=t + 3, text=text
            )
        )
        words += [
            Word(agent=sp.agent, start=t + j * 0.2, end=t + j * 0.2 + 0.1, text=w)
            for j, w in enumerate(text.split())
        ]
        t += 4
    sents = tuple(
        SummarySentence(
            sentence_id=f"{mid}.s.{i}",
            section=sec,
            text=txt,
            links=tuple(acts[j].da_id for j in links),
        )
        for i, (sec, txt, links) in enumerate(sentences)
    )
    summary: dict[str, tuple[str, ...]] = {}
    for s in sents:
        summary[s.section] = (*summary.get(s.section, ()), s.text)
    return AmiMeeting(
        meeting_id=mid,
        type="scenario",
        duration_s=100,
        speakers=speakers,
        words=tuple(words),
        summary=summary,
        dialogue_acts=tuple(acts),
        sentences=sents,
        has_summlinks=True,
    )


A = _meeting(
    "IS1008a",
    {"P1": "A", "P2": "B", "P3": "C", "P4": "D"},
    [
        ("abstract", "The group met.", [0]),
        ("decisions", "The group will discuss the plans at the next meeting.", [1]),
        ("decisions", "The remote will have a locator function and voice recognition.", [2, 3]),
        ("problems", "There was a lack of information.", [4]),
        ("actions", "The Marketing Expert will prepare a sales plan.", [5]),
        ("actions", "The Project Manager will negotiate the cost of production.", [6]),
        ("actions", "He will also present the plan to management.", [7]),
        ("actions", "The group will evaluate the product.", [8]),
    ],
)
B = _meeting(
    "IS1008b",
    {"P1": "A", "P2": "C", "P3": "B", "P4": "D"},  # UI and ID swap letters, as in IS1008
    [
        ("abstract", "The Marketing Expert presented an initial sales plan.", [9]),
        (
            "decisions",
            "The remote will have a locator function, voice recognition and a wood case.",
            [10],
        ),
        ("actions", "The User Interface Designer will design the prototype.", [11]),
    ],
)
EMPTY = _meeting("EN2002b", {"P1": "A", "P2": "B", "P3": "C", "P4": "D"}, [])


def test_subject_parsing() -> None:
    assert split_subject("The Marketing Expert will prepare a sales plan.") == (
        "The Marketing Expert",
        "prepare a sales plan",
    )
    assert split_subject("He will also present it.") == ("He", "present it")
    assert split_subject("No verb here.") is None
    assert roles_in("the industrial designer and the user interface designer") == [
        "Industrial Designer",
        "User Interface Designer",
    ]


def test_questions_templates_and_gold() -> None:
    qs = build_questions([A, B, EMPTY], seed=42)
    by = {q.id: q for q in qs}
    assert not any("EN2002b" in q.meetings for q in qs)  # no summary, no questions
    who = [q for q in qs if q.type == "action_who" and q.meetings == ["IS1008a"]]
    assert [q.gold_answer for q in who] == [
        "The Marketing Expert.",
        "The Project Manager.",
        "The Project Manager.",  # "He will also..." resolves to the previous subject
    ]
    assert "present the plan to management" in who[2].question
    assert all("evaluate the product" not in q.question for q in who)  # group: no who-question
    pm = next(q for q in qs if q.type == "action_what" and "Project Manager" in q.question)
    assert pm.gold_sentence_ids == ["IS1008a.s.5", "IS1008a.s.6"]
    assert who[0].fact_group == by["ami-IS1008a-action_what-01"].fact_group
    dec = [q for q in qs if q.type == "decision" and q.meetings == ["IS1008a"]]
    assert {q.question for q in dec} == {
        "In meeting IS1008a, what did the group decide?",
        "In meeting IS1008a, what was decided about the remote?",
    }
    assert dec[1].gold_evidence == ["IS1008a.C.da2", "IS1008a.D.da3"]
    assert all(q.notes_can_answer is None for q in qs)
    assert {q.split for q in qs if q.meetings == ["IS1008a"]} == {"dev"}
    assert {q.split for q in qs if "IS1008b" in q.meetings} == {"test"}


def test_who_said_keys_on_participant_not_letter() -> None:
    qs = [q for q in build_questions([A, B], seed=1) if q.type == "who_said"]
    assert qs
    for q in qs:
        m = A if q.meetings == ["IS1008a"] else B
        (da,) = q.gold_evidence
        act = next(a for a in m.dialogue_acts if a.da_id == da)
        assert act.text in q.question
        sp = next(s for s in m.speakers if s.agent == act.agent)
        assert sp.participant in q.gold_answer
    # in IS1008b letter C is P2 (UI), not the ID it was in IS1008a
    b_c = [q for q in qs if q.meetings == ["IS1008b"] and ".C." in q.gold_evidence[0]]
    assert all("User Interface Designer" in q.gold_answer for q in b_c)


def test_cross_meeting_questions() -> None:
    qs = [q for q in build_questions([A, B], seed=42) if q.type == "what_changed"]
    follow = next(q for q in qs if q.template.endswith("follow_up"))
    assert follow.meetings == ["IS1008a", "IS1008b"]
    assert "IS1008a: The Marketing Expert will prepare" in follow.gold_answer
    assert "IS1008b: The Marketing Expert presented" in follow.gold_answer
    change = next(q for q in qs if q.template.endswith("what_changed.decision"))
    assert "recognition" in change.question and "locator" in change.question


def test_questions_deterministic(tmp_path: Path) -> None:
    for seed in (3, 3):
        write_jsonl(build_questions([A, B], seed), tmp_path / f"q{seed}.jsonl")
    one = (tmp_path / "q3.jsonl").read_bytes()
    write_jsonl(build_questions([A, B], 3), tmp_path / "again.jsonl")
    assert (tmp_path / "again.jsonl").read_bytes() == one
    assert [q.id for q in load_questions(tmp_path / "q3.jsonl")] == [
        q.id for q in build_questions([A, B], 3)
    ]


def test_closed_book_has_no_meeting() -> None:
    qs = build_questions([A, B], 42)
    prompts = closed_book_prompts(qs, "sys")
    assert [p.qid for p in prompts] == [q.id for q in qs]
    assert all("w0x" not in p.user and "Notes" not in p.user for p in prompts)


def test_verbatim_share() -> None:
    src = (
        "[x:0001] Them (PM): we will ship the remote in march\n"
        "[x:0002] Them (ME): it costs twelve euros"
    )
    assert C.verbatim_share("[x:0002] Them (ME): it costs twelve euros", src) == 1.0
    assert C.verbatim_share("The remote ships in March at twelve euros.", src) == 0.0
    half = C.verbatim_share("we will ship the remote and then something else entirely", src)
    assert half == pytest.approx(5 / 10)
    assert C.verbatim_share("", src) is None
    assert C.verbatim_share("costs twelve", src) == 1.0  # short line matched whole


def test_random_lines_budget_nested_and_seeded() -> None:
    m = C.ami_materials([A])["IS1008a"]
    tokens = m.line_tokens()
    kept = {b: C.keep_random(m, b, seed=7) for b in C.BUDGETS}
    for b, idx in kept.items():
        assert sum(tokens[i] for i in idx) <= b * sum(tokens)
    assert set(kept[0.10]) <= set(kept[0.20]) <= set(kept[0.40])
    assert C.keep_random(m, 0.2, seed=7) == kept[0.20]
    assert C.keep_random(m, 0.2, seed=8) != kept[0.20]


def test_layers_built_without_questions_and_sizes() -> None:
    mats = C.ami_materials([A, B, EMPTY])
    assert len(mats["EN2002b"].utterances) > 0 or not EMPTY.spoken()
    comp = C.zero_cost_compressors(seed=42)
    layers = C.build_layers(mats, comp)
    assert layers == C.build_layers(mats, comp)
    floor, ceil = layers[(C.FLOOR, "IS1008a")], layers[(C.CEILING, "IS1008a")]
    assert floor.kept_pct == 0 and floor.verbatim_share is None
    assert ceil.kept_pct == 1 and ceil.verbatim_share == 1.0
    qs = build_questions([A, B], 42)
    size = C.sizes(layers, qs, mats)
    assert size[C.CEILING]["evidence_recall_mean"] == 1.0
    assert size[C.FLOOR]["evidence_recall_mean"] == 0.0
    assert 0.15 < size["random_20"]["kept_pct_mean"] <= 0.2


def test_word_turns_for_meeting_without_acts() -> None:
    bare = EMPTY.model_copy(update={"dialogue_acts": ()})
    turns = C.ami_materials([bare])["EN2002b"].utterances
    assert len(turns) == 24  # speaker changes every act
    assert turns[0].text.startswith("en2002bw0x0")


class FakeReader:
    """Answers from the first kept line it sees; abstains when no line is shown."""

    def __init__(self) -> None:
        self.users: list[str] = []

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        user = str(request.messages[0]["content"])
        self.users.append(user)
        seen = re.findall(r"\[(IS1008\w:\d{4})\]", user)
        text = json.dumps(
            {
                "answer": "The Marketing Expert." if seen else "Not covered.",
                "abstain": not seen,
                "citations": seen[:1],
            }
        )
        return TurnCompletion([{"type": "text", "text": text}], text, "end_turn", model, 100, 20)


class FakeJudge:
    def complete(self, request: LLMRequest, model: str) -> Completion:
        ok = (
            "System answer: The Marketing Expert." in request.user
            and "Gold answer: The Marketing Expert." in request.user
        )
        text = json.dumps({"verdict": "correct" if ok else "incorrect", "reason": "r"})
        return Completion(text, model, 50, 10, "end_turn")


def test_run_compression_end_to_end(tmp_path: Path) -> None:
    mats = C.ami_materials([A, B])
    layers = C.build_layers(mats, C.zero_cost_compressors(seed=42))
    qs = [q for q in build_questions([A, B], 42) if q.type == "action_who"]
    fake = FakeReader()
    reader = LLMClient(model="claude-haiku-5-5", cache_dir=tmp_path / "r")
    judge = LLMClient(FakeJudge(), model="claude-haiku-5-5", cache_dir=tmp_path / "j")
    j2 = LLMClient(FakeJudge(), model="claude-sonnet-5-5", cache_dir=tmp_path / "j2")
    readers = [
        C.LayerReader(reader, c, mats, layers, fake) for c in (C.FLOOR, C.CEILING, "random_20")
    ]
    readers.append(C.ClosedBookReader(reader, fake))
    report = C.run_compression(
        readers, qs, judge, layers=layers, materials=mats, seed=42, budget_usd=1.0,
        out_dir=tmp_path / "out", spent=lambda: 0.0, judge2=j2,
    )  # fmt: skip
    rows = [
        json.loads(x) for x in next((tmp_path / "out").glob("*.jsonl")).read_text().splitlines()
    ]
    assert len(rows) == 4 * len(qs)
    floor = [r for r in rows if r["system"] == C.FLOOR]
    assert all(r["abstained"] and r["kept_pct"] == 0 for r in floor)
    assert all("no transcript lines kept" in u for u in fake.users[:1])
    cb = [u for u in fake.users if "<meetings>" not in u]
    assert len(cb) == len(qs)  # closed-book saw no meeting
    assert sum("judge2_verdict" in r for r in rows) == round(0.3 * len(rows))
    split = report["summary"]["dev"]["all"]  # IS1008a: the fake's answer is right for the ME
    assert split["conditions"][C.CEILING]["accuracy"] > split["conditions"][C.FLOOR]["accuracy"]
    assert "full_transcript vs notes_only" in split["paired"]
    assert report["meta"]["notes_sources"] == [C.NOTES_SOURCE_AMI]


def test_quote_prices_without_spending() -> None:
    mats = C.ami_materials([A, B])
    layers = C.build_layers(mats, C.zero_cost_compressors(seed=42))
    qs = build_questions([A, B], 42)
    seen: list[str] = []

    def counter(texts: list[str]) -> list[int]:
        seen.extend(texts)
        return [len(t.split()) for t in texts]

    full = C.quote(qs, mats, layers, counter)
    parts = {r.part for r in full}
    assert {"ingest: enhanced", "answers: real meetings", "second judge (30%)"} <= parts
    assert next(r for r in full if r.part == "answers: AMI").calls == len(qs) * len(
        C.planned_conditions()
    )
    now = C.quote(
        qs,
        mats,
        layers,
        counter,
        C.QuoteAssumptions(real_questions=0),
        C.ZERO_COST_CONDITIONS,
        optional=False,
    )
    assert not any(r.part.startswith(("ingest", "answers: real", "OPTIONAL")) for r in now)
    assert 0 < sum(r.usd() for r in now) < sum(r.usd() for r in full)
    md = C.quote_markdown(full, "test")
    assert "**total (Haiku reader)**" in md and "total incl. optional" in md
    assert C.proxy_for("salience_20") == "random_20" and C.proxy_for(C.FACTS) == "random_10"


@pytest.mark.skipif(not DEFAULT_ZIP.exists(), reason="AMI annotations zip not downloaded")
def test_committed_question_set_matches_builder(tmp_path: Path) -> None:
    from concentrate.ami.corpus import load_meeting

    write_jsonl(
        build_questions([load_meeting(m) for m in QUESTION_MEETINGS], 42), tmp_path / "q.jsonl"
    )
    assert (tmp_path / "q.jsonl").read_bytes() == Path(
        "data/ami_questions/questions.jsonl"
    ).read_bytes()

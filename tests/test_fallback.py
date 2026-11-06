"""Transcript fallback: fires only when the memory can't answer, respects as-of, retirement and the
token budget, and keeps line IDs and raw channel labels (no network: scripted fake LLM)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from tenor.agents.concentrate_system import ConcentrateSystem
from tenor.core.models import MeetingNote
from tenor.llm.client import LLMClient, TurnCompletion, TurnRequest, estimate_tokens
from tenor.memory.enhance_stub import STUB_ENHANCER
from tenor.memory.fallback import (
    TranscriptFallback,
    cited_line_ids,
    resolved_labels,
    says_not_covered,
)
from tests.memory_helpers import ScriptedLLM, completion
from tests.test_concentrate_system import DIRECTORY, A, B, C, _notes, _q, _script

PASSAGES = "<transcript_passages>"


class AbstainingLLM(ScriptedLLM):
    """Answers like ScriptedLLM, but abstains on memory-only prompts for "zebra" questions and,
    once passages are present, cites the first passage line."""

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        user = str(request.messages[0]["content"])
        if request.prompt_id != "answer.v1" or "zebra" not in user.lower():
            return super().turn(request, model)
        self.requests.append(request)
        if PASSAGES not in user:
            answer = {
                "answer": "The material doesn't mention it.",
                "abstain": True,
                "citations": [],
            }
        else:
            ids = re.findall(r"not_[A-Za-z0-9]+:\d{4}", user.split(PASSAGES, 1)[1])
            answer = {"answer": "From the transcript.", "abstain": False, "citations": ids[:1]}
        return completion(json.dumps(answer), model)


def _sys(tmp_path: Path, llm: ScriptedLLM, **kw: object) -> ConcentrateSystem:
    client = LLMClient(model="claude-opus-5-5", cache_dir=tmp_path / "llm")
    return ConcentrateSystem(
        client,
        backend=llm,
        ingest_backend=llm,
        ingest_model="claude-sonnet-5-5",
        directory_path=DIRECTORY,
        cache_root=tmp_path / "concentrate",
        enhancer=STUB_ENHANCER,
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def notes() -> list[MeetingNote]:
    return _notes()


def test_on_abstain_fires_only_when_the_memory_cannot_answer(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    llm = AbstainingLLM(_script())
    s = _sys(tmp_path, llm, fallback="on_abstain")
    s.ingest(notes)

    covered = s.run(_q("What is the current price for Northwind Teams?", "2026-10-24"))
    assert covered.llm_calls == 1 and covered.extra["fallback_fired"] is False
    assert covered.extra["fallback_extra_cost_usd"] == 0
    assert PASSAGES not in str(s.turn_request(_q("What is the price?", "2026-10-24")).messages)

    missed = s.run(_q("Who mentioned the zebra cutover script?", "2026-10-24"))
    assert missed.llm_calls == 2 and missed.extra["fallback_fired"] is True
    assert missed.extra["fallback_extra_input_tokens"] > 0
    assert missed.answer.abstain is False
    assert missed.answer.citations and missed.answer.citations[0].startswith("not_")


def test_off_is_unchanged_and_always_appends_passages(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    llm = AbstainingLLM(_script())
    off = _sys(tmp_path, llm)
    off.ingest(notes)
    r = off.run(_q("Who mentioned the zebra cutover script?", "2026-10-24"))
    assert r.llm_calls == 1 and r.answer.abstain and r.extra == {}

    always = _sys(tmp_path, llm, fallback="always", fallback_tokens=400)
    always.ingest(notes)
    q = _q("Who mentioned the zebra cutover script?", "2026-10-24")
    assert PASSAGES in str(always.turn_request(q).messages[0]["content"])
    r = always.run(q)
    assert r.llm_calls == 1 and r.extra["fallback_fired"] is True and not r.answer.abstain


def test_bad_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fallback"):
        _sys(tmp_path, ScriptedLLM({}), fallback="sometimes")


def test_passages_never_read_retired_or_future_meetings(notes: list[MeetingNote]) -> None:
    fb = TranscriptFallback(notes)
    # A (09-08) is the best match for this query, but it is retired 30 days before 10-24.
    p = fb.passages(
        "launch the team plan twenty dollars a seat", date(2026, 10, 24), retire_after_days=30
    )
    assert A not in p.meetings and not any(i.startswith(A) for i in p.line_ids)
    assert A not in p.text
    # Held after the as-of date: C (10-06) is unreadable on 09-30.
    p = fb.passages("cutover script done merged", date(2026, 9, 30))
    assert C not in p.meetings
    # Nothing readable at all -> no block.
    assert fb.passages("anything", date(2026, 9, 1)).text == ""


def test_passages_respect_the_budget_and_keep_line_ids(notes: list[MeetingNote]) -> None:
    fb = TranscriptFallback(notes)
    big = fb.passages("cutover script price seat SSO", date(2026, 10, 24), budget_tokens=5000)
    assert set(big.meetings) == {A, B, C}
    for uid in big.line_ids:
        assert f"[{uid}]" in big.text  # every shown line keeps its citable ID
    small = fb.passages("cutover script price seat SSO", date(2026, 10, 24), budget_tokens=60)
    body = small.text.split("\n\n", 1)[1]
    assert 0 < len(small.line_ids) < len(big.line_ids)
    assert estimate_tokens(body.rsplit("\n</transcript_passages>", 1)[0]) <= 60 + 20
    assert fb.passages("x", date(2026, 10, 24), budget_tokens=0).text == ""


def test_passages_skip_lines_the_memory_already_shows(notes: list[MeetingNote]) -> None:
    fb = TranscriptFallback(notes)
    p = fb.passages("twenty five dollars SSO", date(2026, 10, 24), skip_lines={f"{B}:0000"})
    assert f"{B}:0000" not in p.line_ids and f"{B}:0001" in p.line_ids


def test_raw_channel_labels_are_kept_next_to_enhance_guesses(notes: list[MeetingNote]) -> None:
    resolved = resolved_labels(
        {
            f"{A}:0002": f"[{A}:0002] Sam Patel (Backend Engineer): Sure, I'll have it done.",
            f"{A}:0000": f"[{A}:0000] Them (Marcus Chen): Let's launch.",
            f"{A}:0003": f"[{A}:0003] Them (? Sam Patel|Kofi Mensah): Maybe.",
        }
    )
    fb = TranscriptFallback(notes, resolved=resolved)
    lines = {u.utterance_id: fb.line(u) for m in notes for u in m.utterances}
    assert lines[f"{A}:0002"].startswith(
        f"[{A}:0002] Them, Enhance guess: Sam Patel (Backend Engineer): Sure"
    )
    assert lines[f"{A}:0000"].startswith(f"[{A}:0000] Them (Marcus Chen): Let's")  # no dup
    assert lines[f"{A}:0003"].startswith(f"[{A}:0003] Them: Maybe")  # unresolved: raw only


def test_cited_line_ids_cover_api_and_mcp_ids() -> None:
    uuid = "00000000-0000-4000-8000-0000000000aa"
    text = f"lines [{A}:0001] and [{uuid}:0123]; meeting-level [{A}] is not a line"
    assert cited_line_ids(text) == {f"{A}:0001", f"{uuid}:0123"}


@pytest.mark.parametrize(
    ("answer", "abstained", "fires"),
    [
        ("No answer.", True, True),
        ("The material doesn't mention who proposed it.", False, True),
        ("The notes do not say when it ships.", False, True),
        ("There is no record of the launch date.", False, True),
        ("Not done: there is no record of a follow-up.", False, False),
        ("I can't determine who said that.", False, True),
        ("Kofi Mensah volunteered to own it [not_x:0001].", False, False),
        ("The price is $25; it isn't cheaper than before.", False, False),
    ],
)
def test_not_covered_detection(answer: str, abstained: bool, fires: bool) -> None:
    assert says_not_covered(answer, abstained) is fires

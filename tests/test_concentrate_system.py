"""ConcentrateSystem end to end with a scripted fake LLM: ingest, gate, cache/resume, answer."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from concentrate.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
from concentrate.core.models import MeetingNote
from concentrate.llm.client import LLMClient, cost_usd, turn_cache_path
from tests.memory_helpers import ScriptedLLM, extraction, fact, make_note

A, B, C = "not_AAAAAAAAAAAAAA", "not_BBBBBBBBBBBBBB", "not_CCCCCCCCCCCCCC"
DIRECTORY = (
    Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind" / "directory.json"
)


def _notes() -> list[MeetingNote]:
    return [
        make_note(
            A,
            "2026-09-08",
            "Pricing review",
            [
                ("them", "Marcus Chen", "Let's launch the team plan at twenty dollars a seat."),
                ("me", None, "Sam, can you write the cutover script by Thursday?"),
                ("them", None, "Sure, I'll have the cutover script done by Thursday."),
                ("them", None, "Maybe we should move the launch, not sure."),
            ],
        ),
        make_note(
            B,
            "2026-09-29",
            "Pricing review v2",
            [
                (
                    "them",
                    "Marcus Chen",
                    "So we put SSO in the plan and go to twenty five dollars a seat.",
                ),
                ("them", None, "Corvid and Beacon Freight both need SSO."),
            ],
        ),
        make_note(
            C,
            "2026-10-06",
            "Eng standup",
            [
                ("them", None, "The cutover script is done, merged it yesterday."),
                ("me", None, "Great."),
            ],
        ),
    ]


def _script() -> dict[str, str]:
    return {
        "Pricing review": extraction(
            facts=[
                fact(
                    "teams",
                    "price_per_seat",
                    "$20 per seat per month",
                    "20",
                    ["0000"],
                    "twenty dollars a seat",
                ),
                fact(
                    "teams",
                    "launch_date",
                    "moved",
                    "moved",
                    ["0003"],
                    "move the launch",
                    confidence=0.3,
                    kind="date",
                ),  # low confidence -> entailment
                fact(
                    "teams", "owner", "Marcus Chen", "marcus", ["0000"], "an invented quote"
                ),  # quote check fails
            ],
            commitments=[
                {
                    "owner": "sam_p",
                    "task": "write the cutover script",
                    "due": "2026-09-10",
                    "source_lines": ["0001", "0002"],
                    "quote": "cutover script done by Thursday",
                    "confidence": 0.9,
                }
            ],
            digest="Price set at $20 [0000]; Sam owns the cutover script [0002].",
        ),
        "Pricing review v2": extraction(
            facts=[
                fact(
                    "teams",
                    "price_per_seat",
                    "$25 per seat per month",
                    "25",
                    ["0000"],
                    "twenty five dollars a seat",
                    reason="SSO included",
                )
            ],
            digest="Price raised to $25 with SSO [0000].",
        ),
        "Eng standup": extraction(
            updates=[
                {
                    "commitment_id": "c1",
                    "owner": "sam_p",
                    "task": "cutover script",
                    "kind": "done",
                    "new_due": None,
                    "new_owner": None,
                    "source_lines": ["0000"],
                    "quote": "The cutover script is done",
                    "confidence": 0.95,
                }
            ],
            digest="Cutover script done [0000].",
        ),
    }


def _system(tmp_path: Path, llm: ScriptedLLM, **kw: object) -> ConcentrateSystem:
    client = LLMClient(model="claude-opus-5-5", cache_dir=tmp_path / "llm")
    return ConcentrateSystem(
        client,
        backend=llm,
        ingest_backend=llm,
        ingest_model="claude-sonnet-5-5",
        directory_path=DIRECTORY,
        cache_root=tmp_path / "concentrate",
        **kw,  # type: ignore[arg-type]
    )


def _q(text: str, as_of: str) -> SimpleNamespace:
    return SimpleNamespace(id="q", question=text, as_of=date.fromisoformat(as_of))


@pytest.fixture
def notes() -> list[MeetingNote]:
    return _notes()


def test_ingest_extracts_verifies_and_consolidates(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    llm = ScriptedLLM(_script())
    sys1 = _system(tmp_path, llm)
    stats = sys1.ingest(notes)
    d = stats.detail
    assert not stats.cached and stats.llm_calls == 4  # 3 extractions + 1 entailment
    assert llm.count("extract.v1") == 3 and llm.count("entail.v1") == 1
    assert d["facts_extracted"] == 4 and d["rejected"] == {"quote_mismatch": 1}
    assert d["kept"]["superseded"] == 1 and d["kept"]["update_done"] == 1
    assert d["entailment_checked"] == 1 and d["cache_read_tokens"] == 6000
    expected = 3 * cost_usd("claude-sonnet-5-5", 1000, 300, 2000) + cost_usd(
        "claude-haiku-5-5", 1000, 300
    )
    assert stats.cost_usd == pytest.approx(expected)
    store = sys1.store
    old, new = store.versions("teams", "price_per_seat")
    assert old.valid_to == "2026-09-29" and new.source_lines == [f"{B}:0000"]
    assert [r["reason"] for r in store.rejections()] == ["quote_mismatch"]
    (c,) = store.commitments()
    assert c.status_at("2026-09-20")["status"] == "no_follow_up"
    assert c.status_at("2026-10-06")["status"] == "done"
    tiers = store.meetings()[0]["tiers"]
    assert tiers["L0"] > 0 and tiers["L1"] > 0 and tiers["L3"] > 0
    meeting_a = store.meetings()[0]
    assert f"[{A}:0000]" in meeting_a["digest"]
    # the second meeting's prompt carried the existing key so the price could supersede
    second = next(r for r in llm.requests if "Pricing review v2" in str(r.messages[0]["content"]))
    assert 'teams.price_per_seat = "$20 per seat per month"' in str(second.messages[0]["content"])
    assert second.cache_system and "## Directory" in second.system and "sam_p" in second.system


def test_entailment_rejection_is_logged(tmp_path: Path, notes: list[MeetingNote]) -> None:
    script = _script()
    script["Pricing review"] = script["Pricing review"].replace(
        '"value": "moved"', '"value": "NOT ENTAILED"'
    )
    llm = ScriptedLLM(script)
    stats = _system(tmp_path, llm).ingest(notes)
    assert stats.detail["rejected"] == {"quote_mismatch": 1, "not_entailed": 1}


def test_ingest_is_cached_and_resumable(tmp_path: Path, notes: list[MeetingNote]) -> None:
    crashing = ScriptedLLM(_script(), fail_on="Eng standup")
    with pytest.raises(RuntimeError, match="simulated crash"):
        _system(tmp_path, crashing).ingest(notes)
    store_dir = _system(tmp_path, crashing).store_dir(notes)
    assert (store_dir / "memory.sqlite").exists() and not (store_dir / COMPLETE_MARKER).exists()

    llm = ScriptedLLM(_script())
    stats = _system(tmp_path, llm).ingest(notes)
    assert llm.count("extract.v1") == 1  # only the meeting that crashed
    assert stats.detail["meetings"] == 3 and (store_dir / COMPLETE_MARKER).exists()
    assert stats.llm_calls == 4  # totals include the earlier run's meetings

    again = ScriptedLLM(_script())
    cached = _system(tmp_path, again).ingest(notes)
    assert cached.cached and again.requests == []


def test_answer_uses_structured_lookup_with_line_ids(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    llm = ScriptedLLM(_script())
    sys1 = _system(tmp_path, llm)
    with pytest.raises(RuntimeError, match="ingest"):
        sys1.run(_q("What is the price?", "2026-10-24"))
    sys1.ingest(notes)

    q = _q("What is the current per-seat monthly price for Northwind Teams?", "2026-10-24")
    req = sys1.turn_request(q)
    user = str(req.messages[0]["content"])
    assert req.prompt_id == "answer.v1" and req.system == sys1.instructions
    assert '"$25 per seat per month" | valid 2026-09-29 -> now (CURRENT)' in user
    assert '"$20 per seat per month" | valid 2026-09-08 -> 2026-09-29 (superseded)' in user
    assert f"[{B}:0000] Them (Marcus Chen): So we put SSO" in user
    result = sys1.run(q)
    assert result.answer.citations and ":" in result.answer.citations[0]
    assert result.retrieved[0] == A and result.llm_calls == 1
    assert turn_cache_path(sys1.client, req).exists()  # run() sent exactly turn_request(q)

    early = str(
        sys1.turn_request(
            _q("As of Friday, September 25, what was the price for Northwind Teams?", "2026-09-25")
        ).messages[0]["content"]
    )
    assert '"$20 per seat per month" | valid 2026-09-08 -> now (CURRENT)' in early
    assert "$25" not in early  # not known yet on 09-25

    status = str(
        sys1.turn_request(_q("Did Sam Patel finish the cutover script?", "2026-10-24")).messages[0][
            "content"
        ]
    )
    assert "status as of 2026-10-24: done" in status and f"{C}:0000" in status


def test_retirement_drops_old_transcript_lines_but_keeps_ids(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    llm = ScriptedLLM(_script())
    sys1 = _system(tmp_path, llm, retire_after_days=30)
    sys1.ingest(notes)
    user = str(
        sys1.turn_request(
            _q("What is the current price for Northwind Teams?", "2026-10-24")
        ).messages[0]["content"]
    )
    assert f"lines [{A}:0000]" in user  # provenance pointer kept
    assert "Them (Marcus Chen): Let's launch" not in user  # A (09-08) retired
    assert "Them (Marcus Chen): So we put SSO" in user  # B (09-29) within 30 days
    assert "retired" in user


def test_estimates_are_cache_aware(tmp_path: Path, notes: list[MeetingNote]) -> None:
    sys1 = _system(tmp_path, ScriptedLLM({}))
    tokens_in, tokens_out = sys1.estimate_ingest(notes)
    assert tokens_in > 0 and tokens_out > 0
    usd = sys1.estimate_ingest_usd(notes)
    assert 0 < usd < cost_usd("claude-sonnet-5-5", tokens_in, tokens_out) + 0.01
    q = _q("What is the price?", "2026-10-24")
    assert sys1.estimate(q)[0] > 1000

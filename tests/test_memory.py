"""Memory engine units: bitemporal store, commitment lifecycle, grounding gate, extract helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from concentrate.memory.entities import load_directory
from concentrate.memory.extract import full_id, local_line, to_full_ids, windows
from concentrate.memory.schema import (
    ExtractedCommitment,
    ExtractedFact,
    Extraction,
)
from concentrate.memory.store import MemoryStore, norm_value
from concentrate.memory.verify import check_item, gate_eval, quote_supported

DIRECTORY = (
    Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind" / "directory.json"
)
N = "not_AAAAAAAAAAAAAA"


def _add(
    store: MemoryStore,
    value: str,
    norm: str,
    day: str,
    recorded: str | None = None,
    line: str = "0001",
) -> tuple[str, int]:
    return store.add_fact(
        subject="teams",
        predicate="price_per_seat",
        kind="decision",
        value=value,
        value_norm=norm,
        valid_from=day,
        recorded_at=recorded or f"{day}T10:00:00+00:00",
        note_id=N,
        source_lines=[f"{N}:{line}"],
        quote="q",
        reason=None,
        confidence=0.8,
        privacy="none",
    )


# -- store ---------------------------------------------------------------------------------------


def test_supersession_closes_old_value_and_keeps_history() -> None:
    s = MemoryStore(":memory:")
    assert _add(s, "$20 per seat", "20", "2026-09-08")[0] == "inserted"
    assert _add(s, "$25 per seat", "25", "2026-09-29")[0] == "superseded"
    old, new = s.versions("teams", "price_per_seat")
    assert (old.valid_to, old.superseded_by, new.valid_to) == ("2026-09-29", new.id, None)
    assert [f.value_norm for f in s.as_of("teams", "price_per_seat", "2026-09-15")] == ["20"]
    assert [f.value_norm for f in s.as_of("teams", "price_per_seat", "2026-10-09")] == ["25"]
    assert s.as_of("teams", "price_per_seat", "2026-09-01") == []


def test_restatement_adds_evidence_and_raises_confidence() -> None:
    s = MemoryStore(":memory:")
    _add(s, "$20 per seat", "20", "2026-09-08")
    action, fid = _add(s, "twenty dollars a seat", "$20", "2026-09-15", line="0009")
    assert action == "restated" and norm_value("$20") == norm_value("20")
    (f,) = s.versions("teams", "price_per_seat")
    assert f.id == fid and len(f.evidence) == 1 and f.confidence == pytest.approx(0.96)
    assert f.all_lines() == [f"{N}:0001", f"{N}:0009"]


def test_same_day_different_values_are_flagged_not_resolved() -> None:
    s = MemoryStore(":memory:")
    _add(s, "$20", "20", "2026-09-08")
    assert _add(s, "$22", "22", "2026-09-08")[0] == "contradiction"
    live = s.as_of("teams", "price_per_seat", "2026-09-10")
    assert len(live) == 2 and all(f.contradiction for f in live)
    assert len(s.contradictions()) == 1


def test_out_of_order_fact_is_backfilled_into_the_timeline() -> None:
    s = MemoryStore(":memory:")
    _add(s, "$25", "25", "2026-09-29")
    _add(s, "$20", "20", "2026-09-08", recorded="2026-10-01T10:00:00+00:00")
    early, late = s.versions("teams", "price_per_seat")
    assert (early.value_norm, early.valid_to, late.valid_to) == ("20", "2026-09-29", None)


def test_bitemporal_belief_as_of_transaction_time() -> None:
    """Valid on 09-30 the price was $25, but on 09-20 we still believed $20 would hold."""
    s = MemoryStore(":memory:")
    _add(s, "$20", "20", "2026-09-08")
    _add(s, "$25", "25", "2026-09-29")
    (old, _) = s.versions("teams", "price_per_seat")
    assert old.live_at("2026-10-05", known_at="2026-09-20")  # end not yet known
    assert not old.live_at("2026-10-05", known_at="2026-10-01")
    assert [
        f.value_norm for f in s.as_of("teams", "price_per_seat", "2026-10-05", "2026-09-20")
    ] == ["20"]


def test_commitment_lifecycle() -> None:
    s = MemoryStore(":memory:")
    common = {
        "note_id": N,
        "recorded_at": "2026-09-01T10:00:00+00:00",
        "quote": "q",
        "confidence": 0.9,
    }
    cid = s.add_commitment(
        owner="sam_p",
        task="cutover script",
        due="2026-09-03",
        made_on="2026-09-01",
        source_lines=[f"{N}:0002"],
        **common,
    )
    other = s.add_commitment(
        owner="ruth",
        task="device lab",
        due=None,
        made_on="2026-09-01",
        source_lines=[f"{N}:0003"],
        **common,
    )
    s.add_commitment_event(
        cid,
        kind="slipped",
        day="2026-09-08",
        recorded_at="x",
        note_id=N,
        source_lines=[f"{N}:0010"],
        quote="q",
        new_due="2026-09-11",
        new_owner=None,
    )
    s.add_commitment_event(
        cid,
        kind="done",
        day="2026-09-14",
        recorded_at="x",
        note_id=N,
        source_lines=[f"{N}:0020"],
        quote="q",
        new_due=None,
        new_owner=None,
    )
    c, o = s.commitments()
    assert c.status_at("2026-09-01")["status"] == "open"
    st = c.status_at("2026-09-10")
    assert (st["status"], st["due"]) == ("slipped", "2026-09-11")
    assert c.status_at("2026-09-20")["status"] == "done"
    assert f"{N}:0020" in c.status_at("2026-09-20")["lines"]
    assert o.id == other and o.status_at("2026-10-01")["status"] == "no_follow_up"


# -- gate ----------------------------------------------------------------------------------------

LINES = {
    f"{N}:0001": "Them (Marcus Chen): So we put SSO in the plan and go to "
    "twenty five dollars a seat.",
    f"{N}:0002": "Me: The cutover is October eighth,",
    f"{N}:0003": "Me: and Sam will have it by Thursday.",
    f"{N}:0004": "Them: Is it just me or is the office freezing today?",
}


def _fact(**kw: object) -> ExtractedFact:
    base: dict[str, object] = {
        "subject": "teams",
        "predicate": "price_per_seat",
        "kind": "decision",
        "value": "$25 per seat",
        "value_norm": "25",
        "valid_from": None,
        "source_lines": [f"{N}:0001"],
        "quote": "twenty five dollars a seat",
        "reason": None,
        "confidence": 0.9,
        "privacy": "none",
    }
    base.update(kw)
    return ExtractedFact.model_validate(base)


def test_gate_accepts_grounded_fact_with_spoken_number() -> None:
    r = check_item(_fact(), LINES)
    assert r.ok and not r.needs_entailment


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"source_lines": [f"{N}:0099"]}, "unknown_line"),
        ({"source_lines": []}, "no_source_lines"),
        ({"quote": "thirty dollars a seat"}, "quote_mismatch"),
        ({"value_norm": "30"}, "value_not_in_lines"),
        ({"valid_from": "next week"}, "malformed"),
        (
            {"value_norm": "2026-10-09", "source_lines": [f"{N}:0002"], "quote": "October eighth"},
            "value_not_in_lines",
        ),
    ],
)
def test_gate_rejections(kw: dict[str, object], reason: str) -> None:
    r = check_item(_fact(**kw), LINES)
    assert not r.ok and r.reason == reason


def test_gate_dates_quotes_and_entailment_routing() -> None:
    iso = _fact(value_norm="2026-10-08", source_lines=[f"{N}:0002"], quote="October eighth")
    assert check_item(iso, LINES).ok
    rel = _fact(value_norm="2026-09-24", source_lines=[f"{N}:0003"], quote="by Thursday")
    r = check_item(rel, LINES)
    assert r.ok and r.needs_entailment  # relative date: only an LLM can check it
    assert check_item(_fact(confidence=0.4), LINES).needs_entailment
    assert quote_supported(
        "cutover is October eighth ... Sam will have it",
        LINES[f"{N}:0002"] + " " + LINES[f"{N}:0003"],
    )
    c = ExtractedCommitment(
        owner="sam_p",
        task="cutover",
        due="Thursday",
        source_lines=[f"{N}:0003"],
        quote="Sam will have it",
        confidence=0.9,
    )
    assert check_item(c, LINES).reason == "malformed"


def test_gate_eval_injects_bad_facts() -> None:
    good = [
        _fact(),
        _fact(
            predicate="cutover_date",
            value_norm="2026-10-08",
            source_lines=[f"{N}:0002"],
            quote="October eighth",
        ),
    ]
    ev = gate_eval(good, LINES, seed=1)
    assert ev.good == 2 and ev.false_rejects == 0 and ev.precision == 1.0
    assert ev.by_corruption["fake_line"]["rejected"] == 2
    assert ev.by_corruption["wrong_number"]["rejected"] == 2
    assert ev.by_corruption["wrong_subject"]["rejected"] == 0  # deterministic layers can't see it
    assert 0.5 < ev.recall < 1.0


# -- extraction helpers / entities -----------------------------------------------------------------


def test_line_id_mapping_and_windows() -> None:
    assert full_id(N, "37") == f"{N}:0037" and full_id(N, "[0037]") == f"{N}:0037"
    assert full_id(N, f"{N}:0002") == f"{N}:0002" and full_id(N, "L7") == f"{N}:L7"
    assert local_line(f"[{N}:0005] Me: hi", f"{N}:0005", 5) == "[0005] Me: hi"
    assert windows(10, None) == [(0, 10)] and windows(10, 40) == [(0, 10)]
    spans = windows(100, 40, overlap=6)
    assert spans[0] == (0, 40) and spans[-1][1] == 100 and spans[1][0] == 34
    x = Extraction(
        facts=[_fact(source_lines=["0001", "1"])],
        commitments=[],
        commitment_updates=[],
        digest="Price up [0001]. Lab done [0002, 0003].",
    )
    y = to_full_ids(x, N)
    assert y.facts[0].source_lines == [f"{N}:0001"]
    assert y.digest == f"Price up [{N}:0001]. Lab done [{N}:0002, {N}:0003]."


def test_directory_matching_prefers_longest_and_keeps_ambiguity() -> None:
    d = load_directory(DIRECTORY)
    assert d.find("What did Beacon Freight ask for?") == ["beacon_freight"]
    assert d.find("the onboarding redesign") == ["beacon"]
    assert set(d.find("What did Sam say?")) == {"sam_p", "sam_o"}
    assert d.find("Falcon (the payments migration)") == ["falcon"]

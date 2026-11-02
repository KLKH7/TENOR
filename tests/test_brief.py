"""Pre-meeting brief: as-of queries over the memory store, gold briefs, scoring, baseline."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from concentrate.evals.brief import (
    GoldBuilder,
    Item,
    TopKBriefBaseline,
    find_lines,
    match,
    name_index,
    score_brief,
    score_section,
    shown_from_brief,
)
from concentrate.llm.client import Completion, LLMClient, LLMRequest
from concentrate.memory.brief import UpcomingMeeting, build_brief, to_markdown
from concentrate.memory.store import MemoryStore
from concentrate.synth.world import Commitment, Evidence, Fact, Meeting, World
from tests.memory_helpers import make_note

T = "T09:00:00+00:00"


def _meeting(store: MemoryStore, note_id: str, day: str, title: str = "Sync") -> None:
    store.add_meeting(note_id, title, day, day + T, f"digest of {note_id}", {}, {})


def _fact(store: MemoryStore, note_id: str, day: str, value: str, subject: str = "falcon") -> None:
    store.add_fact(
        subject=subject,
        predicate="status",
        kind="status",
        value=value,
        value_norm=value,
        valid_from=day,
        recorded_at=day + T,
        note_id=note_id,
        source_lines=[f"{note_id}:0001"],
        quote=value,
        reason=None,
        confidence=0.9,
        privacy="none",
    )


def _store() -> MemoryStore:
    s = MemoryStore(":memory:")
    _meeting(s, "n1", "2026-09-01")
    _meeting(s, "n2", "2026-09-08")
    _meeting(s, "x1", "2026-09-09", title="Other")
    _meeting(s, "n3", "2026-09-15")
    _fact(s, "n1", "2026-09-01", "on track")
    _fact(s, "x1", "2026-09-09", "at risk")  # changed after the series last met (n2)
    _fact(s, "n3", "2026-09-15", "late")  # learnt after the brief's start
    _fact(s, "x1", "2026-09-09", "prefers mornings", subject="zed")  # irrelevant subject

    def commit(owner: str, task: str, due: str, note: str, day: str) -> int:
        return s.add_commitment(
            owner=owner,
            task=task,
            due=due,
            note_id=note,
            made_on=day,
            recorded_at=day + T,
            source_lines=[f"{note}:0005"],
            quote=task,
            confidence=0.9,
        )

    def event(cid: int, kind: str, day: str, **kw: Any) -> None:
        s.add_commitment_event(
            cid,
            kind=kind,
            day=day,
            recorded_at=day + T,
            note_id="x1",
            source_lines=["x1:0009"],
            quote=kind,
            new_due=kw.get("new_due"),
            new_owner=kw.get("new_owner"),
        )

    commit("sam", "write the cutover script", "2026-09-03", "n1", "2026-09-01")  # no follow-up
    event(commit("sam", "fix the webhook", "2026-09-04", "n1", "2026-09-01"), "done", "2026-09-09")
    slip = commit("ana", "draft the runbook", "2026-09-05", "n1", "2026-09-01")
    event(slip, "slipped", "2026-09-09", new_due="2026-09-19")
    moved = commit("ana", "own the export", "2026-09-20", "n2", "2026-09-08")
    event(moved, "reassigned", "2026-09-09", new_owner="bob")  # bob is not attending
    late = commit("sam", "ship the invoice page", "2026-09-30", "n2", "2026-09-08")
    event(late, "done", "2026-09-15")  # done after the brief's start: still open then
    return s


def _upcoming() -> UpcomingMeeting:
    return UpcomingMeeting(
        title="Sync",
        start=datetime(2026, 9, 15, 9, 0, tzinfo=UTC),
        attendees=("sam", "ana"),
        note_id="n3",
    )


def test_brief_is_an_as_of_query() -> None:
    b = build_brief(_store(), _upcoming())
    assert b.last_meeting is not None and b.last_meeting.note_id == "n2"
    got = {c.task: c for c in b.commitments}
    assert set(got) == {"write the cutover script", "draft the runbook", "ship the invoice page"}
    assert got["write the cutover script"].status == "no_follow_up"
    assert got["write the cutover script"].overdue
    assert got["draft the runbook"].status == "slipped"
    assert got["draft the runbook"].due == "2026-09-19"
    assert got["ship the invoice page"].status == "open"  # its "done" came later
    assert [(c.value, c.previous) for c in b.changes] == [("at risk", "on track")]
    md = to_markdown(b, {"sam": "Sam"})
    assert "[n1:0005]" in md and "Sam: write the cutover script" in md


def test_changes_skip_commitment_updates() -> None:
    s = _store()
    _fact(s, "x1", "2026-09-09", "runbook slipped", subject="ana")
    s.db.execute("UPDATE facts SET source_lines='[\"x1:0009\"]' WHERE value='runbook slipped'")
    b = build_brief(s, _upcoming())
    assert "runbook slipped" not in [c.value for c in b.changes]


def _world() -> World:
    def meeting(key: str, nid: str, day: int, title: str, customer: str | None = None) -> Meeting:
        return Meeting(
            key=key,
            note_id=nid,
            title=title,
            kind="sync",
            week=1,
            start=datetime(2026, 9, day, 10, tzinfo=UTC),
            minutes=30,
            attendees=["priya", "sam"],
            folder="x",
            customer=customer,
        )

    ms = [
        meeting("a", "na", 1, "Acme call", "acme"),
        meeting("b", "nb", 3, "Standup"),
        meeting("c", "nc", 8, "Acme call", "acme"),
    ]

    def fact(fid: str, key: str, value: str, spoken: str, kind: str = "metric", **kw: Any) -> Fact:
        return Fact(
            id=fid,
            kind=kind,
            subject=kw.pop("subject", "acme"),
            predicate=kw.pop("predicate", "seats"),
            value=value,
            value_norm=value,
            spoken=(spoken,),
            valid_from=date(2026, 9, 1),
            evidence=[Evidence(meeting=key, beat=0)],
            bullet=value,
            **kw,
        )

    facts = [
        fact("f1", "a", "40 seats", "forty seats"),
        fact("f2", "b", "55 seats", "fifty five seats"),
        fact("f3", "a", "SSO (open)", "SSO", kind="request", predicate="request:sso"),
        fact(
            "f4",
            "a",
            "CSV (open)",
            "CSV",
            kind="request",
            predicate="request:csv",
            valid_to=date(2026, 9, 2),
        ),
    ]
    commitments = [
        Commitment(
            id="c1",
            owner="sam",
            task="send the order form",
            task_spoken="order form",
            due=date(2026, 9, 4),
            made_in="a",
            made_evidence=[Evidence(meeting="a", beat=1)],
            outcome="kept",
            resolved_in="b",
            resolved_on=date(2026, 9, 3),
        ),
        Commitment(
            id="c2",
            owner="sam",
            task="write the security memo",
            task_spoken="security memo",
            due=date(2026, 9, 10),
            made_in="b",
            made_evidence=[Evidence(meeting="b", beat=1)],
            outcome="open",
        ),
    ]
    return World(
        seed=1,
        owner="priya",
        start=date(2026, 9, 1),
        meetings=ms,
        facts=facts,
        commitments=commitments,
    )


def _notes() -> list[Any]:
    return [
        make_note(
            "na",
            "2026-09-01",
            "Acme call",
            [
                ("them", None, "We have forty seats."),
                ("me", None, "I'll send the order form."),
                ("them", None, "We need SSO."),
                ("them", None, "And CSV export."),
            ],
        ),
        make_note(
            "nb",
            "2026-09-03",
            "Standup",
            [
                ("them", None, "Acme is at fifty five seats now."),
                ("them", None, "I'll write the security memo."),
                ("them", None, "The order form went out."),
            ],
        ),
        make_note("nc", "2026-09-08", "Acme call", [("me", None, "Hi.")]),
    ]


def test_gold_brief_from_world() -> None:
    g = GoldBuilder(_world(), _notes())
    assert [m.key for m in g.targets()] == ["c"]
    gb = g.brief(g.by_key["c"])
    assert gb.previous is not None and gb.previous.note_id == "na"
    live = {c.key: c.live for c in gb.commitments}
    assert live == {"c1": False, "c2": True}
    assert [c.key for c in gb.changes] == ["f2"]  # learnt between the two Acme calls
    assert gb.changes[0].lines == ["nb:0000"]
    assert {r.key: r.live for r in gb.requests} == {"f3": True, "f4": False}
    assert find_lines(g.notes["na"], ("sso",)) == ["na:0002"]


def test_precision_recall_and_staleness() -> None:
    universe = [
        Item.of("send the order form", ["na:0001"], owner="sam", live=False),
        Item.of("write the security memo", ["nb:0001"], owner="sam"),
        Item.of("book the offsite", ["nb:0009"], owner="ana"),
    ]
    shown = [
        Item.of("Send Acme the order form", ["na:0002"], owner="sam"),  # stale (done)
        Item.of("Security memo", ["nb:0001"], owner="sam"),  # true positive
        Item.of("Plan the party", ["nc:0004"], owner="sam"),  # false positive
        Item.of("Book the offsite", ["nb:0009"], owner="sam"),  # wrong owner
    ]
    s = score_section(shown, universe)
    assert (s.shown, s.gold, s.tp, s.stale) == (4, 2, 1, 1)
    m = s.metrics()
    assert m["precision"] == 0.25 and m["recall"] == 0.5 and m["staleness"] == 0.25
    assert score_section(shown, universe, strict_owner=False).tp == 2
    assert match([], universe) == {}


class _FakeBackend:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload, self.calls = payload, 0

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls += 1
        assert "fifty five seats" in request.user  # retrieved context is in the prompt
        assert "Hi." not in request.user  # nothing from the meeting itself or later
        return Completion(json.dumps(self.payload), model, 100, 50, "end_turn")


def test_topk_baseline_with_fake_llm(tmp_path: Any) -> None:
    payload = {
        "last_meeting_note_id": "na",
        "commitments": [
            {
                "owner": "Sam Patel",
                "task": "security memo",
                "status": "open",
                "due": "",
                "lines": ["nb:0001"],
            }
        ],
        "changes": [{"what": "Acme has 55 seats", "previous": "40", "lines": ["nb:0000"]}],
        "requests": [{"request": "SSO", "lines": ["na:0002"]}],
    }
    backend = _FakeBackend(payload)
    client = LLMClient(backend, model="claude-sonnet-5-5", cache_dir=tmp_path)
    names, ids = name_index({"people": [{"id": "sam", "name": "Sam Patel"}], "orgs": []})
    g = GoldBuilder(_world(), _notes())
    base = TopKBriefBaseline(client, _notes(), names, embedder=None, k=4)
    target = g.by_key["c"]
    assert base.estimate_usd(g.upcoming(target)) > 0
    _, shown = base.run(g.upcoming(target), ids)
    scores = score_brief(shown, g.brief(target))
    assert scores["last_meeting_ok"]
    assert scores["commitments"].tp == 1 and scores["changes"].tp == 1
    assert scores["requests"].tp == 1
    base.run(g.upcoming(target), ids)
    assert backend.calls == 1  # second run is a cache hit


def test_memory_brief_scores_against_gold() -> None:
    s = MemoryStore(":memory:")
    s.add_meeting("na", "Acme call", "2026-09-01", "2026-09-01T10:00:00+00:00", "d", {}, {})
    cid = s.add_commitment(
        owner="sam",
        task="Write the security memo",
        due="2026-09-10",
        note_id="nb",
        made_on="2026-09-03",
        recorded_at="2026-09-03T10:00:00+00:00",
        source_lines=["nb:0001"],
        quote="",
        confidence=1.0,
    )
    assert cid
    g = GoldBuilder(_world(), _notes())
    target = g.by_key["c"]
    shown = shown_from_brief(build_brief(s, g.upcoming(target)))
    scores = score_brief(shown, g.brief(target))
    assert scores["commitments"].tp == 1 and scores["last_meeting_ok"]

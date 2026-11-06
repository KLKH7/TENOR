"""Behaviour of GranolaAPISource against the fake server (no network)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from tenor.sources.base import NoteNotReady
from tenor.sources.granola_api import GranolaAPIError, GranolaAPISource
from tenor.sources.ratelimit import FakeClock
from tests.conftest import many_notes
from tests.fake_granola import API_KEY, FakeGranola

Make = Callable[..., GranolaAPISource]


def test_pagination_follows_cursor_with_max_page_size(
    server: FakeGranola, make_client: Make
) -> None:
    server.notes = many_notes(70)
    ids = [n.id for n in make_client().list_notes()]
    assert sorted(ids) == sorted(server.notes)
    assert len(ids) == len(set(ids)) == 70
    list_calls = [r for r in server.requests if r.url.path == "/v1/notes"]
    assert len(list_calls) == 3  # 30 + 30 + 10
    assert all(r.url.params["page_size"] == "30" for r in list_calls)
    assert [r.url.params.get("cursor") for r in list_calls] == [None, "30", "60"]


def test_folders_paginate(server: FakeGranola, make_client: Make) -> None:
    folders = list(make_client().list_folders())
    assert {f.id for f in folders} == {f["id"] for f in server.folders}


def test_updated_after_is_sent_as_utc_iso(server: FakeGranola, make_client: Make) -> None:
    since = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    ids = {n.id for n in make_client().list_notes(updated_after=since)}
    assert ids == {"not_SmplAcmeCall01", "not_SmplInPerson01"}
    assert server.requests[-1].url.params["updated_after"] == "2026-09-08T00:00:00Z"


def test_naive_datetimes_are_rejected(make_client: Make) -> None:
    with pytest.raises(ValueError):
        list(make_client().list_notes(updated_after=datetime(2026, 9, 8)))


def test_client_never_trips_server_rate_limit(
    server: FakeGranola, make_client: Make, clock: FakeClock
) -> None:
    server.notes = many_notes(60)
    client = make_client()
    for summary in list(client.list_notes()):
        client.get_note(summary.id)
    assert server.rate_limited == 0
    # 2 list pages + 60 gets = 62 requests: 25 burst, then 5/s → >= (62-25)/5 s simulated.
    assert len(server.requests) == 62
    assert clock.now() >= (62 - 25) / 5 - 1e-9


def test_429_honours_retry_after(server: FakeGranola, make_client: Make, clock: FakeClock) -> None:
    server.force_429, server.retry_after = 2, "7"
    note = make_client().get_note("not_SmplStandup001")
    assert note.id == "not_SmplStandup001"
    assert clock.sleeps.count(7.0) == 2


def test_429_without_retry_after_uses_backoff(
    server: FakeGranola, make_client: Make, clock: FakeClock
) -> None:
    server.force_429, server.retry_after = 3, None
    make_client().get_note("not_SmplStandup001")
    assert len(clock.sleeps) >= 3
    assert sum(clock.sleeps) > 0


def test_persistent_429_eventually_raises(server: FakeGranola, make_client: Make) -> None:
    server.force_429 = 100
    with pytest.raises(GranolaAPIError) as exc:
        make_client(max_retries=3).get_note("not_SmplStandup001")
    assert exc.value.status == 429


def test_404_while_processing_is_retried_then_succeeds(
    server: FakeGranola, make_client: Make
) -> None:
    server.get_404s["not_SmplAcmeCall01"] = 2
    note = make_client(not_ready_retries=3).get_note("not_SmplAcmeCall01")
    assert note.transcript and len(note.transcript) == 3


def test_404_after_retries_raises_not_ready(server: FakeGranola, make_client: Make) -> None:
    server.processing.add("not_SmplAcmeCall01")
    with pytest.raises(NoteNotReady):
        make_client(not_ready_retries=2).get_note("not_SmplAcmeCall01")
    gets = [r for r in server.requests if r.url.path == "/v1/notes/not_SmplAcmeCall01"]
    assert len(gets) == 3


def test_processing_notes_are_excluded_from_list(server: FakeGranola, make_client: Make) -> None:
    server.processing.add("not_SmplAcmeCall01")
    assert "not_SmplAcmeCall01" not in {n.id for n in make_client().list_notes()}


def test_413_falls_back_to_paginated_transcript(server: FakeGranola, make_client: Make) -> None:
    big = many_notes(1)
    nid, note = next(iter(big.items()))
    note["transcript"] = note["transcript"] * (240 // len(note["transcript"]))  # 3 pages
    server.notes = big
    server.too_large.add(nid)
    fetched = make_client().get_note(nid)
    assert fetched.transcript is not None and len(fetched.transcript) == 240
    pages = [r for r in server.requests if r.url.path.endswith("/transcript")]
    assert len(pages) == 3
    assert all(r.url.params["page_size"] == "100" for r in pages)


def test_unauthorized_raises_without_leaking_key(server: FakeGranola, clock: FakeClock) -> None:
    client = GranolaAPISource("grn_wrong", transport=server.transport(), clock=clock)
    with pytest.raises(GranolaAPIError) as exc:
        list(client.list_notes())
    assert exc.value.status == 401
    assert "grn_" not in str(exc.value)


def test_api_key_never_appears_in_repr_or_logs(
    server: FakeGranola, make_client: Make, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server.force_429 = 1
    client = make_client()
    list(client.list_notes())
    client.get_note("not_SmplStandup001")
    assert API_KEY not in repr(client)
    assert API_KEY not in caplog.text
    assert caplog.records, "expected some debug logging to have been captured"


def test_missing_api_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    with pytest.raises(Exception, match="GRANOLA_API_KEY"):
        GranolaAPISource()

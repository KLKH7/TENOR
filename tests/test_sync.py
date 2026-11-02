"""Sync behaviour, and equivalence of the two Source implementations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from concentrate.core.models import Channel
from concentrate.core.store import NoteStore
from concentrate.sources.fixtures import FixtureSource
from concentrate.sources.granola_api import GranolaAPISource
from concentrate.sources.poller import sync
from tests.conftest import SAMPLE
from tests.fake_granola import FakeGranola

Make = Callable[..., GranolaAPISource]
FIXED_NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _dump(store: NoteStore) -> list[str]:
    return [n.model_dump_json() for n in store.iter_notes(include_deleted=True)]


def test_api_and_fixture_sources_produce_identical_stores(make_client: Make) -> None:
    a, b = NoteStore(":memory:"), NoteStore(":memory:")
    sync(FixtureSource(SAMPLE), a, now=lambda: FIXED_NOW)
    sync(make_client(), b, now=lambda: FIXED_NOW)
    assert _dump(a) == _dump(b)
    assert a.counts() == b.counts()


def test_normalization_maps_channels_and_ids() -> None:
    store = NoteStore(":memory:")
    sync(FixtureSource(SAMPLE), store, now=lambda: FIXED_NOW)
    standup = store.get_note("not_SmplStandup001")
    assert standup is not None
    assert [u.channel for u in standup.utterances] == [
        Channel.ME,
        Channel.THEM,
        Channel.THEM,
        Channel.ME,
    ]
    assert standup.utterances[1].utterance_id == "not_SmplStandup001:0001"
    assert standup.private_notes_markdown == "falcon ok\nsam - script thurs"
    in_person = store.get_note("not_SmplInPerson01")
    assert in_person is not None and in_person.event_title is None
    # In-person: both people on the one microphone → both "me" by channel, despite diarization.
    assert {u.channel for u in in_person.utterances} == {Channel.ME}
    assert [u.diarization_label for u in in_person.utterances] == ["Speaker A", "Speaker B"]


def test_incremental_sync_skips_unchanged_and_refetches_updated(
    server: FakeGranola, make_client: Make
) -> None:
    store = NoteStore(":memory:")
    first = sync(make_client(), store, now=lambda: FIXED_NOW)
    assert first.fetched == 3
    assert first.watermark == datetime(2026, 9, 9, 11, 30, tzinfo=UTC)

    second = sync(make_client(), store, now=lambda: FIXED_NOW)
    assert second.fetched == 0
    since = [r for r in server.requests if r.url.path == "/v1/notes"][-1].url.params
    assert since["updated_after"] == "2026-09-09T11:20:00Z"  # watermark - 10 min overlap

    server.notes["not_SmplStandup001"] = {
        **server.notes["not_SmplStandup001"],
        "updated_at": "2026-09-10T08:00:00Z",
        "summary_markdown": "edited",
    }
    third = sync(make_client(), store, now=lambda: FIXED_NOW)
    assert third.fetched == 1
    note = store.get_note("not_SmplStandup001")
    assert note is not None and note.summary_markdown == "edited"
    assert third.watermark == datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


def test_not_ready_notes_go_pending_and_recover(server: FakeGranola, make_client: Make) -> None:
    store = NoteStore(":memory:")
    server.get_404s["not_SmplAcmeCall01"] = 100  # listed, but Get keeps 404ing
    report = sync(make_client(not_ready_retries=1), store, now=lambda: FIXED_NOW)
    assert report.pending == ["not_SmplAcmeCall01"]
    assert store.get_note("not_SmplAcmeCall01") is None

    server.get_404s.clear()
    # Even if the note's updated_at is now behind the watermark, pending notes are retried.
    report = sync(make_client(), store, now=lambda: FIXED_NOW)
    assert report.pending == []
    assert store.get_note("not_SmplAcmeCall01") is not None
    assert store.pending("granola") == []


def test_soft_deleted_notes_are_marked_and_transcript_dropped(
    server: FakeGranola, make_client: Make
) -> None:
    store = NoteStore(":memory:")
    sync(make_client(), store, now=lambda: FIXED_NOW)
    server.notes["not_SmplInPerson01"] = {
        **server.notes["not_SmplInPerson01"],
        "updated_at": "2026-09-12T00:00:00Z",
        "deleted_at": "2026-09-12T00:00:00Z",
    }
    report = sync(make_client(), store, now=lambda: FIXED_NOW)
    assert report.deleted == 1
    assert "not_SmplInPerson01" not in {n.note_id for n in store.iter_notes()}
    rows = store.conn.execute(
        "SELECT COUNT(*) FROM utterances WHERE note_id = 'not_SmplInPerson01'"
    ).fetchone()
    assert rows[0] == 0


def test_folder_filter_includes_child_folders() -> None:
    src = FixtureSource(SAMPLE)
    ids = {n.id for n in src.list_notes(folder_id="fol_Nw0Customers01")}
    assert ids == {"not_SmplAcmeCall01"}

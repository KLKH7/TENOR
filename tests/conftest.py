from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from concentrate.sources.granola_api import GranolaAPISource
from concentrate.sources.ratelimit import FakeClock
from tests.fake_granola import API_KEY, FakeGranola

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "sample"


def sample_notes() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((SAMPLE / "notes").glob("*.json"))}


def sample_folders() -> list[dict[str, Any]]:
    folders: list[dict[str, Any]] = json.loads((SAMPLE / "folders.json").read_text())["folders"]
    return folders


def many_notes(n: int) -> dict[str, dict[str, Any]]:
    """``n`` valid notes cloned from the first sample note with distinct IDs and times."""
    template = next(iter(sample_notes().values()))
    base = datetime(2026, 9, 1, tzinfo=UTC)
    notes = {}
    for i in range(n):
        nid = f"not_Clone{i:09d}"
        ts = (base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        notes[nid] = {**template, "id": nid, "created_at": ts, "updated_at": ts}
    return notes


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def server(clock: FakeClock) -> FakeGranola:
    return FakeGranola(clock=clock, notes=sample_notes(), folders=sample_folders())


@pytest.fixture
def make_client(server: FakeGranola, clock: FakeClock) -> Callable[..., GranolaAPISource]:
    def make(**kwargs: Any) -> GranolaAPISource:
        return GranolaAPISource(
            API_KEY,
            base_url="https://public-api.granola.ai",
            transport=server.transport(),
            clock=clock,
            rng=random.Random(0),
            **kwargs,
        )

    return make

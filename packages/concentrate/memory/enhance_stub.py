"""A stand-in for the Enhance stage (``concentrate.enhance``), built in parallel.

It implements the agreed contract with *no* inference: speakers keep Granola's raw ``Me`` /
``Them (Name)`` labels, there are no mention links, and attendees are mapped to directory ids by
email. Swap in the real stage with ``Enhancer(enhance=concentrate.enhance.enhance,
line=concentrate.enhance.enhanced_line, load_directory=concentrate.enhance.<loader>)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from concentrate.agents import corpus as cv
from concentrate.core.models import MeetingNote, Utterance
from concentrate.memory.entities import EntityDirectory, load_directory


class SpeakerResolution(BaseModel):
    person_id: str | None
    confidence: float
    method: str
    candidates: list[str]


class MentionLink(BaseModel):
    utterance_id: str
    start: int
    end: int
    text: str
    kind: Literal["person", "org", "project"]
    entity_id: str | None
    confidence: float
    reason: str


class EnhancedNote(BaseModel):
    note_id: str
    speakers: dict[str, SpeakerResolution]
    mentions: list[MentionLink]
    attendee_ids: list[str]


def stub_enhance(
    note: MeetingNote, directory: Any, history: Sequence[MeetingNote] = ()
) -> EnhancedNote:
    """Contract-shaped output with nothing resolved beyond what Granola already says."""
    ids = directory.attendee_ids(note) if isinstance(directory, EntityDirectory) else []
    return EnhancedNote(note_id=note.note_id, speakers={}, mentions=[], attendee_ids=ids)


def stub_enhanced_line(u: Utterance, e: Any) -> str:
    return f"[{u.utterance_id}] {cv.speaker_label(u)}: {u.text}"


def _stub_directory(path: Path | None) -> Any:
    return load_directory(path)


@dataclass(frozen=True)
class Enhancer:
    """The three Enhance entry points the memory pipeline needs."""

    name: str
    enhance: Callable[[MeetingNote, Any, Sequence[MeetingNote]], Any]
    line: Callable[[Utterance, Any], str]
    load_directory: Callable[[Path | None], Any]


STUB_ENHANCER = Enhancer("stub-v1", stub_enhance, stub_enhanced_line, _stub_directory)


DEFAULT_DIRECTORY = Path("data/fixtures/northwind/directory.json")


def _real_directory(path: Path | None) -> Any:
    from concentrate.enhance.directory import Directory

    return Directory.load(path or DEFAULT_DIRECTORY)


def real_enhancer(version: str = "enhance-v1") -> Enhancer:
    """The real Enhance stage: speaker attribution, mention linking, attendee resolution.

    ``version`` selects the speaker model ("enhance-v1", or "enhance-v2" with the cross-meeting
    task cue and the diarized-owner prior; docs/concentrate/enhance_v2.md). The name is part of
    the memory store's config, so switching versions never reuses a store built by the other.
    """
    from functools import partial

    from concentrate.enhance import enhancer as enh
    from concentrate.enhance.speakers import VERSIONS

    if version not in VERSIONS:
        raise ValueError(f"unknown enhancer version {version!r}; expected one of {VERSIONS}")
    run = partial(enh.enhance, version=version)
    return Enhancer(version, run, enh.enhanced_line, _real_directory)

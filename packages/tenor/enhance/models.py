"""Output types of the Enhance stage (the interface later stages build against)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

EntityKind = Literal["person", "org", "project"]


class SpeakerResolution(BaseModel):
    """Who spoke one utterance.

    ``person_id`` is ``None`` when no candidate clears the confidence threshold; ``candidates``
    then lists the directory ids considered, **best first**, and ``confidence`` is the best
    candidate's score (so callers can still pick a lower threshold).
    """

    model_config = ConfigDict(frozen=True)

    person_id: str | None
    confidence: float
    method: str  # platform_name | owner_mic | addressed | turn_continuity | self_reference |
    # diarization_propagation | attendee_prior | unresolved
    candidates: list[str]


class MentionLink(BaseModel):
    """A span of an utterance's text that refers to a person, org or project."""

    model_config = ConfigDict(frozen=True)

    utterance_id: str
    start: int
    end: int
    text: str
    kind: EntityKind
    entity_id: str | None  # None: ambiguous (candidates are named in ``reason``)
    confidence: float
    reason: str


class EnhancedNote(BaseModel):
    note_id: str
    speakers: dict[str, SpeakerResolution]  # keyed by utterance_id, one per utterance
    mentions: list[MentionLink]
    attendee_ids: list[str]  # attendees (and the owner) resolved to directory ids
    # Display labels ("Sam Patel (Backend Engineer)") for every person id used above, so
    # ``enhanced_line`` can render without the directory. Additive; may be empty.
    labels: dict[str, str] = {}

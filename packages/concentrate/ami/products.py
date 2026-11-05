"""Match product notes to AMI meetings and score their transcripts against the manual words.

Each playback gets a calendar event titled ``AMI <meeting id>`` (docs/benchmark/AMI_DESIGN.md
§3), so a synced note is matched by that tag in its title or event title. Notes come from a
dedicated AMI account synced into its own database (``data/ami_<product>.db``), never from the
author's real meetings.

Results hold numbers only: no transcript text, note IDs or titles. Raw product output stays
local (Kian, 2026-11-04: publish scores only).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from concentrate.ami.corpus import DEFAULT_ZIP, load_meeting
from concentrate.ami.wer import WerScore, score
from concentrate.core.models import MeetingNote

AMI_SET = ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003", "EN2002b")
_TAG = re.compile(r"\bAMI[\s_-]+([A-Z]{2}\d{4}[a-z]?)\b")


class MeetingWer(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    product: str
    score: WerScore
    product_speakers: int  # distinct speaker labels the product gave (0 if none)
    reference_speakers: int


def ami_tag(note: MeetingNote) -> str | None:
    """Return the AMI meeting ID tagged in a note's title or calendar event, if any."""
    for text in (note.title, note.event_title):
        hit = _TAG.search(text or "")
        if hit:
            return hit[1]
    return None


def match_notes(notes: Iterable[MeetingNote]) -> dict[str, MeetingNote]:
    """Map AMI meeting ID -> note. Raises if one meeting was recorded more than once."""
    out: dict[str, MeetingNote] = {}
    for note in notes:
        mid = ami_tag(note)
        if mid is None:
            continue
        if mid in out:
            raise ValueError(f"{mid} matched two notes; delete or retitle one recording")
        out[mid] = note
    return out


def transcript_text(note: MeetingNote) -> str:
    """The product's transcript as one string, in the product's own utterance order."""
    return " ".join(u.text for u in sorted(note.utterances, key=lambda u: u.index))


def score_note(
    meeting_id: str, note: MeetingNote, product: str, zip_path: Path = DEFAULT_ZIP
) -> MeetingWer:
    """Score one product note against the AMI manual transcript of ``meeting_id``."""
    meeting = load_meeting(meeting_id, zip_path)
    labels = {u.speaker_name or u.diarization_label for u in note.utterances} - {None}
    return MeetingWer(
        meeting_id=meeting_id,
        product=product,
        score=score(meeting.reference_text(), transcript_text(note)),
        product_speakers=len(labels),
        reference_speakers=len(meeting.speakers),
    )

"""Match product notes to AMI meetings and score their transcripts against the manual words.

Each playback gets a calendar event titled ``AMI <meeting id>`` (docs/benchmark/AMI_DESIGN.md
§3), so a synced note is matched by that tag in its title or event title. Notes come from a
dedicated AMI account synced into its own database (``data/ami_<product>.db``), never from the
author's real meetings.

Only the system-audio side is scored. AMI is played into the product digitally with the
speakers muted, so the microphone should hear nothing; anything it does hear is the room, not
AMI. Granola labels the capture channel directly. Wispr has none, so its "me" side is ASSUMED
(turns attributed to the account holder; see sources/wispr_mcp.py). ``mic_utterances`` counts
what was dropped: a non-zero value flags a contaminated recording.

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
from concentrate.core.models import Channel, MeetingNote

AMI_SET = ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003", "EN2002b")
_TAG = re.compile(r"\bAMI[\s_-]+([A-Z]{2}\d{4}[a-z]?)\b")


class MeetingWer(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    product: str
    score: WerScore
    product_speakers: int  # distinct speaker labels the product gave (0 if none)
    mic_utterances: int  # microphone-side turns dropped before scoring; > 0 = room leaked in
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
    """The product's system-audio transcript as one string, in the product's utterance order."""
    utts = sorted(note.utterances, key=lambda u: u.index)
    return " ".join(u.text for u in utts if u.channel is not Channel.ME)


def score_note(
    meeting_id: str, note: MeetingNote, product: str, zip_path: Path = DEFAULT_ZIP
) -> MeetingWer:
    """Score one product note against the AMI manual transcript of ``meeting_id``."""
    meeting = load_meeting(meeting_id, zip_path)
    system = [u for u in note.utterances if u.channel is not Channel.ME]
    labels = {u.speaker_name or u.diarization_label for u in system} - {None}
    return MeetingWer(
        meeting_id=meeting_id,
        product=product,
        score=score(meeting.reference_text(), transcript_text(note)),
        product_speakers=len(labels),
        mic_utterances=len(note.utterances) - len(system),
        reference_speakers=len(meeting.speakers),
    )

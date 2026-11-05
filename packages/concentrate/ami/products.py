"""Match product notes to AMI meetings and score their transcripts against the manual words.

Each playback gets a calendar event titled ``AMI <meeting id>`` (docs/method/AMI_REPLAY.md
§3), so a synced note is matched by that tag in its title or event title. Notes come from a
dedicated AMI account synced into its own database (``data/ami_<product>.db``), never from the
author's real meetings.

AMI is played in digitally with the speakers muted, so the microphone should hear nothing;
anything it does hear is the room, not AMI. Granola records the capture channel itself, so its
microphone turns are dropped (``drop_mic=True``) and counted in ``mic_utterances``: a non-zero
value flags a contaminated recording. Wispr has no capture channel: our "me" there is only a
guess from the speaker name (sources/wispr_mcp.py), and on AMI Wispr can name strangers after
the account holder. Dropping those turns would delete real AMI speech, so Wispr is scored with
``drop_mic=False`` and its room protection is physical (muted speakers, silent room).
``owner_labelled`` counts turns attributed to the account holder: on AMI every one is wrong.

Results hold numbers only: no transcript text, note IDs or titles. Raw product output stays
local (Kian, 2026-11-04: publish scores only).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from concentrate.ami.corpus import DEFAULT_ZIP, load_meeting
from concentrate.ami.wer import WerScore, score
from concentrate.core.models import Channel, MeetingNote

log = logging.getLogger(__name__)

AMI_SET = ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003", "EN2002b", "ES2006a")
_TAG = re.compile(r"\bAMI[\s_-]+([A-Z]{2}\d{4}[a-z]?)\b")
# A stress-condition recording ("AMI ES2006a A1", docs/tracks/CAPTURE.md) is not a baseline one.
_STRESS = re.compile(r"\bAMI[\s_-]+([A-Z]{2}\d{4}[a-z]?)[\s_-]+(A\d+)\b")


class MeetingWer(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    product: str
    score: WerScore
    product_speakers: int  # distinct speaker labels the product gave (0 if none)
    mic_utterances: int  # microphone turns dropped before scoring; > 0 = room leaked in
    owner_labelled: int  # turns attributed to the account holder, who never speaks in AMI
    reference_speakers: int


def stress_tag(note: MeetingNote) -> tuple[str, str] | None:
    """(AMI meeting ID, condition) for a stress recording titled like ``AMI ES2006a A1``."""
    for text in (note.title, note.event_title):
        hit = _STRESS.search(text or "")
        if hit:
            return hit[1], hit[2]
    return None


def ami_tag(note: MeetingNote) -> str | None:
    """Return the AMI meeting ID tagged in a note's title or calendar event, if any.

    Stress recordings (:func:`stress_tag`) are not baseline recordings and return None.
    """
    if stress_tag(note) is not None:
        return None
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


_BARE_ID = re.compile(r"[A-Z]{2}\d{4}[a-z]?")


_PIN = re.compile(r"([a-z][a-z0-9_]*)=(\S+)")


def read_overrides(path: Path) -> dict[str, dict[str, str]]:
    """Read manual recording IDs: product -> AMI meeting ID -> note ID.

    A pin line starts with a meeting ID followed by ``<product>=<note id>`` tokens, e.g.
    ``IB4003 wispr=<uuid> granola=<uuid>``. A stress recording pins under
    ``<product>_<condition>`` in lower case (``granola_a1=<uuid>``). Anything else is a human
    note and is ignored: other words on a pin line ("(untitled)"), lines not starting with a
    bare meeting ID, and text after ``#``. A missing file means no overrides.
    """
    out: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        parts = raw.split("#", 1)[0].split()
        if not parts or not _BARE_ID.fullmatch(parts[0]):
            continue
        for tok in parts[1:]:
            pin = _PIN.fullmatch(tok)
            if pin:
                out.setdefault(pin[1], {})[parts[0]] = pin[2]
    return out


def match_recordings(
    notes: Iterable[MeetingNote], overrides: dict[str, str] | None = None
) -> dict[str, MeetingNote]:
    """Map AMI meeting ID -> note, for one product's notes.

    Like :func:`match_notes`, plus a note whose title is exactly a bare meeting ID
    ("IB4003": Wispr titles can lack the "AMI " prefix), and ``overrides`` (meeting ID -> note
    ID, from :func:`read_overrides`), which win over any title match. A pinned meeting whose
    note isn't synced yet is left unmatched (with a warning). Raises if one meeting still
    matches two notes.
    """
    pinned = overrides or {}
    by_id: dict[str, MeetingNote] = {}
    out: dict[str, MeetingNote] = {}
    for note in notes:
        by_id[note.note_id] = note
        bare = (note.title or "").strip()
        mid = ami_tag(note) or (bare if _BARE_ID.fullmatch(bare) else None)
        if mid is None or mid in pinned:
            continue
        if mid in out:
            raise ValueError(f"{mid} matched two notes; retitle one or pin it in the overrides")
        out[mid] = note
    for mid, note_id in pinned.items():
        if note_id in by_id:
            out[mid] = by_id[note_id]
        else:
            log.warning("override for %s names a note that isn't synced yet; skipped", mid)
    return out


def match_stress(
    notes: Iterable[MeetingNote], meeting_id: str, condition: str, pinned: str | None = None
) -> MeetingNote | None:
    """The one note recording ``meeting_id`` under ``condition``: a pinned note ID wins, else
    the note titled ``AMI <meeting_id> <condition>``. Raises if two notes match."""
    found: list[MeetingNote] = []
    for note in notes:
        if pinned is not None:
            if note.note_id == pinned:
                return note
            continue
        if stress_tag(note) == (meeting_id, condition):
            found.append(note)
    if len(found) > 1:
        raise ValueError(f"{meeting_id} {condition} matched {len(found)} notes; pin one")
    return found[0] if found else None


def transcript_text(note: MeetingNote, *, drop_mic: bool = True) -> str:
    """The product's transcript as one string, in its utterance order, mic turns optional."""
    utts = sorted(note.utterances, key=lambda u: u.index)
    return " ".join(
        u.text
        for u in utts
        if not (drop_mic and u.channel is Channel.ME) and not _is_banner(u.text)
    )


def _is_banner(text: str) -> bool:
    """Wispr prepends a ``<<<…>>>`` safety banner as utterance 0; it is not speech."""
    t = text.strip()
    return t.startswith("<<<") and t.endswith(">>>")


def score_note(
    meeting_id: str,
    note: MeetingNote,
    product: str,
    zip_path: Path = DEFAULT_ZIP,
    *,
    drop_mic: bool = True,
) -> MeetingWer:
    """Score one product note against the AMI manual transcript of ``meeting_id``.

    ``drop_mic`` only for products whose microphone channel is a real capture channel (Granola).
    """
    meeting = load_meeting(meeting_id, zip_path)
    mine = [u for u in note.utterances if u.channel is Channel.ME]
    system = [u for u in note.utterances if not (drop_mic and u.channel is Channel.ME)]
    labels = {u.speaker_name or u.diarization_label for u in system} - {None}
    return MeetingWer(
        meeting_id=meeting_id,
        product=product,
        score=score(meeting.reference_text(), transcript_text(note, drop_mic=drop_mic)),
        product_speakers=len(labels),
        mic_utterances=len(note.utterances) - len(system),
        owner_labelled=len(mine),
        reference_speakers=len(meeting.speakers),
    )

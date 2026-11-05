"""Parse AMI's manual annotations (NXT XML) straight from ``ami_public_manual_1.6.2.zip``.

The zip is read in place, never extracted. For one meeting we load:

- **speakers** from ``corpusResources/meetings.xml``: NXT agent letter (A-D) -> role, a stable
  participant code and the audio channel. Letters are per meeting, not per person: in IS1008 the
  same person is agent B in one session and C in the next, so cross-meeting code must key on
  ``participant``, never on the letter;
- **words** from ``words/<ID>.<letter>.words.xml``: each spoken word with forced-alignment start
  and end times. Punctuation, truncated fragments ("th-") and non-word events (laughs, gaps,
  disfluency markers) are flagged or dropped here, so scorers see the same reference;
- **summary** from ``abstractive/<ID>.abssumm.xml``: the annotator's ABSTRACT / DECISIONS /
  PROBLEMS / ACTIONS sentences (scenario meetings only; natural meetings have none).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

NITE = "{http://nite.sourceforge.net/}"
DEFAULT_ZIP = Path("data/ami/annotations/ami_public_manual_1.6.2.zip")
_WORDS = re.compile(r"words/(?P<mid>[A-Z]{2}\d{4}[a-z]?)\.(?P<agent>[A-Z])\.words\.xml$")
SUMMARY_SECTIONS = ("abstract", "decisions", "problems", "actions")


class Speaker(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent: str  # NXT letter, A-D (per meeting)
    role: str | None  # PM / ME / UI / ID in scenario meetings; absent in natural ones
    participant: str  # AMI's anonymised participant code, stable across meetings
    channel: int


class Word(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent: str
    start: float  # seconds from the start of the recording
    end: float
    text: str
    punc: bool = False  # a punctuation token ("." ",")
    trunc: bool = False  # an interrupted fragment ("th", "cou")


class AmiMeeting(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    type: str  # "scenario" or "nonscenario" (AMI's own label)
    duration_s: int
    speakers: tuple[Speaker, ...]
    words: tuple[Word, ...]  # all speakers, sorted by start time
    summary: dict[str, tuple[str, ...]]  # SUMMARY_SECTIONS -> sentences; empty if none

    def spoken(self) -> list[Word]:
        """Words a listener would hear: no punctuation tokens, no truncated fragments."""
        return [w for w in self.words if not w.punc and not w.trunc]

    def reference_text(self) -> str:
        """The manual transcript as one string, all speakers merged by start time."""
        return " ".join(w.text for w in self.spoken())


def _parse(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def _speakers(zf: zipfile.ZipFile, meeting_id: str) -> tuple[str, int, tuple[Speaker, ...]]:
    root = _parse(zf, "corpusResources/meetings.xml")
    for m in root.iter("meeting"):
        if m.get("observation") != meeting_id:
            continue
        speakers = tuple(
            sorted(
                (
                    Speaker(
                        agent=s.get("nxt_agent", ""),
                        role=s.get("role"),
                        participant=s.get("global_name", ""),
                        channel=int(s.get("channel", "-1")),
                    )
                    for s in m.iter("speaker")
                ),
                key=lambda s: s.agent,
            )
        )
        return m.get("type", ""), round(float(m.get("duration", "0"))), speakers
    raise KeyError(f"{meeting_id} not in meetings.xml")


def _words(zf: zipfile.ZipFile, meeting_id: str) -> list[Word]:
    words: list[Word] = []
    for name in sorted(zf.namelist()):
        hit = _WORDS.search(name)
        if not hit or hit["mid"] != meeting_id:
            continue
        for el in _parse(zf, name):
            if el.tag != "w" or not (el.text or "").strip():
                continue  # vocalsound, gap, disfmarker, transformerror: not words
            words.append(
                Word(
                    agent=hit["agent"],
                    start=float(el.get("starttime", "nan")),
                    end=float(el.get("endtime", "nan")),
                    text=(el.text or "").strip(),
                    punc=el.get("punc") == "true",
                    trunc=el.get("trunc") == "true",
                )
            )
    # Stable order: start time, then speaker, then original position (sort is stable).
    words.sort(key=lambda w: (w.start, w.agent))
    return words


def _summary(zf: zipfile.ZipFile, meeting_id: str) -> dict[str, tuple[str, ...]]:
    name = f"abstractive/{meeting_id}.abssumm.xml"
    if name not in zf.namelist():
        return {}
    root = _parse(zf, name)
    out: dict[str, tuple[str, ...]] = {}
    for section in SUMMARY_SECTIONS:
        el = root.find(section)
        if el is not None:
            out[section] = tuple(
                " ".join((s.text or "").split()) for s in el.iter("sentence") if s.text
            )
    return out


def load_meeting(meeting_id: str, zip_path: Path = DEFAULT_ZIP) -> AmiMeeting:
    """Load one AMI meeting's speakers, manual words and abstractive summary from the zip."""
    with zipfile.ZipFile(zip_path) as zf:
        mtype, duration, speakers = _speakers(zf, meeting_id)
        words = _words(zf, meeting_id)
        if not words:
            raise KeyError(f"no word files for {meeting_id} in {zip_path}")
        return AmiMeeting(
            meeting_id=meeting_id,
            type=mtype,
            duration_s=duration,
            speakers=speakers,
            words=tuple(words),
            summary=_summary(zf, meeting_id),
        )

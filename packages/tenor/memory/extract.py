"""Extract: one meeting (or window) of the enhanced transcript -> atomic facts via ``extract.v1``.

The transcript is shown with short local line IDs (``[0037]``) to keep the prompt small; the
reply is mapped back to canonical utterance IDs (``not_x:0037``) before the gate sees it.

Chunking (design-doc open question 2): **whole meetings** by default. Base Northwind meetings
average ~72 lines (~2k tokens) and XL ones ~7k tokens, far inside the context window; one call
per meeting keeps a fact's statement, its reason and its follow-up together (they are often
dozens of lines apart) and pays the cached system prompt once per meeting instead of once per
window. ``window_lines`` splits long meetings into overlapping windows for the comparison and
for pathological transcripts; see docs/archive/concentrate/memory.md for the measurement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tenor.agents import corpus as cv
from tenor.core.models import MeetingNote
from tenor.llm.client import TurnRequest
from tenor.memory.schema import EXTRACTION_SCHEMA, Extraction

EXTRACT_PROMPT = "extract.v2"  # v2: enhanced speaker labels; unresolved owners
WINDOW_OVERLAP = 6
EXTRACT_MAX_TOKENS = 16000
_LOCAL = re.compile(r"^\[?(\d{1,4})\]?$")


def meeting_day(m: MeetingNote) -> str:
    return (m.scheduled_start or m.created_at).date().isoformat()


def recorded_at(m: MeetingNote) -> str:
    """Transaction time of everything learnt from ``m``: when the meeting happened."""
    return (m.scheduled_start or m.created_at).isoformat()


def local_line(full_line: str, utterance_id: str, index: int) -> str:
    """Rewrite an enhanced line's ``[not_x:0037]`` prefix as ``[0037]``."""
    prefix = f"[{utterance_id}]"
    body = full_line[len(prefix) :].lstrip() if full_line.startswith(prefix) else full_line
    return f"[{index:04d}] {body}"


def windows(n: int, size: int | None, overlap: int = WINDOW_OVERLAP) -> list[tuple[int, int]]:
    """``[start, end)`` line ranges: one range when ``size`` is None or the meeting fits."""
    if size is None or n <= size:
        return [(0, n)]
    step = max(1, size - overlap)
    out = []
    for start in range(0, n, step):
        out.append((start, min(n, start + size)))
        if start + size >= n:
            break
    return out


@dataclass(frozen=True)
class ExtractContext:
    """Per-meeting context that comes from the memory built so far."""

    keys: str  # existing (subject, predicate) = value lines
    commitments: str  # open commitments with ids


def user_prompt(
    m: MeetingNote,
    attendees: list[str],
    lines: list[str],
    ctx: ExtractContext,
    part: tuple[int, int] | None = None,
) -> str:
    when = (m.scheduled_start or m.created_at).strftime("%A %Y-%m-%d %H:%M UTC")
    head = [
        f"Meeting: {m.title or 'untitled'}",
        f"Date: {when}",
        f"Attendees: {', '.join(attendees) or 'unknown'}",
        "",
        "Existing memory keys:",
        ctx.keys or "(none)",
        "",
        "Open commitments:",
        ctx.commitments or "(none)",
        "",
        "AI summary (orientation only):",
        cv.summary_view(m),
        "",
    ]
    if part is not None:
        head.append(f"Transcript lines {part[0]:04d}-{part[1] - 1:04d} of {len(m.utterances)}:")
    else:
        head.append("Transcript:")
    return "\n".join(head) + "\n" + "\n".join(lines)


def extract_request(system: str, user: str, *, effort: str) -> TurnRequest:
    return TurnRequest(
        stage="ingest.concentrate.extract",
        prompt_id=EXTRACT_PROMPT,
        system=system,
        messages=({"role": "user", "content": user},),
        schema=EXTRACTION_SCHEMA,
        max_tokens=EXTRACT_MAX_TOKENS,
        effort=effort,
        cache_system=True,
    )


def full_id(note_id: str, ref: str) -> str:
    """``"0037"`` / ``"[0037]"`` / ``"not_x:0037"`` -> ``"not_x:0037"`` (unknown forms are kept
    under this note's prefix, so the gate rejects them as unknown lines)."""
    ref = ref.strip()
    mt = _LOCAL.match(ref)
    if mt:
        return f"{note_id}:{int(mt.group(1)):04d}"
    if ":" in ref:
        return ref.strip("[]")
    return f"{note_id}:{ref}"


def to_full_ids(x: Extraction, note_id: str) -> Extraction:
    """Map every cited line (and the digest's ``[0037]`` refs) to canonical utterance IDs."""

    def fix(lines: list[str]) -> list[str]:
        return list(dict.fromkeys(full_id(note_id, r) for r in lines))

    digest = re.sub(
        r"\[(\d{4}(?:\s*,\s*\d{4})*)\]",
        lambda mt: "[" + ", ".join(f"{note_id}:{d.strip()}" for d in mt.group(1).split(",")) + "]",
        x.digest,
    )
    return Extraction(
        facts=[f.model_copy(update={"source_lines": fix(f.source_lines)}) for f in x.facts],
        commitments=[
            c.model_copy(update={"source_lines": fix(c.source_lines)}) for c in x.commitments
        ],
        commitment_updates=[
            u.model_copy(update={"source_lines": fix(u.source_lines)}) for u in x.commitment_updates
        ],
        digest=digest,
    )


def merge(parts: list[Extraction]) -> Extraction:
    """Concatenate window extractions (consolidation dedupes restated facts)."""
    return Extraction(
        facts=[f for p in parts for f in p.facts],
        commitments=[c for p in parts for c in p.commitments],
        commitment_updates=[u for p in parts for u in p.commitment_updates],
        digest=" ".join(p.digest for p in parts if p.digest),
    )

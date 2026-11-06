"""MCP contract check: detect silent changes in the *shape* of a product's MCP responses.

On 2026-11-05 Granola's ``get_meeting_transcript`` chunks changed from a ``passages`` list to a
single rendered ``transcript`` string, unannounced; our sync read 0 lines without an error. Any
agent built on the MCP breaks the same way. This module records what each read tool returns as
a **shape** (field names, types, tag and attribute names, and the line format of multi-line
text) and diffs it against a committed baseline.

Shapes never hold values: no titles, names, IDs or text. That is what lets the baseline live in
the public repo while the probe runs against the author's private account.

Live probes go through :class:`~tenor.sources.allowlist.AllowlistCaller`, so only read
tools can be called. The meeting probed is discovered at run time (the newest one), so no
meeting ID is ever committed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from tenor.sources.granola_mcp import McpCaller

READ_TOOLS = frozenset(
    {"search_meetings", "get_meetings", "get_meeting_transcript", "search_transcripts"}
)
CONTRACT_VERSION = 1

Shape = Any  # str | dict[str, Shape] | list[Shape]: JSON-serialisable, values-free

_TAG = re.compile(r"<([a-z_][a-z0-9_]*)((?:\s+[a-z_][a-z0-9_]*=\"[^\"]*\")*)\s*/?>")
_ATTR = re.compile(r"([a-z_][a-z0-9_]*)=\"")
_ID = re.compile(r"\bid=\"([0-9a-f-]{36})\"")
# Line formats seen in rendered transcripts. Order matters: first match wins.
_LINE_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[m:ss] Label: text", re.compile(r"^\[(?:\d+:)?\d+:\d{2}\]\s+[^:]{1,80}:\s")),
    ("Label: text", re.compile(r"^(?:Microphone|System audio|Speaker \w+)[^:]{0,80}:\s")),
)


def text_shape(text: str) -> str:
    """Describe a string without its content: ``str``, or the line formats of multi-line text."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return "str"
    formats = set()
    for line in lines:
        name = next((n for n, p in _LINE_FORMATS if p.match(line)), "plain")
        formats.add(name)
    return "text[" + " | ".join(sorted(formats)) + "]"


def json_shape(value: Any) -> Shape:
    """Values-free structural description of parsed JSON. List elements are merged."""
    if isinstance(value, dict):
        return {k: json_shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        merged: Shape = None
        for item in value:
            merged = merge(merged, json_shape(item))
        return [merged] if merged is not None else []
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if value is None:
        return "null"
    return text_shape(str(value))


def merge(a: Shape, b: Shape) -> Shape:
    """Union of two shapes: dict keys combined, differing leaf types joined with ``|``."""
    if a is None:
        return b
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge(a.get(k), b.get(k)) for k in sorted(a.keys() | b.keys())}
    if isinstance(a, list) and isinstance(b, list):
        inner = merge(a[0] if a else None, b[0] if b else None)
        return [inner] if inner is not None else []
    if a == b or b is None:
        return a
    return " | ".join(sorted(set(str(a).split(" | ")) | set(str(b).split(" | "))))


def markup_shape(text: str) -> Shape:
    """Shape of the XML-like text some tools return: each tag name -> its attribute names."""
    tags: dict[str, set[str]] = {}
    for name, attrs in _TAG.findall(text):
        tags.setdefault(name, set()).update(_ATTR.findall(attrs))
    return {"markup": {t: sorted(a) for t, a in sorted(tags.items())}}


def response_shape(text: str) -> Shape:
    """Shape of one tool response: JSON if it parses, else markup tags and attributes."""
    try:
        return {"json": json_shape(json.loads(text))}
    except json.JSONDecodeError:
        return markup_shape(text)


def diff(baseline: Shape, current: Shape, path: str = "") -> list[str]:
    """Human-readable differences between two shapes, one line per change."""
    if isinstance(baseline, dict) and isinstance(current, dict):
        out: list[str] = []
        for key in sorted(baseline.keys() | current.keys()):
            sub = f"{path}.{key}" if path else key
            if key not in current:
                out.append(f"removed  {sub}")
            elif key not in baseline:
                out.append(f"added    {sub}: {_brief(current[key])}")
            else:
                out.extend(diff(baseline[key], current[key], sub))
        return out
    if isinstance(baseline, list) and isinstance(current, list):
        return diff(baseline[0] if baseline else None, current[0] if current else None, path + "[]")
    if baseline != current and not _compatible(baseline, current):
        return [f"changed  {path}: {_brief(baseline)} -> {_brief(current)}"]
    return []


def _compatible(a: Shape, b: Shape) -> bool:
    """Leaves agree once ``null`` is ignored: an optional field that happens to be empty, or
    filled, is not drift."""
    if not (isinstance(a, str) and isinstance(b, str)):
        return False
    ta, tb = set(a.split(" | ")) - {"null"}, set(b.split(" | ")) - {"null"}
    return ta == tb or not ta or not tb


def _brief(shape: Shape) -> str:
    if isinstance(shape, dict):
        return "{" + ", ".join(sorted(shape)) + "}"
    if isinstance(shape, list):
        return "[" + (_brief(shape[0]) if shape else "") + "]"
    return str(shape)


def probe_granola(call: Callable[[str, dict[str, Any]], str]) -> dict[str, Shape]:
    """Call each Granola read tool once on the newest meeting; return ``{probe: shape}``.

    Needs at least one meeting with a transcript in the last 7 days (``search_meetings``' default
    window). ``call`` must be read-only (wrap a live caller in ``AllowlistCaller``).
    """
    listing = call("search_meetings", {"limit": 10, "max_participants": 0})
    shapes: dict[str, Shape] = {"search_meetings": response_shape(listing)}
    # The newest meeting with a finished transcript: one still recording has none yet, and its
    # "pending" reply would make a misleading baseline.
    mid, chunks = None, ""
    for candidate in dict.fromkeys(_ID.findall(listing)):
        chunks = call("get_meeting_transcript", {"meeting_id": candidate, "chunk_ids": [0]})
        if _status(chunks) == "ok":
            mid = candidate
            break
    if mid is None:
        raise LookupError("no meeting with a finished transcript in the last 7 days to probe")
    shapes["get_meetings"] = response_shape(
        call("get_meetings", {"meeting_ids": [mid], "attachments": "none", "max_participants": 0})
    )
    shapes["get_meeting_transcript.chunks"] = response_shape(chunks)
    shapes["get_meeting_transcript.full"] = response_shape(
        call("get_meeting_transcript", {"meeting_id": mid, "max_chars": 2000})
    )
    shapes["search_transcripts"] = response_shape(
        call("search_transcripts", {"keywords": ["the", "and"], "meeting_ids": [mid], "limit": 2})
    )
    return shapes


def _status(text: str) -> str | None:
    try:
        reply = json.loads(text)
    except json.JSONDecodeError:
        return None
    return reply.get("status") if isinstance(reply, dict) else None


def run(caller: McpCaller) -> dict[str, Any]:
    """Probe through ``caller`` and wrap the shapes with the contract version."""
    return {"contract_version": CONTRACT_VERSION, "tools": probe_granola(caller.call)}

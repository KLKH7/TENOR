"""Shared builders for the memory tests: tiny synthetic notes and a scripted fake LLM."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from tenor.core.models import Channel, MeetingNote, Person, Utterance
from tenor.llm.client import TurnCompletion, TurnRequest

OWNER = Person(name="Priya Raman", email="priya@northwind.dev")


def make_note(
    note_id: str, day: str, title: str, lines: list[tuple[str, str | None, str]]
) -> MeetingNote:
    """``lines`` are ``(channel "me"|"them", speaker name or None, text)``."""
    start = datetime.fromisoformat(day).replace(hour=10, tzinfo=UTC)
    utts = tuple(
        Utterance(
            utterance_id=f"{note_id}:{i:04d}",
            note_id=note_id,
            index=i,
            channel=Channel(ch),
            speaker_name=name,
            diarization_label=None,
            text=text,
            start_time=start + timedelta(seconds=10 * i),
            end_time=start + timedelta(seconds=10 * i + 9),
        )
        for i, (ch, name, text) in enumerate(lines)
    )
    return MeetingNote(
        note_id=note_id,
        title=title,
        owner=OWNER,
        created_at=start,
        updated_at=start,
        deleted_at=None,
        web_url=f"https://notes.granola.ai/d/{note_id}",
        event_title=title,
        calendar_event_id=None,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=30),
        organiser_email=OWNER.email,
        invitee_emails=(),
        attendees=(OWNER, Person(name="Marcus Chen", email="marcus@northwind.dev")),
        folders=(),
        space_ids=(),
        summary_markdown=f"Summary of {title}",
        summary_text=f"Summary of {title}",
        private_notes_markdown=None,
        utterances=utts,
    )


def fact(
    subject: str,
    predicate: str,
    value: str,
    value_norm: str,
    lines: list[str],
    quote: str,
    confidence: float = 0.95,
    valid_from: str | None = None,
    kind: str = "decision",
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "subject": subject,
        "predicate": predicate,
        "kind": kind,
        "value": value,
        "value_norm": value_norm,
        "valid_from": valid_from,
        "source_lines": lines,
        "quote": quote,
        "reason": reason,
        "confidence": confidence,
        "privacy": "none",
    }


def extraction(
    facts: list[dict[str, Any]] | None = None,
    commitments: list[dict[str, Any]] | None = None,
    updates: list[dict[str, Any]] | None = None,
    digest: str = "",
) -> str:
    return json.dumps(
        {
            "facts": facts or [],
            "commitments": commitments or [],
            "commitment_updates": updates or [],
            "digest": digest,
        }
    )


def completion(text: str, model: str, cache_read: int = 0) -> TurnCompletion:
    return TurnCompletion(
        [{"type": "text", "text": text}],
        text,
        "end_turn",
        model,
        1000,
        300,
        cache_read_tokens=cache_read,
    )


class ScriptedLLM:
    """Fake ``ToolBackend``: extraction replies keyed by meeting title, entailment by claim
    text (claims containing "NOT ENTAILED" fail), answers cite the first line ID shown."""

    def __init__(self, extractions: dict[str, str], fail_on: str | None = None) -> None:
        self.extractions, self.fail_on = extractions, fail_on
        self.requests: list[TurnRequest] = []

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        self.requests.append(request)
        user = str(request.messages[0]["content"])
        if request.prompt_id == "extract.v2":
            title = user.split("\n", 1)[0].removeprefix("Meeting: ")
            if self.fail_on == title:
                raise RuntimeError("simulated crash")
            return completion(self.extractions.get(title, extraction()), model, cache_read=2000)
        if request.prompt_id == "entail.v2":
            claims = [b for b in user.split("\n\n") if b.startswith("Claim ")]
            verdicts = [
                {"index": i, "entailed": "NOT ENTAILED" not in c, "reason": "fake"}
                for i, c in enumerate(claims)
            ]
            return completion(json.dumps({"verdicts": verdicts}), model)
        ids = re.findall(r"not_[A-Za-z0-9]+:\d{4}", user)
        answer = {"answer": "fake answer", "abstain": not ids, "citations": ids[:1]}
        return completion(json.dumps(answer), model)

    def count(self, prompt_id: str) -> int:
        return sum(r.prompt_id == prompt_id for r in self.requests)

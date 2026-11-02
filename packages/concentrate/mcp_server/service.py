"""Read-only views of a built Concentrate memory, shaped for an agent's tool calls.

This module holds the logic behind every MCP tool and knows nothing about MCP, so the tools can
be tested as plain functions; :mod:`concentrate.mcp_server.server` only wraps them.

Everything is answered "as of" a date. The default is the day of the last meeting in the
memory (the memory's own "now"), never the wall clock, so the same call gives the same answer.
A fact the memory only learnt after the as-of date is hidden (transaction time), and a value
that had already been replaced shows as superseded (valid time).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from concentrate.agents.baselines import ANSWER_PROMPT, ANSWER_SCHEMA, Answer, _user_prompt
from concentrate.agents.concentrate_system import COMPLETE_MARKER, DB_FILE
from concentrate.agents.mem0_system import corpus_hash
from concentrate.agents.retrieval import Embedder
from concentrate.core.models import MeetingNote
from concentrate.core.store import NoteStore
from concentrate.llm.client import LLMClient, ToolBackend, TurnRequest, load_prompt, run_turn
from concentrate.memory.entities import Entity, EntityDirectory, load_directory
from concentrate.memory.query import MemoryIndex, build_context, describe, parse_question
from concentrate.memory.store import Commitment, Fact, MemoryStore
from concentrate.sources.fixtures import FixtureSource
from concentrate.sources.poller import sync

STATUSES = ("open", "no_follow_up", "done", "slipped", "reassigned", "dropped")
UNFINISHED = "unfinished"  # filter alias: anything not done or dropped
MAX_LINES = 200
MAX_FACT_ROWS = 80
MAX_COMMITMENT_ROWS = 60
_LINE_ID = re.compile(r"^(not_[A-Za-z0-9]+):(\d{4})$")
_SNAKE = re.compile(r"[^a-z0-9]+")


class MemoryQueryError(ValueError):
    """A request the memory can't serve as asked (unknown id, ambiguous name, bad date)."""


def _snake(s: str) -> str:
    return _SNAKE.sub("_", s.strip().lower()).strip("_")


@dataclass(frozen=True)
class _Question:
    id: str
    question: str
    as_of: date


@dataclass
class Answerer:
    """Optional final answer: the same ``answer.v1`` prompt, schema and model as the eval."""

    client: LLMClient
    backend: ToolBackend | None = None

    def answer(self, question: str, as_of: date, context: str) -> tuple[Answer, float]:
        """Answer from ``context`` only; returns the answer and this call's cost in USD."""
        user = f"{context}\n\n{_user_prompt(_Question('mcp', question, as_of))}"
        req = TurnRequest(
            stage="mcp.concentrate",
            prompt_id=ANSWER_PROMPT,
            system=load_prompt(ANSWER_PROMPT),
            messages=({"role": "user", "content": user},),
            schema=ANSWER_SCHEMA,
            max_tokens=8000,
        )
        before = len(self.client.ledger.records)
        completion = run_turn(self.client, req, self.backend)
        cost = sum(r.cost_usd for r in self.client.ledger.records[before:])
        return Answer.model_validate_json(completion.text), cost


class MemoryService:
    """The tools' logic over one built memory store plus the corpus it was built from."""

    def __init__(
        self,
        store: MemoryStore,
        directory: EntityDirectory,
        notes: list[MeetingNote],
        *,
        embedder: Embedder | None = None,
        answerer: Answerer | None = None,
        info: dict[str, Any] | None = None,
    ) -> None:
        self.store, self.directory, self.answerer = store, directory, answerer
        self.notes = {m.note_id: m for m in notes}
        self.meetings = {m["note_id"]: m for m in store.meetings()}
        self.index = MemoryIndex(store, directory, embedder)
        self.subjects = {f.subject for f in self.index.facts}
        self.info = info or {}

    # -- dates and names --

    @property
    def today(self) -> date:
        """The memory's "now": the day of the last meeting it has ingested."""
        days = [m["day"] for m in self.meetings.values()]
        return date.fromisoformat(max(days)) if days else date.min

    def _day(self, as_of: str | None) -> date:
        if not as_of:
            return self.today
        try:
            return date.fromisoformat(as_of.strip()[:10])
        except ValueError as exc:
            raise MemoryQueryError(
                f"as_of must be an ISO date (YYYY-MM-DD), got {as_of!r}"
            ) from exc

    def _entity(self, eid: str) -> dict[str, Any]:
        e = self.directory.get(eid)
        if e is None:
            return {"id": eid, "name": eid, "kind": "unknown (seen only in extracted facts)"}
        out: dict[str, Any] = {"id": e.id, "name": e.name, "kind": e.kind}
        if e.role:
            out["role"] = e.role
        if e.email:
            out["email"] = e.email
        if e.aliases:
            out["aliases"] = list(e.aliases)
        return out

    def _candidates(self, name: str) -> list[str]:
        """Entity ids ``name`` could mean: an exact id, directory names/aliases (a bare first
        name keeps every person who has it), then a word-prefix match, then fact subjects."""
        key = _snake(name)
        if self.directory.get(key) is not None:
            return [key]
        found = self.directory.find(name)
        if found:
            return found
        words = [w for w in _snake(name).split("_") if len(w) >= 2]
        if words:
            loose = [
                e.id
                for e in self.directory.entities
                if all(
                    any(t.startswith(w) for t in _snake(" ".join([e.name, *e.aliases])).split("_"))
                    for w in words
                )
            ]
            if loose:
                return loose
        return [key] if key in self.subjects else []

    def _one(self, name: str) -> str:
        ids = self._candidates(name)
        if not ids:
            raise MemoryQueryError(f"no entity matches {name!r}; try resolve_entity")
        if len(ids) > 1:
            options = "; ".join(f"{i} = {self._label(i)}" for i in ids)
            raise MemoryQueryError(
                f"{name!r} is ambiguous ({options}). Call again with one of these ids, "
                "or use resolve_entity to see the evidence for each."
            )
        return ids[0]

    def _label(self, eid: str) -> str:
        e: Entity | None = self.directory.get(eid)
        if e is None:
            return self.directory.name(eid)
        return e.label() + (f" <{e.email}>" if e.email else "")

    def _meeting_ref(self, note_id: str) -> dict[str, str]:
        meta = self.meetings.get(note_id)
        note = self.notes.get(note_id)
        title = (meta or {}).get("title") or (note.title if note else None) or "untitled"
        day = (meta or {}).get("day") or (note.created_at.date().isoformat() if note else "")
        return {"note_id": note_id, "title": title, "day": day}

    # -- facts --

    def _fact_row(self, f: Fact, day: str) -> dict[str, Any]:
        live = f.live_at(day, known_at=day)
        ended = f.valid_to is not None and (f.valid_to_recorded_at or "")[:10] <= day
        state = "current" if live else ("superseded" if ended else "not yet valid")
        row: dict[str, Any] = {
            "subject": f.subject,
            "predicate": f.predicate,
            "value": f.value,
            "valid_from": f.valid_from,
            "valid_to": f.valid_to if ended else None,
            "state": state,
            "recorded_at": f.recorded_at[:10],
            "meeting": self._meeting_ref(f.note_id),
            "quote": f.quote,
            "lines": f.all_lines(),
        }
        if f.reason:
            row["reason"] = f.reason
        if f.evidence:
            row["restated"] = len(f.evidence)
        if f.contradiction:
            row["contradicted"] = "another value was stated the same day; not resolved"
        if f.privacy != "none":
            row["privacy"] = f.privacy
        return row

    def facts(
        self, entity: str, predicate: str | None = None, as_of: str | None = None
    ) -> dict[str, Any]:
        """Every version of ``entity``'s facts known on ``as_of`` (bitemporal history)."""
        eid, day = self._one(entity), self._day(as_of).isoformat()
        mine = self.store.facts_for(eid)
        known = [f for f in mine if f.recorded_at[:10] <= day]
        if predicate:
            want = set(_snake(predicate).split("_"))
            known = [f for f in known if want <= set(f.predicate.split("_"))]
        known.sort(key=lambda f: (f.predicate, f.valid_from, f.id))
        rows = [self._fact_row(f, day) for f in known]
        about = [
            self._fact_row(f, day)
            for f in self.index.facts
            if f.subject != eid and f.value_norm == eid and f.recorded_at[:10] <= day
        ]
        return {
            "entity": self._entity(eid),
            "as_of": day,
            "facts": rows[:MAX_FACT_ROWS],
            "truncated": max(0, len(rows) - MAX_FACT_ROWS),
            "facts_naming_this_entity_as_value": about[:20],
            "learnt_after_as_of": len(mine) - len([f for f in mine if f.recorded_at[:10] <= day]),
            "how_to_read": (
                "valid_from/valid_to = when the value held in the world; recorded_at = the "
                "meeting where the memory learnt it. 'superseded' rows are history, not errors. "
                "Cite `lines` (transcript line IDs); get_transcript_lines shows them."
            ),
        }

    # -- commitments --

    def _commitment_row(self, c: Commitment, day: str) -> dict[str, Any]:
        st = c.status_at(day)
        row: dict[str, Any] = {
            "id": f"c{c.id}",
            "owner": self.directory.name(st["owner"]),
            "owner_id": st["owner"],
            "task": c.task,
            "due": st["due"],
            "made_on": c.made_on,
            "meeting": self._meeting_ref(c.note_id),
            "status": st["status"],
            "quote": c.quote,
            "lines": c.source_lines,
            "events": [
                {
                    "day": ev["day"],
                    "kind": ev["kind"],
                    **({"new_due": ev["new_due"]} if ev["new_due"] else {}),
                    **(
                        {"new_owner": self.directory.name(ev["new_owner"])}
                        if ev["new_owner"]
                        else {}
                    ),
                    "lines": ev["source_lines"],
                }
                for ev in c.events
                if ev["day"] <= day
            ],
        }
        if c.owner != st["owner"]:
            row["original_owner"] = self.directory.name(c.owner)
        if c.owner.startswith("?"):
            row["owner_unresolved"] = (
                "the transcript didn't say who; the memory kept the candidates instead of guessing"
            )
        return row

    def commitments(
        self, person: str | None = None, status: str | None = None, as_of: str | None = None
    ) -> dict[str, Any]:
        """Commitments (who promised what, by when) and their status on ``as_of``."""
        day = self._day(as_of).isoformat()
        if status and status not in (*STATUSES, UNFINISHED):
            raise MemoryQueryError(f"status must be one of {', '.join((*STATUSES, UNFINISHED))}")
        eid = self._one(person) if person else None
        rows = []
        for c in self.store.commitments():
            if c.made_on > day:
                continue
            st = c.status_at(day)
            if eid is not None:
                candidates = (
                    set(c.owner.lstrip("?").split("|")) if c.owner.startswith("?") else set()
                )
                if eid not in {c.owner, st["owner"]} and eid not in candidates:
                    continue
            if status == UNFINISHED and st["status"] in ("done", "dropped"):
                continue
            if status and status != UNFINISHED and st["status"] != status:
                continue
            rows.append(self._commitment_row(c, day))
        rows.sort(key=lambda r: (r["made_on"], r["id"]), reverse=True)
        return {
            "person": self._entity(eid) if eid else None,
            "as_of": day,
            "commitments": rows[:MAX_COMMITMENT_ROWS],
            "truncated": max(0, len(rows) - MAX_COMMITMENT_ROWS),
            "how_to_read": (
                "no_follow_up = never mentioned again after it was made (not the same as done). "
                "owner_unresolved = the speaker couldn't be identified (e.g. an in-person "
                "meeting on one microphone); the candidates are listed in owner."
            ),
        }

    # -- entities --

    def resolve(self, name: str) -> dict[str, Any]:
        """Which entity ``name`` refers to, with the evidence that tells candidates apart."""
        out = []
        for eid in self._candidates(name):
            e = self.directory.get(eid)
            attended = (
                [
                    self._meeting_ref(m.note_id)
                    for m in self.notes.values()
                    if any(p.email.lower() == e.email.lower() for p in (m.owner, *m.attendees))
                ]
                if e is not None and e.email
                else []
            )
            attended.sort(key=lambda r: r["day"])
            facts = [f for f in self.index.facts if f.subject == eid]
            current = [f for f in facts if f.valid_to is None][:6]
            owned = [c for c in self.store.commitments() if c.owner == eid]
            maybe = [
                c
                for c in self.store.commitments()
                if c.owner.startswith("?") and eid in c.owner[1:].split("|")
            ]
            discussed = sorted(
                {f.note_id for f in facts}, key=lambda n: self._meeting_ref(n)["day"]
            )
            out.append(
                {
                    **self._entity(eid),
                    "meetings_attended": len(attended),
                    "first_and_last_attended": [attended[0], attended[-1]] if attended else [],
                    "meetings_with_facts_about_them": len(discussed),
                    "facts": len(facts),
                    "sample_current_facts": [
                        {"predicate": f.predicate, "value": f.value, "lines": f.source_lines}
                        for f in current
                    ],
                    "commitments_owned": len(owned),
                    "commitments_possibly_theirs": len(maybe),
                }
            )
        return {
            "query": name,
            "ambiguous": len(out) > 1,
            "candidates": out,
            "how_to_read": (
                "Several candidates = the name alone is ambiguous: tell them apart by role, email "
                "domain and the meetings they attended. Pass the chosen `id` to other tools."
            ),
        }

    # -- meetings and transcript lines --

    def meeting(self, note_id: str) -> dict[str, Any]:
        """One meeting: metadata, Granola's summary, the memory's digest and what it yielded."""
        note_id = note_id.strip().rsplit("/", 1)[-1]  # accept a notes.granola.ai URL too
        note, meta = self.notes.get(note_id), self.meetings.get(note_id)
        if note is None and meta is None:
            raise MemoryQueryError(f"unknown meeting {note_id!r}")
        out: dict[str, Any] = self._meeting_ref(note_id)
        if note is not None:
            out |= {
                "web_url": note.web_url,
                "owner": {"name": note.owner.name, "email": note.owner.email},
                "attendees": [
                    {
                        "name": p.name,
                        "email": p.email,
                        "entity_id": e.id
                        if (e := self.directory.by_email.get(p.email.lower()))
                        else None,
                    }
                    for p in note.attendees
                ],
                "granola_summary": note.summary_markdown or note.summary_text,
                "transcript_lines": len(note.utterances),
            }
        if meta is not None:
            out["memory_digest"] = meta["digest"]
            facts = [f for f in self.index.facts if f.note_id == note_id]
            out["facts_from_this_meeting"] = [
                {
                    "subject": f.subject,
                    "predicate": f.predicate,
                    "value": f.value,
                    "lines": f.source_lines,
                }
                for f in facts[:40]
            ]
            out["commitments_made_here"] = [
                self._commitment_row(c, self.today.isoformat())
                for c in self.store.commitments()
                if c.note_id == note_id
            ]
        else:
            out["memory_digest"] = None
            out["note"] = "this meeting isn't in the memory yet (build still running?)"
        return out

    def lines(self, ids: list[str], context: int = 0) -> dict[str, Any]:
        """Transcript lines by ID (``not_xxx:0042``), with ``context`` neighbours each side."""
        if not ids:
            raise MemoryQueryError("pass at least one line id like not_AbC123:0042")
        context = max(0, min(context, 10))
        wanted: list[str] = []
        asked: set[str] = set()
        bad: list[str] = []
        for raw in ids:
            mt = _LINE_ID.match(raw.strip().strip("[]"))
            if mt is None:
                bad.append(raw)
                continue
            nid, i = mt.group(1), int(mt.group(2))
            asked.add(f"{nid}:{i:04d}")
            wanted += [f"{nid}:{j:04d}" for j in range(max(0, i - context), i + context + 1)]
        wanted = list(dict.fromkeys(wanted))[:MAX_LINES]
        attributed = self.store.line_texts(wanted)
        rows, missing = [], []
        for lid in wanted:
            nid, idx = lid.split(":")
            note = self.notes.get(nid)
            u = note.utterances[int(idx)] if note and int(idx) < len(note.utterances) else None
            if u is None and lid not in attributed:
                if lid in asked:  # a neighbour past the end of the meeting is just skipped
                    missing.append(lid)
                continue
            row: dict[str, Any] = {"id": lid, "meeting": self._meeting_ref(nid)}
            if u is not None:
                row |= {
                    "channel": u.channel.value,
                    "granola_speaker": u.speaker_name,
                    "start": u.start_time.isoformat(),
                    "text": u.text,
                }
            if lid in attributed:
                row["as_the_memory_read_it"] = attributed[lid].split("] ", 1)[-1]
            rows.append(row)
        return {
            "lines": rows,
            "missing": missing,
            "invalid_ids": bad,
            "how_to_read": (
                "channel me = the note owner's microphone, them = system audio (everyone else). "
                "granola_speaker is Granola's own label when it has one. as_the_memory_read_it "
                "shows Concentrate's speaker attribution; '? A|B' = unsure between A and B."
            ),
        }

    # -- the main entry point --

    def ask(self, question: str, as_of: str | None = None, answer: bool = False) -> dict[str, Any]:
        """The context Concentrate answers from (and optionally its own cited answer)."""
        if not question.strip():
            raise MemoryQueryError("question is empty")
        day = self._day(as_of)
        pq = parse_question(question, day, self.directory)
        ctx = build_context(self.index, question, pq)
        out: dict[str, Any] = {
            "question": question,
            "as_of": day.isoformat(),
            "understood_as": describe(pq, self.directory),
            "meetings_used": [self._meeting_ref(n) for n in ctx.meetings],
            "source_lines_shown": ctx.lines_shown,
            "context": ctx.text,
        }
        if answer:
            if self.answerer is None:
                out["answer_error"] = "this server was started without an answer model"
            else:
                try:
                    a, cost = self.answerer.answer(question, day, ctx.text)
                except Exception as exc:  # no key, network, refusal: keep the context usable
                    # The type only: exception text from an HTTP client can echo headers.
                    out["answer_error"] = f"answer model call failed ({type(exc).__name__})"
                    return out
                out["answer"] = a.model_dump()
                out["answer_model"] = self.answerer.client.model
                out["answer_cost_usd"] = round(cost, 5)
        return out

    def summary(self) -> dict[str, Any]:
        """What this server is serving (for the ``concentrate://memory`` resource)."""
        facts = self.index.facts
        return {
            **self.info,
            "meetings_in_memory": len(self.meetings),
            "meetings_in_corpus": len(self.notes),
            "first_day": min((m["day"] for m in self.meetings.values()), default=None),
            "default_as_of": self.today.isoformat(),
            "fact_rows": len(facts),
            "current_facts": sum(f.valid_to is None for f in facts),
            "commitments": len(self.store.commitments()),
            "entities_in_directory": len(self.directory.entities),
        }


# -- loading --------------------------------------------------------------------------------------


def load_notes(fixtures: Path) -> list[MeetingNote]:
    """Normalize a fixture set exactly as the eval runner does."""
    store = NoteStore(":memory:")
    sync(FixtureSource(fixtures), store)
    return list(store.iter_notes())


def find_store(cache_root: Path, notes: list[MeetingNote], allow_partial: bool = False) -> Path:
    """The built store for this corpus: the newest complete build, else (if allowed) the
    partial build with the most meetings. Builds live in ``<cache_root>/<corpus hash>/<cfg>/``."""
    root = cache_root / corpus_hash(notes)
    builds = sorted(p for p in root.glob("*/") if (p / DB_FILE).exists()) if root.exists() else []
    done = [p for p in builds if (p / COMPLETE_MARKER).exists()]
    if done:
        return max(done, key=lambda p: (p / COMPLETE_MARKER).stat().st_mtime)
    if builds and allow_partial:
        return max(builds, key=lambda p: len(MemoryStore(p / DB_FILE, read_only=True).meetings()))
    hint = f" ({len(builds)} partial build(s); pass --allow-partial to serve one)" if builds else ""
    raise FileNotFoundError(
        f"no finished Concentrate build for this corpus under {root}{hint}. Build it with "
        "`concentrate eval --systems c ...`, or point --store at a build directory."
    )


def open_memory(
    fixtures: Path,
    *,
    store_dir: Path | None = None,
    cache_root: Path = Path("data/cache/concentrate"),
    allow_partial: bool = False,
    embedder: Embedder | None = None,
    answerer: Answerer | None = None,
) -> MemoryService:
    """Open a built memory read-only, with the corpus and entity directory it was built from."""
    notes = load_notes(fixtures)
    store_dir = store_dir or find_store(cache_root, notes, allow_partial)
    store = MemoryStore(store_dir / DB_FILE, read_only=True)
    marker = store_dir / COMPLETE_MARKER
    built = json.loads(marker.read_text()) if marker.exists() else None
    info = {
        "fixtures": str(fixtures),
        "store": str(store_dir),
        "build_complete": built is not None,
        "build_config": (built or {}).get("detail", {}).get("config"),
    }
    directory = load_directory(fixtures / "directory.json", notes)
    return MemoryService(store, directory, notes, embedder=embedder, answerer=answerer, info=info)

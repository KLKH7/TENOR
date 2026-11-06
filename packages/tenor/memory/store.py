"""Consolidation: a bitemporal SQLite store of verified facts, commitments and digests.

Two clocks per fact:

* **valid time** (``valid_from`` / ``valid_to``): when the value was true in the world;
* **transaction time** (``recorded_at``, ``valid_to_recorded_at``): when the memory learnt the
  fact, and when it learnt the fact had ended. A meeting's start time is its transaction time.

Facts are keyed by ``(subject, predicate)``. A new value *supersedes* the open one (the old row
gets ``valid_to``; nothing is deleted), the same value restated adds *evidence*, and two
different values from the same day are flagged as a *contradiction* instead of being resolved.
Commitments move through ``open -> done | slipped | reassigned | dropped`` by appending events;
a commitment with no event is reported as "no follow-up", which is not the same as done.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  note_id TEXT PRIMARY KEY, title TEXT, day TEXT, recorded_at TEXT, seq INTEGER,
  digest TEXT, tiers TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, kind TEXT, value TEXT, value_norm TEXT,
  norm_key TEXT, valid_from TEXT, valid_to TEXT, recorded_at TEXT, valid_to_recorded_at TEXT,
  note_id TEXT, source_lines TEXT, quote TEXT, reason TEXT, confidence REAL, privacy TEXT,
  superseded_by INTEGER, contradiction INTEGER DEFAULT 0, gate TEXT
);
CREATE INDEX IF NOT EXISTS facts_key ON facts(subject, predicate);
CREATE TABLE IF NOT EXISTS evidence (
  fact_id INTEGER, note_id TEXT, source_lines TEXT, quote TEXT, recorded_at TEXT, confidence REAL
);
CREATE TABLE IF NOT EXISTS contradictions (fact_a INTEGER, fact_b INTEGER, day TEXT, note_id TEXT);
CREATE TABLE IF NOT EXISTS commitments (
  id INTEGER PRIMARY KEY, owner TEXT, task TEXT, due TEXT, note_id TEXT, made_on TEXT,
  recorded_at TEXT, source_lines TEXT, quote TEXT, confidence REAL
);
CREATE TABLE IF NOT EXISTS commitment_events (
  commitment_id INTEGER, kind TEXT, day TEXT, recorded_at TEXT, note_id TEXT, source_lines TEXT,
  quote TEXT, new_due TEXT, new_owner TEXT
);
CREATE TABLE IF NOT EXISTS rejections (
  note_id TEXT, item TEXT, reason TEXT, detail TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, first_seen TEXT);
CREATE TABLE IF NOT EXISTS lines (utterance_id TEXT PRIMARY KEY, note_id TEXT, text TEXT);
"""


def norm_value(value_norm: str) -> str:
    """Comparison key for "same value": lowercase alphanumerics only ("$25" == "25")."""
    return re.sub(r"[^a-z0-9]+", "", value_norm.lower())


@dataclass
class Fact:
    id: int
    subject: str
    predicate: str
    kind: str
    value: str
    value_norm: str
    valid_from: str
    valid_to: str | None
    recorded_at: str
    valid_to_recorded_at: str | None
    note_id: str
    source_lines: list[str]
    quote: str
    reason: str | None
    confidence: float
    privacy: str
    superseded_by: int | None
    contradiction: bool
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def all_lines(self) -> list[str]:
        out = list(self.source_lines)
        for e in self.evidence:
            out += [x for x in e["source_lines"] if x not in out]
        return out

    def live_at(self, day: str, known_at: str | None = None) -> bool:
        """True if valid on ``day`` according to what was known at ``known_at`` (ISO)."""
        if known_at is not None and self.recorded_at[:10] > known_at[:10]:
            return False
        end = self.valid_to
        if end is not None and known_at is not None:
            learnt = (self.valid_to_recorded_at or self.recorded_at)[:10]
            if learnt > known_at[:10]:
                end = None  # the end wasn't known yet at known_at
        return self.valid_from <= day and (end is None or day < end)


@dataclass
class Commitment:
    id: int
    owner: str
    task: str
    due: str | None
    note_id: str
    made_on: str
    source_lines: list[str]
    quote: str
    confidence: float
    events: list[dict[str, Any]] = field(default_factory=list)

    def status_at(self, day: str) -> dict[str, Any]:
        """Lifecycle state on ``day``: the last event on or before it, else open/no-follow-up."""
        owner, due, status, lines = self.owner, self.due, "open", list(self.source_lines)
        last: dict[str, Any] | None = None
        for ev in self.events:
            if ev["day"] > day:
                break
            last = ev
            status = ev["kind"]
            owner = ev["new_owner"] or owner
            due = ev["new_due"] or due
            lines += ev["source_lines"]
        if last is None:
            status = "no_follow_up" if self.made_on < day else "open"
        return {"status": status, "owner": owner, "due": due, "lines": lines, "last": last}


class MemoryStore:
    """The consolidated memory. One SQLite file; each meeting is ingested in one transaction."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        if read_only:
            # Serving a built store (the MCP server): SQLite refuses any write, and reads may come
            # from the server's worker threads (safe: the sqlite3 module serializes access).
            uri = f"{Path(path).resolve().as_uri()}?mode=ro"
            self.db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        if not read_only:
            self.db.executescript(_SCHEMA)
        self.counters = {"inserted": 0, "superseded": 0, "restated": 0, "contradictions": 0}

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.db:
            yield

    # -- meetings --

    def has_meeting(self, note_id: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM meetings WHERE note_id=?", (note_id,)).fetchone()
            is not None
        )

    def add_meeting(
        self,
        note_id: str,
        title: str,
        day: str,
        recorded_at: str,
        digest: str,
        tiers: dict[str, int],
        stats: dict[str, Any],
    ) -> None:
        seq = self.db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        self.db.execute(
            "INSERT INTO meetings VALUES (?,?,?,?,?,?,?,?)",
            (note_id, title, day, recorded_at, seq, digest, json.dumps(tiers), json.dumps(stats)),
        )

    def meetings(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM meetings ORDER BY seq").fetchall()
        return [
            {**dict(r), "tiers": json.loads(r["tiers"]), "stats": json.loads(r["stats"])}
            for r in rows
        ]

    # -- facts --

    def _fact(self, r: sqlite3.Row) -> Fact:
        ev = self.db.execute(
            "SELECT * FROM evidence WHERE fact_id=? ORDER BY recorded_at", (r["id"],)
        ).fetchall()
        return Fact(
            id=r["id"],
            subject=r["subject"],
            predicate=r["predicate"],
            kind=r["kind"],
            value=r["value"],
            value_norm=r["value_norm"],
            valid_from=r["valid_from"],
            valid_to=r["valid_to"],
            recorded_at=r["recorded_at"],
            valid_to_recorded_at=r["valid_to_recorded_at"],
            note_id=r["note_id"],
            source_lines=json.loads(r["source_lines"]),
            quote=r["quote"],
            reason=r["reason"],
            confidence=r["confidence"],
            privacy=r["privacy"],
            superseded_by=r["superseded_by"],
            contradiction=bool(r["contradiction"]),
            evidence=[{**dict(e), "source_lines": json.loads(e["source_lines"])} for e in ev],
        )

    def versions(self, subject: str, predicate: str) -> list[Fact]:
        rows = self.db.execute(
            "SELECT * FROM facts WHERE subject=? AND predicate=? ORDER BY valid_from, id",
            (subject, predicate),
        ).fetchall()
        return [self._fact(r) for r in rows]

    def all_facts(self) -> list[Fact]:
        rows = self.db.execute("SELECT * FROM facts ORDER BY valid_from, id").fetchall()
        return [self._fact(r) for r in rows]

    def facts_for(self, subject: str) -> list[Fact]:
        rows = self.db.execute(
            "SELECT * FROM facts WHERE subject=? ORDER BY predicate, valid_from, id", (subject,)
        ).fetchall()
        return [self._fact(r) for r in rows]

    def as_of(
        self, subject: str, predicate: str, day: str, known_at: str | None = None
    ) -> list[Fact]:
        """Values of ``(subject, predicate)`` valid on ``day`` (several = a contradiction)."""
        return [f for f in self.versions(subject, predicate) if f.live_at(day, known_at)]

    def fact_keys(self) -> list[tuple[str, str]]:
        rows = self.db.execute("SELECT DISTINCT subject, predicate FROM facts ORDER BY 1, 2")
        return [(r[0], r[1]) for r in rows]

    def add_fact(
        self,
        *,
        subject: str,
        predicate: str,
        kind: str,
        value: str,
        value_norm: str,
        valid_from: str,
        recorded_at: str,
        note_id: str,
        source_lines: list[str],
        quote: str,
        reason: str | None,
        confidence: float,
        privacy: str,
        gate: str = "",
    ) -> tuple[str, int]:
        """Consolidate one verified fact. Returns ``(action, fact_id)`` where action is
        ``inserted``, ``superseded`` (inserted and closed an earlier value), ``restated``
        (evidence added to an existing row) or ``contradiction`` (inserted, flagged)."""
        key = norm_value(value_norm or value)
        versions = self.versions(subject, predicate)
        # Same value as the version valid at valid_from -> more evidence, not a new fact.
        live = [
            f
            for f in versions
            if f.valid_from <= valid_from and (f.valid_to is None or valid_from < f.valid_to)
        ]
        for f in live:
            if norm_value(f.value_norm or f.value) == key:
                self.db.execute(
                    "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                    (f.id, note_id, json.dumps(source_lines), quote, recorded_at, confidence),
                )
                merged = 1 - (1 - f.confidence) * (1 - confidence)
                self.db.execute("UPDATE facts SET confidence=? WHERE id=?", (merged, f.id))
                self.counters["restated"] += 1
                return "restated", f.id
        nxt = [f for f in versions if f.valid_from > valid_from]
        valid_to = min(f.valid_from for f in nxt) if nxt else None
        cur = self.db.execute(
            "INSERT INTO facts (subject, predicate, kind, value, value_norm, norm_key, valid_from,"
            " valid_to, recorded_at, valid_to_recorded_at, note_id, source_lines, quote, reason,"
            " confidence, privacy, superseded_by, contradiction, gate)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,0,?)",
            (
                subject,
                predicate,
                kind,
                value,
                value_norm,
                key,
                valid_from,
                valid_to,
                recorded_at,
                recorded_at if valid_to else None,
                note_id,
                json.dumps(source_lines),
                quote,
                reason,
                confidence,
                privacy,
                gate,
            ),
        )
        new_id = int(cur.lastrowid or 0)
        self.counters["inserted"] += 1
        action = "inserted"
        for f in live:
            if f.valid_from == valid_from:  # same day, different value: flag, don't resolve
                self.db.execute(
                    "UPDATE facts SET contradiction=1 WHERE id IN (?,?)", (f.id, new_id)
                )
                self.db.execute(
                    "INSERT INTO contradictions VALUES (?,?,?,?)",
                    (f.id, new_id, valid_from, note_id),
                )
                self.counters["contradictions"] += 1
                action = "contradiction"
            else:  # supersede: close the earlier value, keep it for history
                self.db.execute(
                    "UPDATE facts SET valid_to=?, valid_to_recorded_at=?, superseded_by=?"
                    " WHERE id=?",
                    (valid_from, recorded_at, new_id, f.id),
                )
                self.counters["superseded"] += 1
                action = "superseded"
        return action, new_id

    def contradictions(self) -> list[tuple[int, int, str, str]]:
        rows = self.db.execute("SELECT * FROM contradictions").fetchall()
        return [(int(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]

    # -- commitments --

    def add_commitment(
        self,
        *,
        owner: str,
        task: str,
        due: str | None,
        note_id: str,
        made_on: str,
        recorded_at: str,
        source_lines: list[str],
        quote: str,
        confidence: float,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO commitments (owner, task, due, note_id, made_on, recorded_at,"
            " source_lines, quote, confidence) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                owner,
                task,
                due,
                note_id,
                made_on,
                recorded_at,
                json.dumps(source_lines),
                quote,
                confidence,
            ),
        )
        return int(cur.lastrowid or 0)

    def add_commitment_event(
        self,
        commitment_id: int,
        *,
        kind: str,
        day: str,
        recorded_at: str,
        note_id: str,
        source_lines: list[str],
        quote: str,
        new_due: str | None,
        new_owner: str | None,
    ) -> None:
        self.db.execute(
            "INSERT INTO commitment_events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                commitment_id,
                kind,
                day,
                recorded_at,
                note_id,
                json.dumps(source_lines),
                quote,
                new_due,
                new_owner,
            ),
        )

    def commitments(self) -> list[Commitment]:
        out = []
        for r in self.db.execute("SELECT * FROM commitments ORDER BY made_on, id").fetchall():
            evs = self.db.execute(
                "SELECT * FROM commitment_events WHERE commitment_id=? ORDER BY day, rowid",
                (r["id"],),
            ).fetchall()
            out.append(
                Commitment(
                    id=r["id"],
                    owner=r["owner"],
                    task=r["task"],
                    due=r["due"],
                    note_id=r["note_id"],
                    made_on=r["made_on"],
                    source_lines=json.loads(r["source_lines"]),
                    quote=r["quote"],
                    confidence=r["confidence"],
                    events=[
                        {**dict(e), "source_lines": json.loads(e["source_lines"])} for e in evs
                    ],
                )
            )
        return out

    # -- L1 lines (enhanced transcript; dropped from context when a meeting is retired) --

    def add_lines(self, note_id: str, lines: list[tuple[str, str]]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO lines VALUES (?,?,?)", [(u, note_id, t) for u, t in lines]
        )

    def all_line_texts(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.db.execute("SELECT utterance_id, text FROM lines")}

    def line_texts(self, ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            q = f"SELECT utterance_id, text FROM lines WHERE utterance_id IN ({marks})"
            out.update({r[0]: r[1] for r in self.db.execute(q, chunk)})
        return out

    # -- rejections / entities --

    def reject(
        self, note_id: str, item: str, reason: str, detail: str, payload: dict[str, Any]
    ) -> None:
        self.db.execute(
            "INSERT INTO rejections VALUES (?,?,?,?,?)",
            (note_id, item, reason, detail, json.dumps(payload, ensure_ascii=False)),
        )

    def rejections(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM rejections").fetchall()]

    def note_entity(self, entity_id: str, day: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO entities VALUES (?,?)", (entity_id, day))

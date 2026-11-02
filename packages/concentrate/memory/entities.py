"""The entity directory the memory is keyed by (people, orgs, projects) and name matching.

This is deliberately simple string matching (full names, aliases, unambiguous first names). The
Enhance stage (``concentrate.enhance``) owns the hard cases - phonetic matches, email handles,
speaker attribution; here we only need stable ids for the extractor and for question parsing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from concentrate.core.models import MeetingNote

EntityKind = Literal["person", "org", "project", "other"]


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    kind: EntityKind
    aliases: tuple[str, ...] = ()
    role: str | None = None
    email: str | None = None

    def label(self) -> str:
        extra = f", {self.role}" if self.role else ""
        return f"{self.name} ({self.kind}{extra})"


@dataclass
class EntityDirectory:
    entities: list[Entity] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.by_id = {e.id: e for e in self.entities}
        self.by_email = {e.email.lower(): e for e in self.entities if e.email}

    def get(self, entity_id: str) -> Entity | None:
        return self.by_id.get(entity_id)

    def name(self, entity_id: str) -> str:
        if entity_id.startswith("?"):  # unresolved owner: "?sam_p|kofi"
            names = [self.name(c) for c in entity_id[1:].split("|") if c]
            return f"unresolved ({' or '.join(names)})" if names else "unresolved"
        e = self.by_id.get(entity_id)
        return e.name if e else entity_id

    def add(self, entity: Entity) -> None:
        if entity.id not in self.by_id:
            self.entities.append(entity)
            self.by_id[entity.id] = entity
            if entity.email:
                self.by_email[entity.email.lower()] = entity

    def prompt_block(self) -> str:
        """The directory as the extractor sees it (stable: part of the cached system prompt)."""
        rows = []
        for e in self.entities:
            aka = f"; aka {', '.join(e.aliases)}" if e.aliases else ""
            rows.append(f"- {e.id}: {e.label()}{aka}")
        return "\n".join(rows)

    def attendee_ids(self, m: MeetingNote) -> list[str]:
        out = []
        for p in m.attendees:
            e = self.by_email.get(p.email.lower())
            out.append(e.id if e else p.email)
        return out

    def find(self, text: str) -> list[str]:
        """Entity ids mentioned in ``text``, longest names first (so "Beacon Freight" wins over
        "Beacon"). A first name maps to every person who has it (ambiguity is kept)."""
        low = text.lower()
        taken: list[tuple[int, int]] = []
        found: list[str] = []
        for surface, ids in self._surfaces():
            for mt in re.finditer(rf"(?<![a-z0-9]){re.escape(surface)}(?![a-z0-9])", low):
                span = (mt.start(), mt.end())
                if any(a < span[1] and span[0] < b for a, b in taken):
                    continue
                taken.append(span)
                found += [i for i in ids if i not in found]
        return found

    def _surfaces(self) -> list[tuple[str, list[str]]]:
        table: dict[str, list[str]] = {}
        for e in self.entities:
            names = [e.name, *e.aliases]
            if e.kind == "person":
                names.append(e.name.split()[0])
            for n in names:
                key = n.lower().strip()
                if len(key) >= 3 and e.id not in table.setdefault(key, []):
                    table[key].append(e.id)
        return sorted(table.items(), key=lambda kv: -len(kv[0]))


def load_directory(path: Path | None, notes: Iterable[MeetingNote] = ()) -> EntityDirectory:
    """Load ``directory.json`` (people/orgs/projects) and add attendees it doesn't know."""
    d = EntityDirectory()
    if path is not None and path.exists():
        raw = json.loads(path.read_text())
        for p in raw.get("people", []):
            d.add(Entity(p["id"], p["name"], "person", (), p.get("role"), p.get("email")))
        for o in raw.get("orgs", []):
            d.add(Entity(o["id"], o["name"], "org", tuple(o.get("aliases", [])), o.get("kind")))
        for pr in raw.get("projects", []):
            d.add(Entity(pr["id"], pr["name"], "project", tuple(pr.get("aliases", []))))
    for m in notes:
        for person in (m.owner, *m.attendees):
            if person.email.lower() not in d.by_email:
                pid = re.sub(r"[^a-z0-9]+", "_", person.email.lower().split("@")[0]).strip("_")
                d.add(Entity(pid, person.name or person.email, "person", (), None, person.email))
    return d


def mentioned(directory: EntityDirectory, texts: Sequence[str]) -> list[str]:
    """Entity ids mentioned anywhere in ``texts`` (order of first mention)."""
    out: list[str] = []
    for t in texts:
        out += [i for i in directory.find(t) if i not in out]
    return out

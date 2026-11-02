"""The org directory a deployment knows: people (HRIS/CRM), customer orgs, project glossary.

Loaded from ``directory.json`` (written by ``concentrate synth``). It deliberately has no ASR
variants: the enhancer must cope with misheard names on its own.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from concentrate.enhance.strings import fold


class DirectoryPerson(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    email: str
    role: str
    team: str
    org: str

    @property
    def first(self) -> str:
        return self.name.split()[0]

    @property
    def last(self) -> str:
        return self.name.split()[-1]


class DirectoryOrg(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    kind: str
    domain: str


class DirectoryProject(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""


class Directory(BaseModel):
    """People, orgs and projects with lookup helpers (all lookups are diacritic-insensitive)."""

    model_config = ConfigDict(frozen=True)

    people: tuple[DirectoryPerson, ...]
    orgs: tuple[DirectoryOrg, ...] = ()
    projects: tuple[DirectoryProject, ...] = ()

    @classmethod
    def load(cls, path: Path) -> Directory:
        """Load ``directory.json`` (or a fixture directory containing it)."""
        if path.is_dir():
            path = path / "directory.json"
        return cls.model_validate_json(path.read_bytes())

    @cached_property
    def by_id(self) -> dict[str, DirectoryPerson]:
        return {p.id: p for p in self.people}

    @cached_property
    def by_email(self) -> dict[str, DirectoryPerson]:
        return {p.email.lower(): p for p in self.people}

    @cached_property
    def by_full_name(self) -> dict[str, list[DirectoryPerson]]:
        out: dict[str, list[DirectoryPerson]] = {}
        for p in self.people:
            out.setdefault(fold(p.name), []).append(p)
        return out

    @cached_property
    def by_first_name(self) -> dict[str, list[DirectoryPerson]]:
        out: dict[str, list[DirectoryPerson]] = {}
        for p in self.people:
            out.setdefault(fold(p.first), []).append(p)
        return out

    def person(self, pid: str) -> DirectoryPerson | None:
        return self.by_id.get(pid)

    def label(self, pid: str) -> str:
        """'Sam Patel (Backend Engineer)' or 'Owen Price (Head of IT, Beacon Freight)'."""
        p = self.by_id.get(pid)
        if p is None:
            return pid
        if p.team == "external":
            return f"{p.name} ({p.role}, {p.org})"
        return f"{p.name} ({p.role})"

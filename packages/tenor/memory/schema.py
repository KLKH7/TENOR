"""What the extractor returns (``extract.v2``) and the JSON schema that constrains it.

Line references inside an extraction are *local* 4-digit indexes (``"0037"``) to keep prompts
small; :mod:`tenor.memory.extract` maps them to canonical utterance IDs
(``not_x:0037``) before anything is verified or stored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

FactKind = Literal[
    "decision", "ownership", "status", "metric", "date", "plan", "request", "deal", "attribute",
    "other",
]  # fmt: skip
Privacy = Literal["none", "health", "family", "compensation", "personal"]
UpdateKind = Literal["done", "slipped", "reassigned", "dropped"]
FACT_KINDS: tuple[str, ...] = FactKind.__args__  # type: ignore[attr-defined]
PRIVACY: tuple[str, ...] = Privacy.__args__  # type: ignore[attr-defined]
UPDATE_KINDS: tuple[str, ...] = UpdateKind.__args__  # type: ignore[attr-defined]


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str  # directory id (person/org/project) or a new snake_case id
    predicate: str  # snake_case attribute, e.g. "owner", "price_per_seat", "launch_date"
    kind: FactKind
    value: str  # human-readable value
    value_norm: str  # canonical: ISO date, plain number, person id, short phrase
    valid_from: str | None  # ISO date the value became true; None = the meeting date
    source_lines: list[str]
    quote: str  # verbatim supporting text from the cited lines
    reason: str | None
    confidence: float
    privacy: Privacy


UNRESOLVED = "unresolved"  # owner when the speaker can't be pinned down; see owner_candidates


class ExtractedCommitment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str  # person id, or UNRESOLVED
    owner_candidates: list[str] = []  # person ids the owner could be, when UNRESOLVED
    task: str
    due: str | None  # ISO date
    source_lines: list[str]
    quote: str
    confidence: float


class ExtractedUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commitment_id: str | None  # id from the open-commitments list, when known
    owner: str  # person id, or UNRESOLVED
    owner_candidates: list[str] = []
    task: str
    kind: UpdateKind
    new_due: str | None
    new_owner: str | None
    source_lines: list[str]
    quote: str
    confidence: float


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[ExtractedFact]
    commitments: list[ExtractedCommitment]
    commitment_updates: list[ExtractedUpdate]
    digest: str


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_STR: dict[str, Any] = {"type": "string"}
_NSTR: dict[str, Any] = {"type": ["string", "null"]}
_LINES: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_NUM: dict[str, Any] = {"type": "number"}

EXTRACTION_SCHEMA: dict[str, Any] = _obj(
    {
        "facts": {
            "type": "array",
            "items": _obj(
                {
                    "subject": _STR,
                    "predicate": _STR,
                    "kind": {"type": "string", "enum": list(FACT_KINDS)},
                    "value": _STR,
                    "value_norm": _STR,
                    "valid_from": _NSTR,
                    "source_lines": _LINES,
                    "quote": _STR,
                    "reason": _NSTR,
                    "confidence": _NUM,
                    "privacy": {"type": "string", "enum": list(PRIVACY)},
                }
            ),
        },
        "commitments": {
            "type": "array",
            "items": _obj(
                {
                    "owner": _STR,
                    "owner_candidates": _LINES,
                    "task": _STR,
                    "due": _NSTR,
                    "source_lines": _LINES,
                    "quote": _STR,
                    "confidence": _NUM,
                }
            ),
        },
        "commitment_updates": {
            "type": "array",
            "items": _obj(
                {
                    "commitment_id": _NSTR,
                    "owner": _STR,
                    "owner_candidates": _LINES,
                    "task": _STR,
                    "kind": {"type": "string", "enum": list(UPDATE_KINDS)},
                    "new_due": _NSTR,
                    "new_owner": _NSTR,
                    "source_lines": _LINES,
                    "quote": _STR,
                    "confidence": _NUM,
                }
            ),
        },
        "digest": _STR,
    }
)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    reason: str
    entailed: bool


class Entailment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict]


ENTAILMENT_SCHEMA: dict[str, Any] = _obj(
    {
        "verdicts": {
            "type": "array",
            "items": _obj(
                {"index": {"type": "integer"}, "reason": _STR, "entailed": {"type": "boolean"}}
            ),
        }
    }
)

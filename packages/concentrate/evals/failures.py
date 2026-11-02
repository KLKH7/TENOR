"""Failure taxonomy: why was each wrong answer wrong?

Cheap, deterministic signals come first; every one is computed from the saved report row plus
the benchmark's ground truth (``questions.jsonl``, ``world.json``, ``directory.json``), so it is
free, reproducible and auditable. An optional LLM classifier (``llm_request``) can then refine
only the answers the deterministic rules leave in a generic bucket.

Deterministic failure types, in priority order (an answer can carry several tags; its *primary*
type is the first that applies):

1. ``abstained_wrongly``: declined although the question has an answer.
2. ``should_have_abstained``: answered an unanswerable question.
3. ``stale_value``: states a non-gold value from the same fact's history (e.g. the old owner
   or price), i.e. an older (or, for "as of" questions, a different) version of the truth.
4. ``wrong_entity``: names a different person/org/project sharing the gold entity's first name
   or first word (the other Sam; Beacon the project vs Beacon Freight the customer).
5. ``partial_list``: a multi-item answer with some but not all gold items.
6. ``retrieval_miss``: cites no gold evidence (no gold line; for systems that cite only
   meetings, no gold meeting). Detail says whether it cited the wrong meeting or nothing.
7. ``reasoning_or_reading``: cited gold evidence but still answered wrong.
8. ``unclassified``: none of the above could be decided (e.g. no ground truth available).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from concentrate.evals.scoring import _norm_present
from concentrate.llm.client import LLMRequest, load_prompt
from concentrate.synth.questions import UNANSWERABLE, Question

FAILURE_TYPES = (
    "abstained_wrongly",
    "should_have_abstained",
    "stale_value",
    "wrong_entity",
    "partial_list",
    "retrieval_miss",
    "reasoning_or_reading",
    "unclassified",
)
GENERIC_TYPES = frozenset({"retrieval_miss", "reasoning_or_reading", "unclassified"})


# -- ground truth --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    kind: str  # person | org | project


@dataclass
class GroundTruth:
    """What the deterministic rules need from a fixture set."""

    questions: dict[str, Question]
    facts: dict[str, dict[str, Any]]
    commitments: dict[str, dict[str, Any]]
    entities: dict[str, Entity]
    chains: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def load(cls, fixtures: Path) -> GroundTruth:
        questions = {
            q.id: q
            for q in (
                Question.model_validate_json(line)
                for line in (fixtures / "questions.jsonl").read_text().splitlines()
                if line.strip()
            )
        }
        world = json.loads((fixtures / "world.json").read_text())
        directory = json.loads((fixtures / "directory.json").read_text())
        entities = {
            e["id"]: Entity(e["id"], e["name"], kind)
            for kind, key in (("person", "people"), ("org", "orgs"), ("project", "projects"))
            for e in directory.get(key, [])
        }
        facts = {f["id"]: f for f in world.get("facts", [])}
        chains: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for f in facts.values():
            chains[(f["subject"], f["predicate"])].append(f)
        return cls(
            questions=questions,
            facts=facts,
            commitments={c["id"]: c for c in world.get("commitments", [])},
            entities=entities,
            chains=dict(chains),
        )


# -- signals -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tag:
    type: str
    detail: str = ""


@dataclass
class Failure:
    """One wrong answer and its failure tags."""

    system: str
    qid: str
    category: str
    verdict: str
    tags: list[Tag]
    llm_type: str | None = None
    llm_reason: str | None = None

    @property
    def primary(self) -> str:
        ranked = sorted(self.tags, key=lambda t: FAILURE_TYPES.index(t.type))
        return ranked[0].type if ranked else "unclassified"

    def to_json(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "qid": self.qid,
            "category": self.category,
            "verdict": self.verdict,
            "primary": self.primary,
            "tags": [{"type": t.type, "detail": t.detail} for t in self.tags],
            "llm_type": self.llm_type,
            "llm_reason": self.llm_reason,
        }


def _meeting(cite: str) -> str:
    return cite.strip("[]").split(":")[0]


def retrieval_signal(row: dict[str, Any], q: Question | None) -> Tag | None:
    """``retrieval_miss`` / ``reasoning_or_reading`` from citations vs gold evidence."""
    citations = [c.strip("[]") for c in row["answer"].get("citations") or []]
    if q is None:  # no ground truth: fall back to the row's own citation recall
        recall = row.get("cite_recall")
        if recall is None:
            return None
        if recall > 0:
            return Tag("reasoning_or_reading", "cited a gold line")
        return Tag("retrieval_miss", "no gold line cited")
    gold_lines = set(q.gold_utterance_ids)
    if not gold_lines:
        return None
    gold_meetings = {_meeting(g) for g in gold_lines}
    lines = {c for c in citations if ":" in c}
    meetings = {_meeting(c) for c in citations}
    retrieved = set(row.get("retrieved") or [])
    not_retrieved = bool(retrieved) and not retrieved & gold_meetings
    suffix = "; gold meeting not among retrieved" if not_retrieved else ""
    hit = bool(lines & gold_lines) if lines else bool(meetings & gold_meetings)
    if hit:
        return Tag("reasoning_or_reading", "cited gold " + ("line" if lines else "meeting"))
    if not citations:
        return Tag("retrieval_miss", "no citations" + suffix)
    if meetings & gold_meetings:
        return Tag("retrieval_miss", "right meeting, no gold line" + suffix)
    return Tag("retrieval_miss", "wrong meeting" + suffix)


def stale_signal(answer: str, q: Question, gt: GroundTruth) -> Tag | None:
    """The answer states a superseded or other-version value of the question's own fact."""
    gold = set(q.answer_norm)
    if all(_norm_present(n, answer) for n in gold):
        return None
    for sid in q.sources:
        candidates: list[tuple[str, str]] = []  # (value_norm, description)
        if sid in gt.facts:
            f = gt.facts[sid]
            chain = gt.chains.get((f["subject"], f["predicate"]), [])
            for other in chain:
                v = other.get("value_norm")
                if other["id"] != sid and v and v not in gold:
                    when = "older" if other["valid_from"] < f["valid_from"] else "newer"
                    candidates.append((v, f"{when} {f['subject']}.{f['predicate']} = {v!r}"))
        elif sid in gt.commitments:
            c = gt.commitments[sid]
            if c.get("new_owner") in gold and c.get("owner"):
                candidates.append((c["owner"], f"original owner {c['owner']!r}"))
            if c.get("new_due") in gold and c.get("due"):
                candidates.append((c["due"], f"original due date {c['due']}"))
        for value, desc in candidates:
            if _norm_present(value, answer):
                return Tag("stale_value", desc)
    return None


def _mentions(entity: Entity, text: str, others: list[Entity]) -> bool:
    """Is ``entity`` named in ``text``, not merely as part of a longer confusable name?"""
    t = text.lower()
    for o in others:  # "Beacon" must not match inside "Beacon Freight"
        if len(o.name) > len(entity.name) and entity.name.lower() in o.name.lower():
            t = t.replace(o.name.lower(), " ")
    if re.search(rf"\b{re.escape(entity.name.lower())}\b", t):
        return True
    if entity.kind == "person" and " " in entity.name:
        last = entity.name.split()[-1].lower()
        return bool(re.search(rf"\b{re.escape(last)}\b", t))
    return False


def _gold_entities(q: Question, gt: GroundTruth) -> set[str]:
    ids: set[str] = {n for n in q.answer_norm if n in gt.entities}
    for sid in q.sources:
        f = gt.facts.get(sid)
        if f:
            ids |= {x for x in (f["subject"], f.get("value_norm")) if x in gt.entities}
        c = gt.commitments.get(sid)
        if c:
            ids |= {x for x in (c.get("owner"), c.get("new_owner")) if x in gt.entities}
    text = f"{q.question} {q.answer}"
    ids |= {e.id for e in gt.entities.values() if _mentions(e, text, list(gt.entities.values()))}
    return ids


def entity_signal(answer: str, q: Question, gt: GroundTruth) -> Tag | None:
    """The answer names a look-alike of a gold entity (same first name/word) instead of it."""
    everyone = list(gt.entities.values())
    gold = _gold_entities(q, gt)
    for gid in sorted(gold):
        g = gt.entities[gid]
        first = g.name.split()[0].lower()
        lookalikes = [
            e for e in everyone if e.id not in gold and e.name.split()[0].lower() == first
        ]
        if not lookalikes or _mentions(g, answer, everyone):
            continue
        for e in lookalikes:
            if _mentions(e, answer, everyone):
                return Tag("wrong_entity", f"named {e.name} instead of {g.name}")
    return None


def partial_signal(row: dict[str, Any], q: Question | None) -> Tag | None:
    n_gold = len(q.answer_norm) if q else 0
    em = float(row.get("exact_match") or 0.0)
    if q is not None and n_gold > 1 and 0 < em < 1:
        return Tag("partial_list", f"{round(em * n_gold)}/{n_gold} gold items")
    if row.get("verdict") == "partially_correct" and (q is None or n_gold > 1):
        return Tag("partial_list", "judge: partially correct")
    return None


def classify(row: dict[str, Any], gt: GroundTruth | None) -> Failure:
    """All deterministic tags for one wrong answer (a report ``.jsonl`` row)."""
    q = gt.questions.get(row["qid"]) if gt else None
    if q is not None and q.question != row.get("question", q.question):
        q = None  # the report was run on a different fixture set: don't trust its ground truth
    answer = row["answer"].get("answer", "")
    abstained = bool(row["abstained"])
    unanswerable = bool(row["unanswerable"])
    tags: list[Tag] = []
    if abstained and not unanswerable:
        tags.append(Tag("abstained_wrongly"))
    elif unanswerable and not abstained:
        tags.append(Tag("should_have_abstained"))
    else:
        if q is not None and gt is not None and q.answer_norm != [UNANSWERABLE]:
            tags += [t for t in (stale_signal(answer, q, gt), entity_signal(answer, q, gt)) if t]
        tags += [t for t in (partial_signal(row, q), retrieval_signal(row, q)) if t]
    return Failure(row["system"], row["qid"], row["category"], row["verdict"], tags)


def failures(rows: list[dict[str, Any]], gt: GroundTruth | None) -> list[Failure]:
    """Classify every wrong answer (verdict other than "correct")."""
    return [classify(r, gt) for r in rows if not r["correct"]]


def failure_table(
    fails: list[Failure], n_examples: int = 3
) -> dict[str, dict[str, tuple[int, list[str]]]]:
    """``{system: {primary type: (count, first example qids)}}`` (examples sorted by qid)."""
    cells: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for f in fails:
        cells[f.system][f.primary].append(f.qid)
    return {
        s: {t: (len(q), sorted(q)[:n_examples]) for t, q in by_type.items()}
        for s, by_type in cells.items()
    }


# -- optional LLM refinement ----------------------------------------------------------------------

CLASSIFY_PROMPT = "failure_classify.v1"
LLM_TYPES = (
    "retrieval_miss",
    "misread_evidence",
    "temporal_confusion",
    "entity_confusion",
    "stale_value",
    "partial_list",
    "counting_or_aggregation",
    "hallucination",
    "over_abstention",
    "judge_error",
    "other",
)
LLMType = Literal[
    "retrieval_miss",
    "misread_evidence",
    "temporal_confusion",
    "entity_confusion",
    "stale_value",
    "partial_list",
    "counting_or_aggregation",
    "hallucination",
    "over_abstention",
    "judge_error",
    "other",
]


class LLMFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    type: LLMType


LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "type": {"type": "string", "enum": list(LLM_TYPES)},
    },
    "required": ["reason", "type"],
    "additionalProperties": False,
}


def llm_request(row: dict[str, Any], failure: Failure) -> LLMRequest:
    """Classifier call for one generic-bucket failure (reason first, then the label)."""
    gold = "UNANSWERABLE" if row["unanswerable"] else row["gold"]
    signals = "; ".join(f"{t.type} ({t.detail})" if t.detail else t.type for t in failure.tags)
    user = (
        f"Category: {row['category']}\nQuestion: {row['question']}\nGold answer: {gold}\n"
        f"System answer{' (abstained)' if row['abstained'] else ''}: "
        f"{row['answer'].get('answer', '')}\n"
        f"Judge verdict: {row['verdict']}\nDeterministic signals: {signals or 'none'}"
    )
    return LLMRequest(
        stage="analysis.failure_classify",
        prompt_id=CLASSIFY_PROMPT,
        system=load_prompt(CLASSIFY_PROMPT),
        user=user,
        schema=LLM_SCHEMA,
        max_tokens=400,
        effort="low",
    )

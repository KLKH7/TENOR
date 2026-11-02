"""End-to-end synthetic dataset generation: world → pad → (polish) → render → questions.

Output layout (``<out>/``), readable by ``FixtureSource``::

    notes/<note_id>.json   exact Granola Get Note bodies (with transcript)
    folders.json           exact Granola List Folders body
    world.json             ground truth: meetings (beats), facts, commitments
    directory.json         org directory + glossary (people, customers, projects/aliases)
    questions.jsonl        benchmark questions with gold answers and evidence
    manifest.json          seed, generator version, noise rates, polish stats, validation

Determinism: with ``polish=False`` the same seed always yields byte-identical files. With
``polish=True`` it does too once the LLM cache is warm (the cache is the source of truth).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from concentrate.core import granola
from concentrate.llm.client import LLMClient, cost_usd
from concentrate.synth.noise import NoiseRates
from concentrate.synth.pad import pad_world
from concentrate.synth.polish import PROMPT_ID, PolishStats, estimate_cost_tokens, polish_meeting
from concentrate.synth.questions import ValidationReport, generate, select_benchmark, validate
from concentrate.synth.render import BeatMap, folder_models, folders_output, render_meeting
from concentrate.synth.roster import ALL_ORGS, ALL_PROJECTS, ORGS, PROJECTS, STAFF
from concentrate.synth.scenario import build_world
from concentrate.synth.world import World

GENERATOR_VERSION = "northwind-1"


class SynthResult(BaseModel):
    seed: int
    meetings: int
    utterances: int
    words: int
    questions: int
    validation: ValidationReport
    polish: PolishStats | None
    llm_spent_usd: float


def directory(scale: str = "base") -> dict[str, Any]:
    """The org directory + glossary a deployment would have (HRIS, CRM, project tracker).

    Deliberately excludes ``asr_variants``: a real system doesn't know how ASR will mishear
    names, so linkers must cope with misspellings themselves.
    """
    orgs = ALL_ORGS if scale == "xl" else ORGS
    projects = ALL_PROJECTS if scale == "xl" else PROJECTS
    return {
        "people": [
            {
                "id": p.id,
                "name": p.name,
                "email": p.email,
                "role": p.role,
                "team": p.team,
                "org": p.org,
            }
            for p in [*STAFF, *(c for o in orgs for c in o.contacts)]
        ],
        "orgs": [{"id": o.id, "name": o.name, "kind": o.kind, "domain": o.domain} for o in orgs],
        "projects": [
            {"id": p.id, "name": p.name, "aliases": list(p.aliases), "description": p.description}
            for p in projects
        ],
    }


# Northwind-XL meetings are padded longer: real 30-minute meetings run thousands of words.
PAD_FACTOR = {"base": 1.0, "xl": 2.5}


def build(seed: int, scale: str = "base") -> World:
    """The padded ground-truth world for ``seed`` (no rendering)."""
    return pad_world(build_world(seed, scale), seed, PAD_FACTOR[scale])


# Current Claude models think adaptively even at low effort; thinking is billed as output.
# Unmeasured until the first real run, so budget a conservative allowance.
THINKING_OVERHEAD = 1.3


def estimate_polish_usd(world: World, model: str) -> tuple[int, int, float]:
    """(input tokens, output tokens incl. thinking allowance, USD) for polishing ``world``."""
    tin, tout = estimate_cost_tokens(world.meetings)
    tout = round(tout * THINKING_OVERHEAD)
    return tin, tout, cost_usd(model, tin, tout)


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def generate_dataset(
    seed: int,
    out: Path,
    *,
    llm: LLMClient | None = None,
    rates: NoiseRates | None = None,
    scale: str = "base",
) -> SynthResult:
    """Generate and write the dataset. ``llm=None`` means template-only rendering.

    At ``scale="xl"`` every generated question goes to ``questions_all.jsonl`` and a stratified
    benchmark subset (``select_benchmark``) to ``questions.jsonl``.
    """
    rates = rates or NoiseRates()
    world = build(seed, scale)
    stats = PolishStats() if llm else None
    folders = folder_models(seed, scale)
    notes: list[granola.Note] = []
    beat_map: BeatMap = {}
    for meeting in world.meetings:
        texts = (
            polish_meeting(meeting, llm, stats)
            if llm is not None and stats is not None
            else [b.text for b in meeting.beats]
        )
        note, bm = render_meeting(meeting, texts, world, seed, rates, folders)
        notes.append(note)
        beat_map.update(bm)

    all_questions = generate(world, beat_map, seed)
    questions = select_benchmark(all_questions, seed) if scale == "xl" else all_questions
    report = validate(questions, notes)

    (out / "notes").mkdir(parents=True, exist_ok=True)
    for stale in (out / "notes").glob("*.json"):
        stale.unlink()
    for note in notes:
        (out / "notes" / f"{note.id}.json").write_text(
            _dump(note.model_dump(mode="json", exclude_unset=True))
        )
    (out / "folders.json").write_text(
        _dump(folders_output(seed, scale).model_dump(mode="json", exclude_unset=True))
    )
    (out / "world.json").write_text(_dump(world.model_dump(mode="json")))
    (out / "directory.json").write_text(_dump(directory(scale)))
    if scale == "xl":
        (out / "questions_all.jsonl").write_text(
            "".join(q.model_dump_json() + "\n" for q in all_questions)
        )
    (out / "questions.jsonl").write_text("".join(q.model_dump_json() + "\n" for q in questions))
    utterances = sum(len(n.transcript or []) for n in notes)
    words = sum(len(i.text.split()) for n in notes for i in n.transcript or [])
    manifest = {
        "generator": GENERATOR_VERSION,
        "scale": scale,
        "seed": seed,
        "polish": {
            "enabled": llm is not None,
            "prompt": PROMPT_ID if llm else None,
            "model": llm.model if llm else None,
            "stats": stats.model_dump() if stats else None,
        },
        "noise_rates": rates.model_dump(),
        "counts": {
            "meetings": len(notes),
            "utterances": utterances,
            "words": words,
            "facts": len(world.facts),
            "commitments": len(world.commitments),
            "questions": len(questions),
        },
        "validation": report.model_dump(),
    }
    (out / "manifest.json").write_text(_dump(manifest))
    return SynthResult(
        seed=seed,
        meetings=len(notes),
        utterances=utterances,
        words=words,
        questions=len(questions),
        validation=report,
        polish=stats,
        llm_spent_usd=llm.ledger.spent_usd() if llm else 0.0,
    )

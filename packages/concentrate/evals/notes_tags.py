"""Notes-sufficient vs transcript-needed: a computed, not hand-labelled, tag per question.

A question is **notes-sufficient** when its gold answer can be recovered from the AI notes
(``summary_markdown`` plus the owner's typed ``private_notes_markdown``, with the meeting's
title and date) of its gold meetings; otherwise it is **transcript-needed**. Unanswerable
questions get their own tag. This is the split Granola's transcript auto-deletion acts on:
after deletion "chat responses ... will be limited to whatever is in your notes".

Two deterministic taggers, both pure:

* :func:`rule_tag`, the **portable rule**, works on any corpus (synthetic or the real slice):
  every gold key value (synthetic ``answer_norm``; real owner-listed ``key_terms``) must be
  found in the gold meetings' notes by the benchmark's own string matcher
  (``scoring._norm_present``), extended with first names and status words. It is a string
  matcher, so it errs both ways: generic values ("62", "signed") can match an unrelated bullet,
  and a paraphrase can hide a real match.
* :func:`generator_tag`, the **generator reference**, synthetic only: the generator plants each
  fact as one summary bullet and drops ~20% of them (``synth.render.LOSSY_SUMMARY_DROP``). A
  question is notes-sufficient iff every planted line its answer needs survived into the
  gold meeting's notes. Exact by construction, so it is the primary synthetic tag and the
  yardstick the portable rule is measured against (dev used for tuning, test for reporting).

Definitions and caveats: ``docs/results/NOTES_VS_TRANSCRIPT.md``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from concentrate.core.models import MeetingNote
from concentrate.evals.scoring import _norm_present
from concentrate.synth.questions import UNANSWERABLE, Question
from concentrate.synth.roster import PEOPLE, person
from concentrate.synth.world import World

Tag = Literal["notes_sufficient", "transcript_needed", "unanswerable"]
TAGS: tuple[Tag, ...] = ("notes_sufficient", "transcript_needed", "unanswerable")
SHORT = {"notes_sufficient": "notes-sufficient", "transcript_needed": "transcript-needed",
         "unanswerable": "unanswerable"}  # fmt: skip

# Status values as a summary writes them (the gold uses one normalised word).
STATUS_FORMS: dict[str, tuple[str, ...]] = {
    "done": ("done", "delivered", "complete", "shipped", "finished"),
    "slipped": ("slipped", "now due"),
    "reassigned": ("reassigned", "→"),
}
# Statuses that are the *absence* of an update ("still open", "never followed up"). Notes can't
# state them; they are recoverable when the commitment itself is in the notes, so the rule asks
# for one of the question's evidence phrases instead.
ABSENCE_STATUSES = frozenset({"open", "dropped"})


def notes_text(
    title: str | None,
    when: datetime | None,
    summary_markdown: str | None,
    private_notes_markdown: str | None,
) -> str:
    """What a notes-only reader sees of one meeting: title, date, AI summary, typed notes."""
    header = [title or ""]
    if when is not None:
        header.append(f"{when:%A, %B} {when.day}, {when.year} ({when.date().isoformat()})")
    body = [summary_markdown or "", private_notes_markdown or ""]
    return "\n".join([*header, *body]).replace("**", "")


def note_notes_text(note: MeetingNote) -> str:
    """:func:`notes_text` of a normalized note (fixtures, API or MCP alike)."""
    when = note.scheduled_start or note.created_at
    return notes_text(note.title, when, note.summary_markdown, note.private_notes_markdown)


def item_present(item: str, text: str, anchors: Sequence[str] = ()) -> bool:
    """Is one gold key value recoverable from a notes text?"""
    low = text.lower()
    if item in ABSENCE_STATUSES:
        return any(a.lower() in low for a in anchors)
    if item in STATUS_FORMS:
        return any(f in low for f in STATUS_FORMS[item])
    if item in PEOPLE and person(item).first.lower() in low:
        return True
    return _norm_present(item.lower() if item not in PEOPLE else item, text)


def rule_tag(items: Sequence[str], texts: Iterable[str], anchors: Sequence[str] = ()) -> Tag:
    """The portable rule: every key value found in the gold meetings' notes."""
    if not items or list(items) == [UNANSWERABLE]:
        return "unanswerable"
    text = "\n".join(texts)
    ok = all(item_present(i, text, anchors) for i in items)
    return "notes_sufficient" if ok else "transcript_needed"


def _flat(text: str) -> str:
    """Case-, emphasis- and colon-insensitive form, so a bullet matches its typed-notes copy."""
    return " ".join(text.replace("*", "").replace(":", "").lower().split())


def _typed_form(bullet: str) -> str:
    """How the generator copies a bullet into the owner's typed notes (``render._typed_notes``)."""
    return " ".join(_flat(bullet.split(";")[0]).split()[:8])


def generator_tag(q: Question, world: World, notes: Mapping[str, str]) -> Tag:
    """Synthetic reference: did every planted line the answer needs survive into the notes?

    * a fact: its bullet (or the owner's typed copy of it) is in the notes of the meeting that
      first stated it;
    * a commitment: its task is named in some gold meeting's notes ("<who> to <task> by <day>",
      or a later "Done: <task> (<who>)"); for a status question, an update line
      ("Done:/Slipped:/Reassigned: <task>") is in one of the meetings that reported an update
      (when there was one);
    * a person in the answer is named in the gold meetings' notes (notes record what was
      decided, not who said it, so "who said X?" needs the transcript unless they do)."""
    if q.answer_norm == [UNANSWERABLE]:
        return "unanswerable"
    nid = {m.key: m.note_id for m in world.meetings}
    flat = {k: _flat(v) for k, v in notes.items()}
    gold = [flat[u.split(":", 1)[0]] for u in q.gold_utterance_ids]
    facts = {f.id: f for f in world.facts}
    coms = {c.id: c for c in world.commitments}
    for s in q.sources:
        if s in facts:
            f = facts[s]
            text = flat[nid[f.evidence[0].meeting]]
            if _flat(f.bullet) not in text and _typed_form(f.bullet) not in text:
                return "transcript_needed"
            continue
        c = coms[s]
        task = _flat(c.task)
        if not any(task in t for t in gold):
            return "transcript_needed"
        updates = sorted({e.meeting for e in c.resolved_evidence})
        if q.category == "commitment_status" and updates:
            heads = ("done", "slipped", "reassigned")
            if not any(f"{h} {task}" in flat[nid[m]] for h in heads for m in updates):
                return "transcript_needed"
    for item in q.answer_norm:
        if item in PEOPLE and not any(person(item).first.lower() in t for t in gold):
            return "transcript_needed"
    return "notes_sufficient"


@dataclass(frozen=True)
class TaggedQuestion:
    qid: str
    category: str
    split: str
    rule: Tag
    generator: Tag | None  # None on corpora without a generator (the real slice)

    @property
    def tag(self) -> Tag:
        """Primary tag: the generator reference where it exists, else the portable rule."""
        return self.generator or self.rule


def tag_synthetic(
    questions: Sequence[Question], notes: Sequence[MeetingNote], world: World
) -> list[TaggedQuestion]:
    """Tag every synthetic question with both taggers."""
    text = {n.note_id: note_notes_text(n) for n in notes}
    out = []
    for q in questions:
        gold = sorted({u.split(":", 1)[0] for u in q.gold_utterance_ids})
        out.append(
            TaggedQuestion(
                q.id, q.category, q.split,
                rule_tag(q.answer_norm, (text[m] for m in gold), q.evidence_keys),
                generator_tag(q, world, text),
            )
        )  # fmt: skip
    return out


def agreement(tagged: Iterable[TaggedQuestion]) -> dict[str, Any]:
    """Portable rule vs generator reference on answerable questions: confusion, agreement, κ."""
    pairs = [(t.rule, t.generator) for t in tagged if t.generator and t.generator in TAGS[:2]]
    cm = Counter(pairs)
    n = len(pairs)
    if not n:
        return {"n": 0}
    agree = sum(c for (r, g), c in cm.items() if r == g) / n
    pr = sum(r == "notes_sufficient" for r, _ in pairs) / n
    pg = sum(g == "notes_sufficient" for _, g in pairs) / n
    chance = pr * pg + (1 - pr) * (1 - pg)
    kappa = (agree - chance) / (1 - chance) if chance < 1 else float("nan")
    return {
        "n": n,
        "agreement": agree,
        "kappa": kappa,
        "confusion": {f"rule={r}|generator={g}": c for (r, g), c in sorted(cm.items())},
        "rule_sufficient_share": pr,
        "generator_sufficient_share": pg,
    }


def counts(tagged: Iterable[TaggedQuestion], *, by: str = "category") -> dict[str, dict[str, int]]:
    """``{category: {tag: count}}`` (primary tag), plus an ``all`` row: aggregate, safe to share."""
    out: dict[str, Counter[str]] = {}
    for t in tagged:
        for key in (getattr(t, by), "all"):
            out.setdefault(key, Counter())[t.tag] += 1
    return {k: {tag: v[tag] for tag in TAGS} for k, v in sorted(out.items())}

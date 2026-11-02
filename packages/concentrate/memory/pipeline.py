"""Stages 2-4 for one meeting: Extract -> Verify (grounding gate) -> Consolidate.

:class:`MemoryBuilder` is driven chronologically by :class:`ConcentrateSystem` (or a script).
Each meeting is consolidated in one SQLite transaction together with its ``meetings`` row, so a
crash never leaves a half-ingested meeting and resuming simply skips meetings already stored.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from concentrate.agents import corpus as cv
from concentrate.core.models import MeetingNote
from concentrate.llm.client import (
    LLMClient,
    LLMTruncated,
    ToolBackend,
    TurnCompletion,
    TurnRequest,
    estimate_tokens,
    load_prompt,
    run_turn,
)
from concentrate.memory.enhance_stub import STUB_ENHANCER, Enhancer
from concentrate.memory.entities import EntityDirectory
from concentrate.memory.extract import (
    EXTRACT_PROMPT,
    ExtractContext,
    extract_request,
    local_line,
    meeting_day,
    merge,
    recorded_at,
    to_full_ids,
    user_prompt,
    windows,
)
from concentrate.memory.schema import (
    Entailment,
    ExtractedCommitment,
    ExtractedFact,
    ExtractedUpdate,
    Extraction,
)
from concentrate.memory.store import Commitment, MemoryStore
from concentrate.memory.verify import (
    ENTAIL_PROMPT,
    LOW_CONFIDENCE,
    Item,
    check_item,
    entail,
)

ENTAIL_MAX_TOKENS = 8000
TRUNCATION_WINDOW = 60  # lines per window when a whole-meeting extraction hits max_tokens
_SNAKE = re.compile(r"[^a-z0-9:]+")


@dataclass(frozen=True)
class BuildConfig:
    effort: str = "medium"
    window_lines: int | None = None  # None = whole meeting per call
    entailment: bool = True
    entail_effort: str = "low"
    low_confidence: float = LOW_CONFIDENCE
    max_keys: int = 80
    max_open_commitments: int = 40


def snake(s: str) -> str:
    return _SNAKE.sub("_", s.strip().lower()).strip("_")


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 2}


class MemoryBuilder:
    """Builds the memory one meeting at a time (call :meth:`ingest` in chronological order)."""

    def __init__(
        self,
        store: MemoryStore,
        directory: EntityDirectory,
        extract_client: LLMClient,
        *,
        extract_backend: ToolBackend | None = None,
        entail_client: LLMClient | None = None,
        entail_backend: ToolBackend | None = None,
        enhancer: Enhancer = STUB_ENHANCER,
        enhancer_directory: Any = None,
        config: BuildConfig | None = None,
    ) -> None:
        self.store, self.directory = store, directory
        self.config = config or BuildConfig()
        self.extract_client, self.extract_backend = extract_client, extract_backend
        self.entail_client, self.entail_backend = entail_client, entail_backend
        self.enhancer = enhancer
        self.enhancer_directory = directory if enhancer_directory is None else enhancer_directory
        self.system = f"{load_prompt(EXTRACT_PROMPT)}\n{directory.prompt_block()}\n"
        self.entail_system = load_prompt(ENTAIL_PROMPT)

    # -- context from the memory so far --

    def _keys_block(self, subjects: list[str], day: str) -> str:
        """Current values the extractor should reuse keys for: entities this meeting mentions
        first, then the most recently changed keys, up to ``max_keys`` rows."""
        wanted = set(subjects)
        rows: list[tuple[bool, str, str]] = []  # (not mentioned, -recency, row)
        for subject, predicate in self.store.fact_keys():
            for f in self.store.as_of(subject, predicate, day):
                row = f'- {subject}.{predicate} = "{f.value}" (since {f.valid_from})'
                rows.append((subject not in wanted, f.recorded_at, row))
        rows.sort(key=lambda r: r[1], reverse=True)
        rows.sort(key=lambda r: r[0])
        keep = rows[: self.config.max_keys]
        return "\n".join(sorted(r[2] for r in keep))

    def _open_commitments(self, day: str, attendees: list[str]) -> list[Commitment]:
        live = []
        for c in self.store.commitments():
            st = c.status_at(day)
            if st["status"] in ("done", "dropped"):
                continue
            live.append(c)
        live.sort(key=lambda c: (c.owner not in attendees, c.made_on), reverse=False)
        return live[: self.config.max_open_commitments]

    @staticmethod
    def _commitments_block(cs: list[Commitment], day: str) -> str:
        rows = []
        for c in cs:
            st = c.status_at(day)
            due = f", due {st['due']}" if st["due"] else ""
            rows.append(f"- c{c.id}: {st['owner']} - {c.task}{due} (made {c.made_on})")
        return "\n".join(rows)

    # -- LLM calls --

    def _extract(
        self,
        m: MeetingNote,
        attendees: list[str],
        lines: list[str],
        ctx: ExtractContext,
        size: int | None,
    ) -> tuple[Extraction, list[TurnCompletion], int]:
        parts, completions = [], []
        spans = windows(len(lines), size)
        for start, end in spans:
            part = None if len(spans) == 1 else (start, end)
            user = user_prompt(m, attendees, lines[start:end], ctx, part)
            req = extract_request(self.system, user, effort=self.config.effort)
            c = run_turn(self.extract_client, req, self.extract_backend)
            completions.append(c)
            parts.append(to_full_ids(Extraction.model_validate_json(c.text), m.note_id))
        return merge(parts), completions, len(spans)

    def _entail_fn(self, completions: list[TurnCompletion]) -> Any:
        client = self.entail_client

        def fn(user: str, schema: dict[str, Any]) -> Entailment:
            assert client is not None
            req = TurnRequest(
                stage="ingest.concentrate.entail",
                prompt_id=ENTAIL_PROMPT,
                system=self.entail_system,
                messages=({"role": "user", "content": user},),
                schema=schema,
                max_tokens=ENTAIL_MAX_TOKENS,
                effort=self.config.entail_effort,
            )
            c = run_turn(client, req, self.entail_backend)
            completions.append(c)
            return Entailment.model_validate_json(c.text)

        return fn

    # -- one meeting --

    def ingest(self, m: MeetingNote, history: Sequence[MeetingNote] = ()) -> dict[str, Any]:
        """Extract, verify and consolidate ``m``; returns its stats (also stored)."""
        if self.store.has_meeting(m.note_id):
            return dict(
                next(r["stats"] for r in self.store.meetings() if r["note_id"] == m.note_id)
            )
        t0 = time.perf_counter()
        ledger = self.extract_client.ledger
        before = len(ledger.records)
        day, rec = meeting_day(m), recorded_at(m)
        enhanced = self.enhancer.enhance(m, self.enhancer_directory, tuple(history))
        full_lines = [self.enhancer.line(u, enhanced) for u in m.utterances]
        local = [
            local_line(x, u.utterance_id, u.index)
            for x, u in zip(full_lines, m.utterances, strict=True)
        ]
        text_of = {
            u.utterance_id: x.split("] ", 1)[1] for u, x in zip(m.utterances, local, strict=True)
        }
        attendee_ids = list(getattr(enhanced, "attendee_ids", []) or self.directory.attendee_ids(m))
        attendees = [f"{self.directory.name(a)} ({a})" for a in attendee_ids]
        everything = f"{m.title}\n{cv.summary_view(m)}\n" + "\n".join(text_of.values())
        subjects = list(dict.fromkeys(attendee_ids + self.directory.find(everything)))
        open_cs = self._open_commitments(day, attendee_ids)
        ctx = ExtractContext(self._keys_block(subjects, day), self._commitments_block(open_cs, day))

        try:
            x, completions, n_windows = self._extract(
                m, attendees, local, ctx, self.config.window_lines
            )
        except LLMTruncated:
            x, completions, n_windows = self._extract(m, attendees, local, ctx, TRUNCATION_WINDOW)

        # -- verify --
        items: list[Item] = [*x.facts, *x.commitments, *x.commitment_updates]
        results = [
            check_item(it, text_of, low_confidence=self.config.low_confidence) for it in items
        ]
        to_entail = [i for i, r in enumerate(results) if r.ok and r.needs_entailment]
        entailed = 0
        checked: set[int] = set()
        if self.config.entailment and self.entail_client is not None and to_entail:
            verdicts = entail(
                [items[i] for i in to_entail], text_of, day, self._entail_fn(completions)
            )
            for i, v in zip(to_entail, verdicts, strict=True):
                results[i] = v
            entailed, checked = len(to_entail), set(to_entail)

        # -- consolidate (one transaction with the meeting row) --
        actions: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        kept_text: list[str] = []
        with self.store.transaction():
            made: list[tuple[int, ExtractedCommitment]] = []
            for n, (it, r) in enumerate(zip(items, results, strict=True)):
                if not r.ok:
                    reasons[r.reason] += 1
                    self.store.reject(
                        m.note_id, type(it).__name__, r.reason, r.detail, it.model_dump()
                    )
                    continue
                if isinstance(it, ExtractedFact):
                    action = self._add_fact(it, m.note_id, day, rec, n in checked)
                    actions[action] += 1
                    kept_text.append(f"{it.subject}.{it.predicate} = {it.value} {it.source_lines}")
                elif isinstance(it, ExtractedCommitment):
                    if any(
                        it.owner == c.owner and set(it.source_lines) & set(c.source_lines)
                        for _, c in made
                    ):
                        actions["commitment_duplicate"] += 1
                        continue
                    cid = self.store.add_commitment(
                        owner=snake(it.owner),
                        task=it.task,
                        due=it.due,
                        note_id=m.note_id,
                        made_on=day,
                        recorded_at=rec,
                        source_lines=it.source_lines,
                        quote=it.quote,
                        confidence=it.confidence,
                    )
                    made.append((cid, it))
                    actions["commitment"] += 1
                    kept_text.append(
                        f"{it.owner} committed: {it.task} due {it.due} {it.source_lines}"
                    )
                elif isinstance(it, ExtractedUpdate):
                    target = self._match_commitment(it, open_cs, made)
                    if target is None:
                        reasons["update_unmatched"] += 1
                        self.store.reject(
                            m.note_id,
                            "ExtractedUpdate",
                            "update_unmatched",
                            "no commitment matches",
                            it.model_dump(),
                        )
                        continue
                    self.store.add_commitment_event(
                        target,
                        kind=it.kind,
                        day=day,
                        recorded_at=rec,
                        note_id=m.note_id,
                        source_lines=it.source_lines,
                        quote=it.quote,
                        new_due=it.new_due,
                        new_owner=snake(it.new_owner) if it.new_owner else None,
                    )
                    actions[f"update_{it.kind}"] += 1
                    kept_text.append(f"c{target} {it.kind} {it.source_lines}")
            calls = ledger.records[before:]
            stats: dict[str, Any] = {
                "windows": n_windows,
                "llm_calls": len(calls),
                "cached_calls": sum(r.cached for r in calls),
                "input_tokens": sum(r.input_tokens for r in calls),
                "output_tokens": sum(r.output_tokens for r in calls),
                "cache_read_tokens": sum(c.cache_read_tokens for c in completions),
                "cache_write_tokens": sum(c.cache_write_tokens for c in completions),
                "cost_usd": sum(r.cost_usd for r in calls),
                "seconds": time.perf_counter() - t0,
                "facts_extracted": len(x.facts),
                "commitments_extracted": len(x.commitments),
                "updates_extracted": len(x.commitment_updates),
                "entailment_checked": entailed,
                "kept": dict(actions),
                "rejected": dict(reasons),
            }
            tiers = {
                "L0": estimate_tokens(cv.transcript_lines(m.utterances)) if m.utterances else 0,
                "L1": estimate_tokens("\n".join(full_lines)) if full_lines else 0,
                "L2": estimate_tokens("\n".join(kept_text)) if kept_text else 0,
                "L3": estimate_tokens(x.digest) if x.digest else 0,
            }
            self.store.add_lines(
                m.note_id,
                [(u.utterance_id, x) for u, x in zip(m.utterances, full_lines, strict=True)],
            )
            self.store.add_meeting(
                m.note_id, m.title or "untitled", day, rec, x.digest, tiers, stats
            )
        return stats

    def _add_fact(self, f: ExtractedFact, note_id: str, day: str, rec: str, entailed: bool) -> str:
        subject = snake(f.subject)
        if self.directory.get(subject) is None:
            self.store.note_entity(subject, day)
        action, _ = self.store.add_fact(
            subject=subject,
            predicate=snake(f.predicate),
            kind=f.kind,
            value=f.value,
            value_norm=f.value_norm,
            valid_from=f.valid_from or day,
            recorded_at=rec,
            note_id=note_id,
            source_lines=f.source_lines,
            quote=f.quote,
            reason=f.reason,
            confidence=f.confidence,
            privacy=f.privacy,
            gate="entailed" if entailed else "deterministic",
        )
        return action

    @staticmethod
    def _match_commitment(
        u: ExtractedUpdate, open_cs: list[Commitment], made: list[tuple[int, ExtractedCommitment]]
    ) -> int | None:
        ids = {c.id for c in open_cs} | {cid for cid, _ in made}
        if u.commitment_id:
            mt = re.fullmatch(r"c?(\d+)", u.commitment_id.strip())
            if mt and int(mt.group(1)) in ids:
                return int(mt.group(1))
        want = _tokens(u.task)
        best, best_score = None, 0.0
        cands = [(c.id, c.owner, c.task) for c in open_cs] + [(i, c.owner, c.task) for i, c in made]
        for cid, owner, task in cands:
            have = _tokens(task)
            score = len(want & have) / max(1, len(want | have)) + (
                0.3 if snake(u.owner) == owner else 0
            )
            if score > best_score:
                best, best_score = cid, score
        return best if best_score >= 0.5 else None

"""Pre-meeting brief evaluation: memory brief vs an LLM over top-k retrieved chunks.

**Ground truth** comes from the generator's world (``world.json``): commitments with outcomes
and the meetings where they were made / resolved, facts with the meetings that state them. For
each target meeting (a meeting whose series met before) we compute the *ideal brief* as of its
start:

* commitments: made before the start, owner (after any reassignment) attending, not reported
  done before the start. Dropped commitments are never reported, so they stay open (a good
  brief flags them as overdue with no follow-up);
* changes: facts about the attendees, the customer, or subjects stated in earlier meetings of
  the series, first stated after the series' previous meeting and before this one;
* requests: the customer's request/complaint facts known before the start and still valid;
* last meeting: the previous meeting with the same title.

Gold transcript lines are found by locating each item's protected spoken span (``spoken`` /
``task_spoken``, which survive noise verbatim) in the fixture transcript, so they do not depend
on re-rendering with the generator version that produced the fixtures.

**Matching** a shown item to a gold item: the item cites a line within ``LINE_SLACK`` of a gold
line in the same meeting, or it comes from a gold meeting and its text overlaps the gold text
(content-word Jaccard >= ``MIN_JACCARD``); commitments with a resolvable owner must agree on it.
Matching is greedy one-to-one, best score first. Each shown item is a true positive (matches a
gold item), **stale** (matches an item that was already resolved: done commitment, closed
request), or a false positive. Precision = TP / shown, recall = TP / gold, staleness = stale /
shown.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from tenor.agents import corpus as cv
from tenor.agents.retrieval import Embedder, HybridIndex
from tenor.core.models import MeetingNote
from tenor.llm.client import LLMClient, LLMRequest, cost_usd, estimate_tokens, load_prompt
from tenor.memory.brief import Brief, UpcomingMeeting, build_brief
from tenor.memory.store import MemoryStore
from tenor.synth.world import Commitment, Fact, Meeting, World

BASELINE_PROMPT = "brief_baseline.v1"
DEV_FRACTION = 0.3
LINE_SLACK = 2
MIN_JACCARD = 0.2
DEFAULT_K_CHUNKS = 24
BASELINE_OUTPUT_TOKENS_EST = 2500  # JSON + adaptive thinking at low effort (planning only)
SECTIONS = ("commitments", "changes", "requests", "commitments_any_owner")
_STOPWORDS = (
    "the and for with that this from will have has are was were been about into onto their "
    "them they our your his her its per by on of to in at a an is be it as or"
)
_STOP = frozenset(_STOPWORDS.split())


# -- items --------------------------------------------------------------------------------


@dataclass
class Item:
    """One brief entry, from a system or from the gold brief."""

    text: str
    lines: list[str]
    notes: set[str]
    owner: str | None = None
    live: bool = True  # gold only: False = already resolved at the meeting start
    key: str = ""

    @staticmethod
    def of(text: str, lines: list[str], notes: set[str] | None = None, **kw: Any) -> Item:
        return Item(text, lines, {*(notes or set()), *(x.split(":")[0] for x in lines)}, **kw)


@dataclass
class GoldBrief:
    meeting: Meeting
    previous: Meeting | None
    commitments: list[Item]  # universe: live and resolved
    changes: list[Item]
    requests: list[Item]
    split: str


@dataclass
class ShownBrief:
    last_note_id: str | None
    commitments: list[Item] = field(default_factory=list)
    changes: list[Item] = field(default_factory=list)
    requests: list[Item] = field(default_factory=list)


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def jaccard(a: str, b: str) -> float:
    x, y = content_words(a), content_words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _line_hit(shown: list[str], gold: list[str], slack: int = LINE_SLACK) -> bool:
    gold_pos = {(g.split(":")[0], int(g.split(":")[1])) for g in gold if ":" in g}
    for s in shown:
        if ":" not in s:
            continue
        note, idx = s.split(":")[0], s.split(":")[1]
        if not idx.isdigit():
            continue
        if any(n == note and abs(int(idx) - i) <= slack for n, i in gold_pos):
            return True
    return False


def score_pair(s: Item, g: Item, strict_owner: bool = True) -> float:
    """Match strength (0 = no match). ``strict_owner`` requires resolvable owners to agree."""
    if strict_owner and s.owner and g.owner and s.owner != g.owner:
        return 0.0
    j = jaccard(s.text, g.text)
    if _line_hit(s.lines, g.lines):
        return 1.0 + j
    if s.notes & g.notes and j >= MIN_JACCARD:
        return j
    return 0.0


def match(shown: list[Item], gold: list[Item], strict_owner: bool = True) -> dict[int, int]:
    """Greedy one-to-one matching, best score first: shown index -> gold index."""
    pairs = sorted(
        (
            (score_pair(s, g, strict_owner), i, j)
            for i, s in enumerate(shown)
            for j, g in enumerate(gold)
        ),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    out: dict[int, int] = {}
    used: set[int] = set()
    for sc, i, j in pairs:
        if sc <= 0:
            break
        if i in out or j in used:
            continue
        out[i] = j
        used.add(j)
    return out


@dataclass
class SectionScore:
    shown: int = 0
    gold: int = 0
    tp: int = 0
    stale: int = 0
    cited_ok: int = 0  # TPs whose citations hit a gold line

    def add(self, o: SectionScore) -> None:
        for k in ("shown", "gold", "tp", "stale", "cited_ok"):
            setattr(self, k, getattr(self, k) + getattr(o, k))

    def metrics(self) -> dict[str, Any]:
        p = self.tp / self.shown if self.shown else None
        r = self.tp / self.gold if self.gold else None
        f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
        return {
            "precision": p,
            "recall": r,
            "f1": f,
            "staleness": self.stale / self.shown if self.shown else None,
            "citation_precision": self.cited_ok / self.tp if self.tp else None,
            **{k: getattr(self, k) for k in ("shown", "gold", "tp", "stale")},
        }


def score_section(
    shown: list[Item], universe: list[Item], strict_owner: bool = True
) -> SectionScore:
    m = match(shown, universe, strict_owner)
    s = SectionScore(shown=len(shown), gold=sum(g.live for g in universe))
    for i, j in m.items():
        g = universe[j]
        if g.live:
            s.tp += 1
            s.cited_ok += _line_hit(shown[i].lines, g.lines)
        else:
            s.stale += 1
    return s


# -- ground truth --------------------------------------------------------------------------


def find_lines(note: MeetingNote | None, spans: tuple[str, ...] | list[str]) -> list[str]:
    """Utterance IDs in ``note`` whose text contains any of ``spans`` (case-insensitive)."""
    if note is None:
        return []
    keys = [s.lower() for s in spans if s]
    return [u.utterance_id for u in note.utterances if any(k in u.text.lower() for k in keys)]


def _start(m: Meeting) -> datetime:
    return m.start if m.start.tzinfo else m.start.replace(tzinfo=UTC)


def is_request_fact(kind: str, predicate: str) -> bool:
    return kind == "request" or predicate.startswith(("request", "complaint"))


class GoldBuilder:
    """Computes ideal briefs from the world model and the rendered notes."""

    def __init__(self, world: World, notes: list[MeetingNote], seed: int = 42) -> None:
        self.w, self.seed = world, seed
        self.notes = {n.note_id: n for n in notes}
        self.by_key = {m.key: m for m in world.meetings}
        self.order = sorted(world.meetings, key=lambda m: (_start(m), m.note_id))

    def note(self, key: str) -> MeetingNote | None:
        return self.notes.get(self.by_key[key].note_id)

    def previous(self, m: Meeting) -> Meeting | None:
        prior = [x for x in self.order if x.title == m.title and _start(x) < _start(m)]
        return prior[-1] if prior else None

    def split(self, m: Meeting) -> str:
        h = int(hashlib.sha256(f"{self.seed}:brief:{m.note_id}".encode()).hexdigest(), 16)
        return "dev" if (h % 1000) < DEV_FRACTION * 1000 else "test"

    def targets(self) -> list[Meeting]:
        """Meetings whose series met before (so every section is defined)."""
        return [m for m in self.order if self.previous(m) is not None]

    def fact_known_at(self, f: Fact) -> datetime:
        return min(_start(self.by_key[e.meeting]) for e in f.evidence)

    def fact_lines(self, f: Fact) -> list[str]:
        out: list[str] = []
        for e in f.evidence:
            out += [x for x in find_lines(self.note(e.meeting), f.spoken) if x not in out]
        return out

    def _commitment(self, c: Commitment, t: datetime) -> Item | None:
        made = self.by_key[c.made_in]
        if _start(made) >= t:
            return None
        owner, live = c.owner, True
        resolved = self.by_key[c.resolved_in] if c.resolved_in else None
        done = c.outcome == "kept" or (c.outcome == "slipped" and c.resolved_on is not None)
        if done and resolved is not None and _start(resolved) < t:
            live = False
        if c.outcome == "reassigned" and resolved is not None and _start(resolved) < t:
            owner = c.new_owner or owner
        lines = find_lines(self.note(c.made_in), (c.task_spoken,))
        for e in c.resolved_evidence:
            if _start(self.by_key[e.meeting]) < t:
                lines += [
                    x for x in find_lines(self.note(e.meeting), (c.task_spoken,)) if x not in lines
                ]
        return Item.of(
            f"{c.task} {c.task_spoken}",
            lines,
            {made.note_id},
            owner=owner,
            live=live,
            key=c.id,
        )

    def relevant_subjects(self, m: Meeting) -> set[str]:
        series = {x.key for x in self.order if x.title == m.title and _start(x) < _start(m)}
        subjects = set(m.attendees) | ({m.customer} if m.customer else set())
        subjects |= {
            f.subject for f in self.w.facts if any(e.meeting in series for e in f.evidence)
        }
        return subjects

    def brief(self, m: Meeting) -> GoldBrief:
        t, prev = _start(m), self.previous(m)
        commitments = [
            item
            for c in self.w.commitments
            if (item := self._commitment(c, t)) is not None and item.owner in m.attendees
        ]
        since = _start(prev) if prev else t
        subjects = self.relevant_subjects(m)
        changes = [
            Item.of(f"{f.value} {f.bullet}", self.fact_lines(f), key=f.id)
            for f in self.w.facts
            if f.subject in subjects and since < self.fact_known_at(f) < t
        ]
        requests = [
            Item.of(
                f"{f.value} {f.bullet}",
                self.fact_lines(f),
                key=f.id,
                live=f.valid_to is None or f.valid_to > t.date(),
            )
            for f in self.w.facts
            if m.customer
            and f.subject == m.customer
            and is_request_fact(f.kind, f.predicate)
            and self.fact_known_at(f) < t
        ]
        return GoldBrief(m, prev, commitments, changes, requests, self.split(m))

    def upcoming(self, m: Meeting) -> UpcomingMeeting:
        """What a calendar would tell the brief builder (no world knowledge beyond that)."""
        return UpcomingMeeting(
            title=m.title,
            start=_start(m),
            attendees=tuple(m.attendees),
            customer=m.customer,
            note_id=m.note_id,
        )


# -- systems -------------------------------------------------------------------------------


def shown_from_brief(b: Brief) -> ShownBrief:
    return ShownBrief(
        last_note_id=b.last_meeting.note_id if b.last_meeting else None,
        commitments=[Item.of(c.task, c.lines, {c.made_in}, owner=c.owner) for c in b.commitments],
        changes=[Item.of(c.value, c.lines, {c.learnt_in}) for c in b.changes],
        requests=[Item.of(r.request, r.lines, {r.note_id}) for r in b.requests],
    )


def memory_brief(store: MemoryStore, gold: GoldBuilder, m: Meeting) -> Brief:
    return build_brief(store, gold.upcoming(m))


class _BCommitment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str
    task: str
    status: str
    due: str
    lines: list[str]


class _BChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: str
    previous: str
    lines: list[str]


class _BRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str
    lines: list[str]


class BaselineBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    last_meeting_note_id: str
    commitments: list[_BCommitment]
    changes: list[_BChange]
    requests: list[_BRequest]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_STR: dict[str, Any] = {"type": "string"}
_LINES: dict[str, Any] = {"type": "array", "items": _STR}
BASELINE_SCHEMA: dict[str, Any] = _obj(
    {
        "last_meeting_note_id": _STR,
        "commitments": {
            "type": "array",
            "items": _obj(
                {"owner": _STR, "task": _STR, "status": _STR, "due": _STR, "lines": _LINES}
            ),
        },
        "changes": {
            "type": "array",
            "items": _obj({"what": _STR, "previous": _STR, "lines": _LINES}),
        },
        "requests": {"type": "array", "items": _obj({"request": _STR, "lines": _LINES})},
    }
)


class TopKBriefBaseline:
    """An LLM writes the brief from the top-k transcript chunks (no precomputed memory).

    Retrieval is the baselines' hybrid BM25 + embeddings over 15-utterance chunks and AI
    summaries, restricted to meetings that started before the target meeting. The previous
    meeting in the series contributes its AI summary too, as a calendar-aware agent would fetch
    it. The query names the meeting, the attendees and the customer.
    """

    def __init__(
        self,
        client: LLMClient,
        notes: list[MeetingNote],
        names: dict[str, str],
        embedder: Embedder | None = None,
        k: int = DEFAULT_K_CHUNKS,
    ) -> None:
        self.client, self.names, self.k = client, names, k
        self.notes = cv.by_date(notes)
        self.start = {n.note_id: n.scheduled_start or n.created_at for n in self.notes}
        self.docs: list[tuple[str, int]] = []  # (note_id, chunk index or -1 for the summary)
        texts: list[str] = []
        self._chunks: dict[tuple[str, int], cv.Chunk] = {}
        for n in self.notes:
            texts.append(f"{n.title}\n{cv.summary_view(n)}")
            self.docs.append((n.note_id, -1))
            for c in cv.chunks(n):
                texts.append(f"{n.title}\n{c.text}")
                self.docs.append((n.note_id, c.chunk_id))
                self._chunks[(n.note_id, c.chunk_id)] = c
        self.index = HybridIndex(texts, embedder)
        self.instructions = load_prompt(BASELINE_PROMPT)

    def query(self, m: UpcomingMeeting) -> str:
        who = ", ".join(self.names.get(a, a) for a in m.attendees)
        cust = f" with {self.names.get(m.customer, m.customer)}" if m.customer else ""
        return (
            f"{m.title}{cust}. Attendees: {who}. Action items, commitments, follow-ups, "
            "deadlines, decisions, changes, customer requests."
        )

    def context(self, m: UpcomingMeeting) -> str:
        ranked = self.index.search(self.query(m), k=len(self.docs))
        picked: list[tuple[str, int]] = []
        for d in ranked:
            nid, cid = self.docs[d]
            if self.start[nid] < m.start and nid != m.note_id:
                picked.append((nid, cid))
            if len(picked) == self.k:
                break
        prior_series = [
            n for n in self.notes if n.title == m.title and self.start[n.note_id] < m.start
        ]
        if prior_series and (prior_series[-1].note_id, -1) not in picked:
            picked.append((prior_series[-1].note_id, -1))
        by_note: dict[str, list[int]] = {}
        for nid, cid in picked:
            by_note.setdefault(nid, []).append(cid)
        parts = []
        for n in self.notes:
            if n.note_id not in by_note:
                continue
            cids = sorted(by_note[n.note_id])
            block = [cv.header(n)]
            if -1 in cids:
                block += ["## Summary", cv.summary_view(n)]
            lines = [self._chunks[(n.note_id, c)].utterances for c in cids if c >= 0]
            if lines:
                block += ["## Transcript excerpts"]
                block += [cv.transcript_lines(u) for u in lines]
            parts.append("\n".join(block))
        return "\n\n".join(parts)

    def request(self, m: UpcomingMeeting) -> LLMRequest:
        who = ", ".join(f"{self.names.get(a, a)} ({a})" for a in m.attendees)
        cust = self.names.get(m.customer, m.customer) if m.customer else "none"
        user = (
            f"<meetings>\n{self.context(m)}\n</meetings>\n\n"
            f"Upcoming meeting: {m.title}\nStarts: {m.start:%a %Y-%m-%d %H:%M} UTC\n"
            f"Attendees: {who}\nCustomer: {cust}\n\nWrite the brief."
        )
        return LLMRequest(
            stage="brief.baseline",
            prompt_id=BASELINE_PROMPT,
            system=self.instructions,
            user=user,
            schema=BASELINE_SCHEMA,
            max_tokens=12000,
            effort="low",
        )

    def estimate_usd(self, m: UpcomingMeeting) -> float:
        r = self.request(m)
        if self.client.is_cached(r):
            return 0.0
        tokens = estimate_tokens(r.system) + estimate_tokens(r.user)
        return cost_usd(self.client.model, tokens, BASELINE_OUTPUT_TOKENS_EST)

    def run(self, m: UpcomingMeeting, ids: dict[str, str]) -> tuple[BaselineBrief, ShownBrief]:
        out = self.client.structured(self.request(m), BaselineBrief)
        return out, ShownBrief(
            last_note_id=out.last_meeting_note_id or None,
            commitments=[
                Item.of(c.task, c.lines, owner=resolve_person(c.owner, ids))
                for c in out.commitments
            ],
            changes=[Item.of(c.what, c.lines) for c in out.changes],
            requests=[Item.of(r.request, r.lines) for r in out.requests],
        )


def name_index(directory: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """(id -> display name, lowercase name or unique first name -> id) from directory.json."""
    names: dict[str, str] = {}
    ids: dict[str, str] = {}
    firsts: dict[str, list[str]] = {}
    for p in directory.get("people", []):
        names[p["id"]] = p["name"]
        ids[p["name"].lower()] = p["id"]
        firsts.setdefault(p["name"].split()[0].lower(), []).append(p["id"])
    for o in directory.get("orgs", []):
        names[o["id"]] = o["name"]
    for first, who in firsts.items():
        if len(who) == 1:
            ids.setdefault(first, who[0])
    return names, ids


def resolve_person(name: str, ids: dict[str, str]) -> str | None:
    key = re.sub(r"\s*\(.*\)$", "", name).strip().lower()
    return ids.get(key) or ids.get(key.split()[0] if key else "")


# -- scoring -------------------------------------------------------------------------------


def score_brief(shown: ShownBrief, gold: GoldBrief) -> dict[str, Any]:
    """Per-section scores for one meeting, plus last-meeting correctness."""
    return {
        "commitments": score_section(shown.commitments, gold.commitments),
        "changes": score_section(shown.changes, gold.changes),
        "requests": score_section(shown.requests, gold.requests),
        "commitments_any_owner": score_section(shown.commitments, gold.commitments, False),
        "last_meeting_ok": shown.last_note_id == (gold.previous.note_id if gold.previous else None),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Micro-averaged metrics over meetings."""
    out: dict[str, Any] = {"meetings": len(rows)}
    for sec in SECTIONS:
        total = SectionScore()
        for r in rows:
            total.add(r[sec])
        out[sec] = total.metrics()
    out["last_meeting_accuracy"] = (
        sum(r["last_meeting_ok"] for r in rows) / len(rows) if rows else None
    )
    return out


def to_json_row(note_id: str, split: str, scores: dict[str, Any]) -> dict[str, Any]:
    """One meeting's scores as plain JSON."""
    return {
        "note_id": note_id,
        "split": split,
        "last_meeting_ok": scores["last_meeting_ok"],
        **{s: scores[s].metrics() for s in SECTIONS},
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, default=str)


# -- runner --------------------------------------------------------------------------------


def sample_targets(gold: GoldBuilder, split: str, limit: int) -> list[Meeting]:
    """Targets in ``split`` ("dev" | "test" | "all"), an evenly spaced sample of ``limit``."""
    ts = [m for m in gold.targets() if split == "all" or gold.split(m) == split]
    if limit and len(ts) > limit:
        step = len(ts) / limit
        ts = [ts[int(i * step)] for i in range(limit)]
    return ts


def evaluate(
    store: MemoryStore,
    gold: GoldBuilder,
    targets: list[Meeting],
    baseline: TopKBriefBaseline | None = None,
    ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Score the memory brief (and the baseline, if given) on ``targets``."""
    rows: dict[str, list[dict[str, Any]]] = {"memory": [], "baseline": []}
    per: list[dict[str, Any]] = []
    for m in targets:
        gb = gold.brief(m)
        mem = score_brief(shown_from_brief(memory_brief(store, gold, m)), gb)
        rows["memory"].append(mem)
        row: dict[str, Any] = {"memory": to_json_row(m.note_id, gb.split, mem)}
        if baseline is not None:
            _, shown = baseline.run(gold.upcoming(m), ids or {})
            base = score_brief(shown, gb)
            rows["baseline"].append(base)
            row["baseline"] = to_json_row(m.note_id, gb.split, base)
        per.append({"note_id": m.note_id, "title": m.title, **row})
    return {
        "summary": {k: aggregate(v) for k, v in rows.items() if v},
        "meetings": per,
    }

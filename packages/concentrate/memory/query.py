"""Answer-time memory access: question parsing, structured lookup, hybrid retrieval, context.

Structured lookup answers "what is X's Y (as of D)?" by key, which is exact and cheap; hybrid
retrieval (BM25 + embeddings, RRF) over fact texts and meeting digests covers open-ended
questions and keys the parser didn't resolve. Both are filtered to what the memory knew on the
question's date (transaction time <= as-of), so a later meeting can't leak into an "as of"
answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from concentrate.agents.retrieval import Embedder, HybridIndex
from concentrate.memory.entities import EntityDirectory
from concentrate.memory.store import Commitment, Fact, MemoryStore

MAX_PREDICATES_PER_SUBJECT = 12
MAX_COMMITMENTS = 15
MAX_LIST_COMMITMENTS = 40
MAX_SOURCE_LINES = 150
_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "as_of",
        re.compile(
            r"\bas of\b|\bat the time\b|\bback then\b|\bon (mon|tues|wednes|thurs|fri)day\b"
        ),
    ),
    ("latest", re.compile(r"\b(current|currently|now|latest|still|today)\b")),
    (
        "history",
        re.compile(
            r"\b(change[sd]?|changing|before|previous|originally|history|why did|when did)\b"
        ),
    ),
    (
        "status",
        re.compile(
            r"\b(status|done|finish(ed)?|complete[d]?|deliver(ed)?|follow(ed)?[- ]up|open|"
            r"slipp(ed)?|took over|reassign(ed)?)\b"
        ),
    ),
    ("list", re.compile(r"\b(which|list|all|how many|what are|who are)\b")),
]


@dataclass(frozen=True)
class ParsedQuestion:
    entities: list[str]
    intents: list[str]
    as_of: date

    @property
    def primary(self) -> str:
        return self.intents[0] if self.intents else "open"


def parse_question(text: str, as_of: date, directory: EntityDirectory) -> ParsedQuestion:
    """Resolve the question's entities (directory names/aliases) and its intent flags."""
    low = text.lower()
    intents = [name for name, rx in _INTENTS if rx.search(low)]
    return ParsedQuestion(
        _by_role(low, directory.find(text), directory), intents or ["open"], as_of
    )


def _by_role(low: str, ids: list[str], directory: EntityDirectory) -> list[str]:
    """Narrow an ambiguous first name by a role the question states ("Sam the backend
    engineer" -> Sam Patel): keep the one namesake whose directory role appears in the text."""
    groups: dict[str, list[str]] = {}
    for i in ids:
        e = directory.get(i)
        if e is not None and e.kind == "person":
            groups.setdefault(e.name.split()[0].lower(), []).append(i)
    drop: set[str] = set()
    for group in groups.values():
        hits = [i for i in group if (r := directory.by_id[i].role) and r.lower() in low]
        if len(group) > 1 and len(hits) == 1:
            drop |= set(group) - set(hits)
    return [i for i in ids if i not in drop]


_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_TITLE_RX = re.compile(r'"([^"]+)"')
_DAY_RX = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})\b")
_REASSIGNED_RX = re.compile(r"\breassign|\bhanded (over|off)|\bto someone else\b")
_NO_FOLLOW_RX = re.compile(r"never (been )?follow|no follow|without (any )?follow")


def anchored_meetings(question: str, as_of: date, meetings: list[dict[str, Any]]) -> list[str]:
    """Note IDs of the meeting a question names by quoted title, narrowed by any calendar date
    it gives ("in the \"Eng standup\" meeting on Monday, August 31")."""
    titles = {t.lower() for t in _TITLE_RX.findall(question)}
    if not titles:
        return []
    days = set()
    for month, dd in _DAY_RX.findall(question.lower()):
        try:
            days.add(date(as_of.year, _MONTHS.index(month) + 1, int(dd)).isoformat())
        except ValueError:
            continue
    return [
        m["note_id"]
        for m in meetings
        if (m["title"] or "").lower() in titles and (not days or m["day"] in days)
    ]


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower().replace("_", " ")) if len(t) > 2}


@dataclass
class MemoryIndex:
    """Hybrid index over fact texts and meeting digests (rebuilt after ingestion)."""

    store: MemoryStore
    directory: EntityDirectory
    embedder: Embedder | None = None
    facts: list[Fact] = field(default_factory=list)
    digests: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.facts = self.store.all_facts()
        self.digests = [
            {"note_id": m["note_id"], "title": m["title"], "day": m["day"], "digest": m["digest"]}
            for m in self.store.meetings()
        ]
        docs = [self.fact_doc(f) for f in self.facts] + [
            f"{d['title']} {d['day']}: {d['digest']}" for d in self.digests
        ]
        self.index = HybridIndex(docs, self.embedder) if docs else None

    def fact_doc(self, f: Fact) -> str:
        reason = f" because {f.reason}" if f.reason else ""
        return (
            f"{self.directory.name(f.subject)} {f.predicate.replace('_', ' ')}: {f.value}{reason}"
        )

    def search(self, query: str, k: int) -> list[tuple[str, int]]:
        """``("fact"|"digest", position)`` hits, best first."""
        if self.index is None:
            return []
        n = len(self.facts)
        return [("fact", i) if i < n else ("digest", i - n) for i in self.index.search(query, k)]


def _fact_line(
    f: Fact, as_of: str, directory: EntityDirectory, *, supersession: bool = True
) -> str:
    if not supersession:  # ablation: an append-only fact log (stated date, no validity/state)
        reason = f" | reason: {f.reason}" if f.reason else ""
        return (
            f'- {directory.name(f.subject)} | {f.predicate} = "{f.value}" | stated '
            f"{f.recorded_at[:10]}{reason} | lines [{', '.join(f.all_lines())}]"
        )
    live = f.live_at(as_of, known_at=as_of)
    end = f.valid_to if (f.valid_to and (f.valid_to_recorded_at or "")[:10] <= as_of) else None
    state = "CURRENT" if live else ("superseded" if end else "not yet valid")
    if f.contradiction:
        state += ", CONTRADICTED by another value stated the same day"
    parts = [
        f'- {directory.name(f.subject)} | {f.predicate} = "{f.value}"',
        f"valid {f.valid_from} -> {end or 'now'} ({state})",
    ]
    if f.reason:
        parts.append(f"reason: {f.reason}")
    if f.privacy != "none":
        parts.append(f"privacy: {f.privacy}")
    if f.evidence:
        parts.append(f"restated {len(f.evidence)}x")
    parts.append("lines [" + ", ".join(f.all_lines()) + "]")
    return " | ".join(parts)


def _commitment_line(c: Commitment, as_of: str, directory: EntityDirectory) -> str:
    st = c.status_at(as_of)
    due = f", due {c.due}" if c.due else ""
    out = [
        f"- {directory.name(c.owner)}: {c.task}{due} (made {c.made_on})"
        f" | status as of {as_of}: {st['status']}"
    ]
    for ev in c.events:
        if ev["day"] <= as_of:
            extra = f" new due {ev['new_due']}" if ev["new_due"] else ""
            extra += f" new owner {directory.name(ev['new_owner'])}" if ev["new_owner"] else ""
            out.append(f"    {ev['day']}: {ev['kind']}{extra} [{', '.join(ev['source_lines'])}]")
    out[0] += " | lines [" + ", ".join(c.source_lines) + "]"
    return "\n".join(out)


@dataclass
class Context:
    text: str
    meetings: list[str]  # note IDs the context draws on
    lines_shown: int
    retired_meetings: int


def build_context(
    idx: MemoryIndex,
    question: str,
    pq: ParsedQuestion,
    *,
    k_facts: int = 20,
    k_digests: int = 4,
    retire_after_days: int | None = None,
    supersession: bool = True,
) -> Context:
    """Render the memory slice for one question (structured lookup + retrieval + lines).

    ``supersession=False`` (ablation) renders facts as an append-only log: each value with the
    date it was stated, but no validity interval and no CURRENT/superseded state.
    """
    store, directory = idx.store, idx.directory
    day = pq.as_of.isoformat()
    known = [f for f in idx.facts if f.recorded_at[:10] <= day]
    meetings = {m["note_id"]: m for m in store.meetings()}
    q_tokens = _tokens(question)
    anchors = set(anchored_meetings(question, pq.as_of, list(meetings.values())))
    shown: dict[int, Fact] = {}
    sections: list[str] = []

    # 1. structured lookup by resolved entity
    people = {
        e for e in pq.entities if (ent := directory.get(e)) is not None and ent.kind == "person"
    }
    look: list[str] = []
    for subject in pq.entities:
        mine = [f for f in known if f.subject == subject]
        related = [
            f
            for f in known
            if f.subject != subject and subject in people and f.value_norm == subject
        ]
        if not mine and not related:
            continue
        preds: dict[str, list[Fact]] = {}
        for f in mine:
            preds.setdefault(f.predicate, []).append(f)
        keep = list(preds)
        if len(keep) > MAX_PREDICATES_PER_SUBJECT:

            def score(p: str, preds: dict[str, list[Fact]] = preds) -> int:
                return len(q_tokens & _tokens(p + " " + " ".join(f.value for f in preds[p])))

            keep = sorted(keep, key=score, reverse=True)[:MAX_PREDICATES_PER_SUBJECT]
        look.append(f"### {directory.name(subject)} ({subject})")
        for p in sorted(keep):
            for f in preds[p]:
                look.append(_fact_line(f, day, directory, supersession=supersession))
                shown[f.id] = f
        for f in related:
            look.append(_fact_line(f, day, directory, supersession=supersession))
            shown[f.id] = f
    if look:
        sections.append(
            f"## Structured lookup (facts known as of {day}, by entity)\n" + "\n".join(look)
        )

    # 2. commitments: the named meeting's first, then the most on-topic; list questions about
    # reassignments / missing follow-ups get every matching commitment
    low = question.lower()
    cs = [c for c in store.commitments() if c.made_on <= day]
    cap = MAX_COMMITMENTS
    if _REASSIGNED_RX.search(low) and not people:
        picked = [
            c
            for c in cs
            if c.status_at(day)["status"] == "reassigned"
            or any(ev["kind"] == "reassigned" and ev["day"] <= day for ev in c.events)
        ]
        cap = MAX_LIST_COMMITMENTS
    elif _NO_FOLLOW_RX.search(low) and not people:
        picked = [c for c in cs if c.status_at(day)["status"] == "no_follow_up"]
        cap = MAX_LIST_COMMITMENTS
    else:

        def relevance(c: Commitment) -> tuple[bool, int]:
            return c.note_id in anchors, len(q_tokens & _tokens(c.task))

        picked = [
            c
            for c in cs
            if c.note_id in anchors
            or c.owner in people
            or set(c.owner.lstrip("?").split("|")) & people
        ]
        for c in cs:
            overlap = q_tokens & _tokens(c.task)
            if c not in picked and (
                len(overlap) >= 2
                or (overlap and len(overlap) / max(1, len(_tokens(c.task))) >= 0.5)
            ):
                picked.append(c)
        picked.sort(key=relevance, reverse=True)
        if "list" in pq.intents:
            cap = MAX_LIST_COMMITMENTS
    picked = picked[:cap]
    if picked:
        sections.append(
            "## Commitments (status as of the question date; no event = no recorded follow-up)\n"
            + "\n".join(_commitment_line(c, day, directory) for c in picked)
        )

    # 2b. facts stated or restated in the meeting the question names
    named = [
        f
        for f in known
        if f.id not in shown
        and (f.note_id in anchors or any(e["note_id"] in anchors for e in f.evidence))
    ]
    if named:
        sections.append(
            "## Facts stated in the meeting the question names\n"
            + "\n".join(_fact_line(f, day, directory, supersession=supersession) for f in named)
        )
        shown.update({f.id: f for f in named})

    # 3. hybrid retrieval over facts and digests
    hits = idx.search(question, k=k_facts + k_digests + len(shown) + 20)
    extra_facts: list[Fact] = []
    digests: list[dict[str, str]] = [d for d in idx.digests if d["note_id"] in anchors]
    for kind, i in hits:
        if kind == "fact":
            f = idx.facts[i]
            if f.id not in shown and f.recorded_at[:10] <= day and len(extra_facts) < k_facts:
                extra_facts.append(f)
        elif (
            len(digests) < k_digests
            and idx.digests[i]["day"] <= day
            and idx.digests[i] not in digests
        ):
            digests.append(idx.digests[i])
    if extra_facts:
        sections.append(
            "## Related facts (search)\n"
            + "\n".join(
                _fact_line(f, day, directory, supersession=supersession) for f in extra_facts
            )
        )
        shown.update({f.id: f for f in extra_facts})
    if digests:
        sections.append(
            "## Meeting digests (search)\n"
            + "\n".join(
                f"- {d['title']} [{d['note_id']}] {d['day']}: {d['digest']}" for d in digests
            )
        )

    # 4. source lines (L1), unless the meeting is retired
    cutoff = (
        (pq.as_of - timedelta(days=retire_after_days)).isoformat()
        if retire_after_days is not None
        else None
    )
    wanted: list[str] = []
    for f in shown.values():
        wanted += f.all_lines()
    for c in picked:
        st = c.status_at(day)
        wanted += st["lines"]
    wanted = list(dict.fromkeys(wanted))
    retired = {
        n
        for n in {w.split(":")[0] for w in wanted}
        if cutoff is not None and n in meetings and meetings[n]["day"] < cutoff
    }
    live_ids = [w for w in wanted if w.split(":")[0] not in retired][:MAX_SOURCE_LINES]
    texts = store.line_texts(live_ids)
    by_meeting: dict[str, list[str]] = {}
    for w in sorted(live_ids):
        if w in texts:
            by_meeting.setdefault(w.split(":")[0], []).append(texts[w])
    if by_meeting:
        blocks = []
        for nid in sorted(by_meeting, key=lambda n: (meetings.get(n, {}).get("day", ""), n)):
            meta = meetings.get(nid, {})
            blocks.append(
                f"### {meta.get('title', 'untitled')} [{nid}] {meta.get('day', '')}\n"
                + "\n".join(by_meeting[nid])
            )
        sections.append("## Source lines cited by these facts\n" + "\n\n".join(blocks))
    if retired:
        sections.append(
            f"(Transcripts of {len(retired)} meeting(s) older than {retire_after_days} days are "
            "retired: their facts keep line IDs as provenance, but the lines are not available.)"
        )

    used = (
        {f.note_id for f in shown.values()}
        | {c.note_id for c in picked}
        | anchors
        | {d["note_id"] for d in digests}
    )
    preamble = (
        "Memory built from the meetings: verified facts with validity dates (valid time) and the "
        "transcript line IDs that state them, commitments with their follow-ups, and meeting "
        "digests. Cite the line IDs in brackets."
    )
    body = "\n\n".join(sections) if sections else "(no matching memory)"
    ordered = sorted(used, key=lambda n: (meetings.get(n, {}).get("day", ""), n))
    return Context(f"<memory>\n{preamble}\n\n{body}\n</memory>", ordered, len(texts), len(retired))


def describe(pq: ParsedQuestion, directory: EntityDirectory) -> str:
    names = ", ".join(directory.name(e) for e in pq.entities) or "none"
    return f"entities: {names}; intents: {', '.join(pq.intents)}"

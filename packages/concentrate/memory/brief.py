"""Pre-meeting brief: what you should know before walking into a meeting, read from memory.

Given an upcoming meeting (start time, attendees, title, optional customer), the brief lists:

* **commitments**: every attendee's commitments still open when the meeting starts, with
  status (``open`` | ``slipped`` | ``reassigned`` | ``no_follow_up``), current due date and
  whether it is overdue;
* **changes**: fact versions the memory learnt *since this group last met* (new values and
  supersessions) about the attendees, the customer, or anything the group discussed before;
* **requests**: the customer's requests that are still live;
* **last meeting**: the previous meeting in the same series, with its digest.

Every item carries the transcript line IDs it rests on. Everything is an **as-of query**: the
store is bitemporal, and the brief only uses rows whose *transaction time* (``recorded_at``) is
before the meeting starts, so building a brief for a past meeting never leaks what was said
later. No LLM is involved; :func:`to_markdown` renders the result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from concentrate.memory.store import Commitment, Fact, MemoryStore

CommitmentStatus = Literal["open", "slipped", "reassigned", "no_follow_up"]
CLOSED_EVENTS = ("done", "dropped")


class UpcomingMeeting(BaseModel):
    """What the calendar knows about the meeting the brief is for."""

    model_config = ConfigDict(frozen=True)

    title: str
    start: datetime
    attendees: tuple[str, ...]  # entity ids, e.g. ("priya", "sam_p")
    customer: str | None = None  # org entity id for customer meetings
    note_id: str | None = None  # the meeting's own note, if it exists (excluded from memory)


class BriefCommitment(BaseModel):
    owner: str
    task: str
    status: CommitmentStatus
    due: str | None
    original_due: str | None
    overdue: bool
    made_in: str
    made_on: str
    lines: list[str]


class BriefChange(BaseModel):
    subject: str
    predicate: str
    kind: str
    value: str
    previous: str | None  # the value it replaced, if any (None = new fact)
    learnt_in: str
    learnt_on: str
    lines: list[str]


class BriefRequest(BaseModel):
    customer: str
    request: str
    since: str
    note_id: str
    lines: list[str]


class LastMeeting(BaseModel):
    note_id: str
    title: str
    day: str
    digest: str


class Brief(BaseModel):
    meeting: UpcomingMeeting
    as_of: str
    last_meeting: LastMeeting | None
    commitments: list[BriefCommitment]
    changes: list[BriefChange]
    requests: list[BriefRequest]


def _ts(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def series_meetings(store: MemoryStore, m: UpcomingMeeting) -> list[dict[str, Any]]:
    """Earlier meetings in the same series (same title), oldest first, known before ``m``."""
    return [
        r
        for r in store.meetings()
        if r["title"] == m.title and _ts(r["recorded_at"]) < m.start and r["note_id"] != m.note_id
    ]


def commitment_state(c: Commitment, at: datetime) -> dict[str, Any] | None:
    """Lifecycle of ``c`` as known at ``at`` (events recorded strictly before it).

    Returns ``None`` when ``c`` is closed (done / dropped) by then.
    """
    owner, due, status = c.owner, c.due, "open"
    lines = list(c.source_lines)
    slipped = reassigned = False
    seen = False
    for ev in c.events:
        if _ts(ev["recorded_at"]) >= at:
            continue
        seen = True
        if ev["kind"] in CLOSED_EVENTS:
            return None
        slipped |= ev["kind"] == "slipped"
        reassigned |= ev["kind"] == "reassigned"
        owner = ev["new_owner"] or owner
        due = ev["new_due"] or due
        lines += [x for x in ev["source_lines"] if x not in lines]
    if slipped:
        status = "slipped"
    elif reassigned:
        status = "reassigned"
    elif not seen and due is not None and due < at.date().isoformat():
        status = "no_follow_up"
    return {"owner": owner, "due": due, "status": status, "lines": lines}


def _known(c: Commitment, store_recorded: dict[int, str], at: datetime) -> bool:
    return _ts(store_recorded[c.id]) < at


def _commitments(store: MemoryStore, m: UpcomingMeeting) -> list[BriefCommitment]:
    recorded = {
        int(r[0]): str(r[1]) for r in store.db.execute("SELECT id, recorded_at FROM commitments")
    }
    today = m.start.date().isoformat()
    out = []
    for c in store.commitments():
        if not _known(c, recorded, m.start) or c.note_id == m.note_id:
            continue
        st = commitment_state(c, m.start)
        if st is None or st["owner"] not in m.attendees:
            continue
        out.append(
            BriefCommitment(
                owner=st["owner"],
                task=c.task,
                status=st["status"],
                due=st["due"],
                original_due=c.due,
                overdue=st["due"] is not None and st["due"] < today,
                made_in=c.note_id,
                made_on=c.made_on,
                lines=st["lines"],
            )
        )
    return sorted(out, key=lambda b: (b.owner, b.due or "9999", b.made_on, b.task))


def relevant_subjects(store: MemoryStore, m: UpcomingMeeting, series: list[str]) -> set[str]:
    """Attendees, the customer, and every subject the series has discussed before."""
    subjects = set(m.attendees)
    if m.customer:
        subjects.add(m.customer)
    if series:
        marks = ",".join("?" * len(series))
        q = f"SELECT DISTINCT subject FROM facts WHERE note_id IN ({marks})"
        subjects |= {r[0] for r in store.db.execute(q, series)}
    return subjects


def commitment_lines(store: MemoryStore) -> set[str]:
    """Every line a commitment or commitment event cites."""
    out: set[str] = set()
    for c in store.commitments():
        out.update(c.source_lines)
        for ev in c.events:
            out.update(ev["source_lines"])
    return out


def _changes(
    store: MemoryStore, m: UpcomingMeeting, since: datetime, series: list[str]
) -> list[BriefChange]:
    """Fact versions learnt in ``(since, m.start)`` about relevant subjects.

    Facts that restate a commitment update ("the export is done") are left out: they rest on
    the same lines as a commitment event and already show in the commitments section.
    """
    subjects = relevant_subjects(store, m, series)
    in_commitments = commitment_lines(store)
    by_id: dict[int, Fact] = {f.id: f for f in store.all_facts()}
    replaced = {p.superseded_by: p for p in by_id.values() if p.superseded_by is not None}
    out = []
    for f in by_id.values():
        learnt = _ts(f.recorded_at)
        if f.subject not in subjects or not since < learnt < m.start or f.note_id == m.note_id:
            continue
        if in_commitments.intersection(f.source_lines):
            continue
        prev = replaced.get(f.id)
        out.append(
            BriefChange(
                subject=f.subject,
                predicate=f.predicate,
                kind=f.kind,
                value=f.value,
                previous=prev.value if prev is not None else None,
                learnt_in=f.note_id,
                learnt_on=f.recorded_at[:10],
                lines=f.source_lines,
            )
        )
    return sorted(out, key=lambda c: (c.learnt_on, c.subject, c.predicate))


def is_request(f: Fact) -> bool:
    return f.kind == "request" or f.predicate.startswith(("request", "complaint"))


def _requests(store: MemoryStore, m: UpcomingMeeting) -> list[BriefRequest]:
    if not m.customer:
        return []
    known_at = m.start.isoformat()
    day = m.start.date().isoformat()
    return [
        BriefRequest(
            customer=f.subject,
            request=f.value,
            since=f.valid_from,
            note_id=f.note_id,
            lines=f.all_lines(),
        )
        for f in store.facts_for(m.customer)
        if is_request(f) and _ts(f.recorded_at) < m.start and f.live_at(day, known_at)
    ]


def build_brief(store: MemoryStore, m: UpcomingMeeting) -> Brief:
    """The brief for ``m``, using only what the memory knew before ``m.start``."""
    series = series_meetings(store, m)
    last = series[-1] if series else None
    since = _ts(last["recorded_at"]) if last else m.start  # no previous meeting: no "changes"
    return Brief(
        meeting=m,
        as_of=m.start.isoformat(),
        last_meeting=(
            LastMeeting(
                note_id=last["note_id"], title=last["title"], day=last["day"], digest=last["digest"]
            )
            if last
            else None
        ),
        commitments=_commitments(store, m),
        changes=_changes(store, m, since, [r["note_id"] for r in series]),
        requests=_requests(store, m),
    )


def _cite(lines: list[str], n: int = 3) -> str:
    return "".join(f"[{x}]" for x in lines[:n])


def to_markdown(b: Brief, names: dict[str, str] | None = None) -> str:
    """Render a brief as Markdown with line-ID citations."""
    names = names or {}

    def who(pid: str) -> str:
        return names.get(pid, pid)

    m = b.meeting
    out = [
        f"# Brief: {m.title} ({m.start:%a %Y-%m-%d %H:%M} UTC)",
        "Attendees: " + ", ".join(who(a) for a in m.attendees),
        "",
        "## Last time",
    ]
    if b.last_meeting:
        lm = b.last_meeting
        out += [f"{lm.title} on {lm.day} [{lm.note_id}]", "", lm.digest]
    else:
        out.append("First meeting of this series in memory.")
    out += ["", "## Open commitments"]
    for c in b.commitments:
        flag = " **overdue**" if c.overdue else ""
        due = f"due {c.due}" if c.due else "no due date"
        if c.original_due and c.due != c.original_due:
            due += f" (was {c.original_due})"
        out.append(f"- {who(c.owner)}: {c.task} ({c.status}, {due}){flag} {_cite(c.lines)}")
    if not b.commitments:
        out.append("- none")
    out += ["", "## Changed since you last met"]
    for ch in b.changes:
        was = f" (was: {ch.previous})" if ch.previous else " (new)"
        out.append(f"- {ch.learnt_on} {ch.value}{was} {_cite(ch.lines)}")
    if not b.changes:
        out.append("- nothing recorded")
    if m.customer:
        out += ["", f"## Open requests from {who(m.customer)}"]
        out += [f"- {r.request} (since {r.since}) {_cite(r.lines)}" for r in b.requests] or [
            "- none"
        ]
    return "\n".join(out) + "\n"

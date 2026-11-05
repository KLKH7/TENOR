"""AMI question set for the evidence-compression experiment.

Plan: docs/benchmark/EVIDENCE_COMPRESSION.md.

Every question is built by a fixed template from AMI's **human** annotations; no LLM drafts
anything. The gold answer is the annotator's own sentence(s); the gold evidence is the dialogue
acts the annotator linked to them (``extractive/<ID>.summlink.xml``), or, for who-said-it, the
dialogue act being quoted.

Templates (one ``template`` id each, so a later change is visible in the data):

* ``decision``: decision sentences grouped by their grammatical subject. An agent subject (the
  group, a role) gives "what did <agent> decide?"; any other subject is the topic ("what was
  decided about the remote?"). Empty subjects ("There will not be...", "People will...") join
  the group's bucket.
* ``action_who``: one per action sentence whose subject names a scenario role: "who was given the
  action item to <predicate>?". Pronoun subjects ("He will also...") take the previous action's
  subject.
* ``action_what``: action sentences grouped by subject: "what action items were assigned to X?".
  ``action_who`` and ``action_what`` on the same sentence share a ``fact_group``.
* ``problem``: one per meeting, "what problems or open issues came up?".
* ``who_said``: a seeded sample of summary-linked dialogue acts (10-40 words), quoted verbatim:
  "who said: '...'? Answer with their role." The role is looked up by AMI's participant code,
  which is stable across IS1008a-d; the NXT letter is not (the same person is B, then C).
* ``what_changed.follow_up``: a role's action item in IS1008k and what that role presented in
  IS1008k+1 (abstract sentences whose subject is that role and whose verb is presented/gave).
* ``what_changed.decision``: a decision in IS1008k and the decision in IS1008k+1 sharing the
  most content words (at least two); the shared words are the question's cue.

Lists longer than :data:`MAX_LIST` sentences are dropped: judge.v1 marks a list "correct" only
if every item is present, so a 10-item list is unanswerable in practice (IB4003's problems).

EN2002b has no abstractive summary and no dialogue acts in AMI 1.6.2, so it yields no questions;
it still counts for compression sizes. IB4003 has no summary links, so its gold evidence is empty
(``evidence_source = "none"``), and no scenario roles, so it has no who-said-it questions.

AMI is CC BY 4.0 (University of Edinburgh / AMI consortium); the JSONL written here carries that
attribution in ``data/ami_questions/README.md``.
"""

from __future__ import annotations

import itertools
import json
import random
import re
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from concentrate.ami.corpus import ROLE_NAMES, AmiMeeting, SummarySentence

QUESTION_MEETINGS = ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003", "EN2002b")
SERIES = ("IS1008a", "IS1008b", "IS1008c", "IS1008d")
DEV_MEETINGS = ("IS1008a",)  # questions touching only these are DEV; everything else TEST
OUT_DIR = Path("data/ami_questions")
MAX_LIST = 5
WHO_SAID_PER_MEETING = 3
WHO_SAID_WORDS = (10, 40)
AS_OF = date(2005, 1, 1)  # corpus.meeting_note's fixed synthetic date
TEMPLATE_VERSION = "ami_questions.v1"

QType = Literal["decision", "action_who", "action_what", "problem", "who_said", "what_changed"]
EvidenceSource = Literal["summlink", "quoted_act", "none"]

_AGENT_VERB = re.compile(r"\s+\b(will|decided|discussed|agreed)\b\s*")
_PRONOUN = {"he", "she", "they"}
_EMPTY_SUBJECTS = {"there", "people", "it", "this"}
_GROUP_WORDS = {"the group", "the team", "the group members", "the designers"}
_ROLE_RE = re.compile("|".join(ROLE_NAMES.values()) + "|project manager", re.IGNORECASE)
_PRESENTER = re.compile(r"^(?P<subj>.+?)\s+(?:also\s+)?(?:presented|gave)\b")
_STOP = frozenset((
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "will", "be", "is",
    "was", "were", "that", "this", "it", "its", "their", "they", "group", "remote", "product",
    "design", "designer", "project", "manager", "marketing", "expert", "user", "interface",
    "industrial", "decided", "include", "included", "including", "which", "whether", "would",
    "should", "until", "discuss", "discussed", "meeting", "next", "have", "made"
))  # fmt: skip


class AmiQuestion(BaseModel):
    """One AMI question. Field names match what the shared judge and stats code read."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: QType
    template: str
    meetings: list[str]  # the meetings the reader is given (oracle meeting selection)
    question: str
    gold_answer: str
    key_terms: list[str] = Field(default_factory=list)  # exact-match signal only
    gold_sentence_ids: list[str] = Field(default_factory=list)  # abstractive sentences used
    gold_evidence: list[str] = Field(default_factory=list)  # dialogue-act ids
    evidence_source: EvidenceSource
    fact_group: str  # questions sharing a fact (action_who / action_what of one sentence)
    notes_can_answer: bool | None = None  # filled later, once real product notes are synced
    split: Literal["dev", "test"]
    seed: int
    as_of: date = AS_OF
    status: Literal["verified"] = "verified"  # from human annotations: nothing to review
    unanswerable: bool = False


# -- parsing annotator sentences -------------------------------------------------------------------


def _clean(text: str) -> str:
    return text.strip().rstrip(".").strip()


def split_subject(text: str) -> tuple[str, str] | None:
    """(subject, predicate) at the first will/decided/discussed/agreed, else None."""
    hit = _AGENT_VERB.search(text)
    if hit is None or hit.start() == 0:
        return None
    rest = _clean(text[hit.end() :])
    rest = re.sub(r"^also\s+", "", rest)
    return text[: hit.start()].strip(), rest


def _norm_subject(subject: str) -> str:
    s = subject.lower()
    if s in _GROUP_WORDS:
        return "the group"
    return s


def is_agent(subject: str) -> bool:
    """The group or a role (so 'what did X decide?' reads naturally)."""
    return subject == "the group" or bool(_ROLE_RE.fullmatch(subject.removeprefix("the ")))


def roles_in(subject: str) -> list[str]:
    """Scenario role names in a subject, in order ('the X and the Y' -> [X, Y])."""
    return [m.group(0).title() for m in _ROLE_RE.finditer(subject)]


def _subjects(
    sentences: Sequence[SummarySentence], pattern: re.Pattern[str] | None = None
) -> list[tuple[SummarySentence, str, str]]:
    """(sentence, normalised subject, predicate); pronouns resolve to the previous subject.
    ``pattern`` (with a ``subj`` group) replaces the will/decided split, e.g. for presenters."""
    out: list[tuple[SummarySentence, str, str]] = []
    prev = ""
    for s in sentences:
        if pattern is not None:
            hit = pattern.match(s.text)
            parts = (hit["subj"], _clean(s.text[hit.end() :])) if hit else None
        else:
            parts = split_subject(s.text)
        if parts is None:
            continue
        subj, pred = parts
        norm = _norm_subject(subj)
        if norm in _PRONOUN and prev:
            norm = prev
        elif norm in _EMPTY_SUBJECTS:
            norm = "the group"
        out.append((s, norm, pred))
        prev = norm
    return out


def _display(subject: str, original: str = "") -> str:
    """Subject as written in a question: the annotator's casing with a lower-case leading
    article, role names capitalised ('the Marketing Expert', 'Gisella')."""
    text = original if original and original.lower() == subject else subject
    text = re.sub(r"^The\b", "the", text)
    return _ROLE_RE.sub(lambda m: m.group(0).title(), text)


def _group_by_subject(
    items: list[tuple[SummarySentence, str, str]],
) -> dict[str, list[SummarySentence]]:
    out: dict[str, list[SummarySentence]] = {}
    for s, subj, _ in items:
        out.setdefault(subj, []).append(s)
    return out


def _gold(sentences: Sequence[SummarySentence]) -> str:
    return " ".join(s.text if s.text.endswith(".") else f"{s.text}." for s in sentences)


def _evidence(sentences: Iterable[SummarySentence]) -> list[str]:
    return list(dict.fromkeys(da for s in sentences for da in s.links))


def _split(meetings: Sequence[str]) -> Literal["dev", "test"]:
    return "dev" if all(m in DEV_MEETINGS for m in meetings) else "test"


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4 and w not in _STOP}


# -- templates -------------------------------------------------------------------------------------


class _Builder:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.out: list[AmiQuestion] = []
        self._n: dict[str, int] = {}

    def add(
        self,
        qtype: QType,
        template: str,
        meetings: list[str],
        question: str,
        sentences: Sequence[SummarySentence],
        *,
        gold: str | None = None,
        evidence: list[str] | None = None,
        key_terms: list[str] | None = None,
        fact_group: str | None = None,
        source: EvidenceSource | None = None,
    ) -> None:
        stem = f"{'-'.join(meetings)}-{qtype}"
        self._n[stem] = self._n.get(stem, 0) + 1
        qid = f"ami-{stem}-{self._n[stem]:02d}"
        ev = evidence if evidence is not None else _evidence(sentences)
        self.out.append(
            AmiQuestion(
                id=qid,
                type=qtype,
                template=f"{TEMPLATE_VERSION}:{template}",
                meetings=meetings,
                question=question,
                gold_answer=gold if gold is not None else _gold(sentences),
                key_terms=key_terms or [],
                gold_sentence_ids=[s.sentence_id for s in sentences],
                gold_evidence=ev,
                evidence_source=source or ("summlink" if ev else "none"),
                fact_group=fact_group or qid,
                split=_split(meetings),
                seed=self.seed,
            )
        )


def _section(m: AmiMeeting, section: str) -> list[SummarySentence]:
    return [s for s in m.sentences if s.section == section]


def _decisions(b: _Builder, m: AmiMeeting) -> None:
    for subj, sents in _group_by_subject(_subjects(_section(m, "decisions"))).items():
        if len(sents) > MAX_LIST:
            continue
        parts = split_subject(sents[0].text)
        shown = _display(subj, parts[0] if parts else "")
        q = (
            f"In meeting {m.meeting_id}, what did {shown} decide?"
            if is_agent(subj)
            else f"In meeting {m.meeting_id}, what was decided about {shown}?"
        )
        b.add("decision", "decision.by_subject", [m.meeting_id], q, sents)


def _actions(b: _Builder, m: AmiMeeting) -> None:
    items = _subjects(_section(m, "actions"))
    groups = _group_by_subject(items)
    fact = {s.sentence_id: f"{m.meeting_id}:{s.sentence_id}" for s, _, _ in items}
    for s, subj, pred in items:
        roles = roles_in(subj)
        if not roles:
            continue
        b.add(
            "action_who",
            "action.who",
            [m.meeting_id],
            f"In meeting {m.meeting_id}, who was given the action item to {pred}?",
            [s],
            gold=" and ".join(f"The {r}" for r in roles) + ".",
            key_terms=roles,
            fact_group=fact[s.sentence_id],
        )
    for subj, sents in groups.items():
        if len(sents) > MAX_LIST:
            continue
        b.add(
            "action_what",
            "action.what",
            [m.meeting_id],
            f"In meeting {m.meeting_id}, what action items were assigned to {_display(subj)}?",
            sents,
            fact_group=fact[sents[0].sentence_id] if len(sents) == 1 else None,
        )


def _problems(b: _Builder, m: AmiMeeting) -> None:
    sents = _section(m, "problems")
    if sents and len(sents) <= MAX_LIST:
        b.add(
            "problem",
            "problem.meeting",
            [m.meeting_id],
            f"In meeting {m.meeting_id}, what problems or open issues came up?",
            sents,
        )


def _who_said(b: _Builder, m: AmiMeeting) -> None:
    role = {s.agent: ROLE_NAMES.get(s.role or "", "") for s in m.speakers}
    if not all(role.values()):
        return  # no scenario roles (natural meeting)
    linked = {da for s in m.sentences for da in s.links}
    lo, hi = WHO_SAID_WORDS
    texts = [a.text for a in m.dialogue_acts]
    pool = [
        a
        for a in m.dialogue_acts
        if a.da_id in linked and lo <= len(a.text.split()) <= hi and texts.count(a.text) == 1
    ]
    rng = random.Random(f"{b.seed}:{m.meeting_id}:who_said")
    rng.shuffle(pool)
    picked: list[str] = []
    chosen = []
    for a in pool:  # distinct speakers first, then anyone
        if role[a.agent] not in picked:
            picked.append(role[a.agent])
            chosen.append(a)
        if len(chosen) == WHO_SAID_PER_MEETING:
            break
    chosen.sort(key=lambda a: a.start)
    participant = {s.agent: s.participant for s in m.speakers}
    for a in chosen:
        b.add(
            "who_said",
            "who_said.linked_act",
            [m.meeting_id],
            f'In meeting {m.meeting_id}, who said: "{a.text}"? Answer with their role.',
            [],
            gold=f"The {role[a.agent]} (participant {participant[a.agent]}).",
            evidence=[a.da_id],
            key_terms=[role[a.agent]],
            source="quoted_act",
        )


def _follow_ups(b: _Builder, before: AmiMeeting, after: AmiMeeting) -> None:
    presented = _subjects(_section(after, "abstract"), _PRESENTER)
    for subj, sents in _group_by_subject(_subjects(_section(before, "actions"))).items():
        roles = roles_in(subj)
        if not roles:
            continue
        later = [s for s, asubj, _ in presented if set(roles_in(asubj)) & set(roles)]
        if not later:
            continue
        who = " and ".join(f"the {r}" for r in roles)
        was = "were" if len(roles) > 1 else "was"
        q = (
            f"What action item {was} {who} given in meeting {before.meeting_id}, and what did "
            f"{who} present at the next meeting, {after.meeting_id}?"
        )
        gold = f"{before.meeting_id}: {_gold(sents)} {after.meeting_id}: {_gold(later)}"
        b.add(
            "what_changed",
            "what_changed.follow_up",
            [before.meeting_id, after.meeting_id],
            q,
            [*sents, *later],
            gold=gold,
            key_terms=roles,
        )


def _decision_changes(b: _Builder, before: AmiMeeting, after: AmiMeeting) -> None:
    best: tuple[int, str, SummarySentence, SummarySentence] | None = None
    for x in _section(before, "decisions"):
        for y in _section(after, "decisions"):
            shared = _content(x.text) & _content(y.text)
            key = (len(shared), ",".join(sorted(shared)))
            if len(shared) >= 2 and (best is None or key > best[:2]):
                best = (*key, x, y)
    if best is None:
        return
    _, words, x, y = best
    # The three longest shared words, alphabetical within a length: the question's cue.
    cue = ", ".join(sorted(words.split(","), key=lambda w: (-len(w), w))[:3])
    b.add(
        "what_changed",
        "what_changed.decision",
        [before.meeting_id, after.meeting_id],
        f"How did the group's decision about {cue} change from meeting {before.meeting_id} "
        f"to meeting {after.meeting_id}?",
        [x, y],
        gold=f"{before.meeting_id}: {_gold([x])} {after.meeting_id}: {_gold([y])}",
    )


def build_questions(meetings: Sequence[AmiMeeting], seed: int) -> list[AmiQuestion]:
    """Every question the templates produce for ``meetings`` (deterministic in ``seed``)."""
    b = _Builder(seed)
    by_id = {m.meeting_id: m for m in meetings}
    for m in meetings:
        if not m.sentences:
            continue  # no abstractive summary (EN2002b)
        _decisions(b, m)
        _actions(b, m)
        _problems(b, m)
        _who_said(b, m)
    series = [by_id[s] for s in SERIES if s in by_id]
    for before, after in itertools.pairwise(series):
        _follow_ups(b, before, after)
        _decision_changes(b, before, after)
    return b.out


# -- closed-book control ---------------------------------------------------------------------------

CLOSED_BOOK_PROMPT = "closed_book.v1"


class ClosedBookPrompt(BaseModel):
    """A question exactly as the closed-book control sends it: no meeting material at all."""

    model_config = ConfigDict(extra="forbid")

    qid: str
    prompt_id: str
    system: str
    user: str


def closed_book_user(q: AmiQuestion) -> str:
    """The closed-book user turn: the question and nothing else."""
    return f"Question: {q.question}"


def closed_book_prompts(qs: Sequence[AmiQuestion], system: str) -> list[ClosedBookPrompt]:
    return [
        ClosedBookPrompt(
            qid=q.id, prompt_id=CLOSED_BOOK_PROMPT, system=system, user=closed_book_user(q)
        )
        for q in qs
    ]


# -- files -----------------------------------------------------------------------------------------


def write_jsonl(rows: Iterable[BaseModel], path: Path) -> None:
    """One JSON object per line, keys in model order (byte-identical for the same seed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in rows))


def load_questions(path: Path = OUT_DIR / "questions.jsonl") -> list[AmiQuestion]:
    return [AmiQuestion.model_validate_json(x) for x in path.read_text().splitlines() if x]


def counts(qs: Sequence[AmiQuestion]) -> dict[str, dict[str, int]]:
    """Questions per type and split."""
    out: dict[str, dict[str, int]] = {}
    for q in qs:
        row = out.setdefault(q.type, {"dev": 0, "test": 0})
        row[q.split] += 1
    return out

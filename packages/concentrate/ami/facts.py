"""Stage 1 of "Anatomy of a wrong fact": did each fact survive transcription?

A *fact token* is a short span a wrong answer usually hinges on. From AMI's manual transcript
(its dialogue acts, or word turns where a meeting has none) we extract four kinds,
deterministically and at $0:

- **number**: a maximal run of number words after :func:`concentrate.ami.wer.normalise`
  ("25" and "twenty-five" both become ``twenty five``), within one utterance. A lone "one"
  is left out: it is mostly a pronoun;
- **money_percent**: a number followed by a unit ("twenty five euros", "ten percent");
- **name**: a word written capitalised mid-sentence and never in lower case (in practice
  proper names: people, places, organisations, languages; "I", fillers, interjections,
  months, weekdays, role words and AMI's spelled acronyms are excluded). Once a word is a
  name, every occurrence counts, sentence-initial ones too;
- **role**: the scenario role terms "project manager", "marketing", "industrial designer",
  "user interface".

For one product transcript we report, per kind:

- **recall**: of the human fact tokens (with multiplicity), the share also in the product's
  transcript, in any order (like content-word recall in ``wer.py``);
- **substitutions**: by local alignment. Each human fact is located in the product transcript
  by the n-grams around it (anchors), then the two windows are aligned word by word
  (edit distance with a backtrace). A fact aligned to *different* words is a substitution
  ("twenty five" -> "thirty five"); aligned to nothing, a deletion; with no anchor, unaligned;
- **name insertions**: words the product writes as names that never occur anywhere in the
  human transcript. This is how a product writing the account holder's name into AMI audio
  shows up (``owner_name_insertions`` counts those that are the owner's first name).

Results hold counts plus at most :data:`MAX_EXAMPLES` short examples per kind (normalised
words, a few either side). Kian approved short quoted examples beside the AMI source
(``docs/benchmark/AMI_DESIGN.md``). ``FACTS_VERSION`` is logged with every result; bump it when
extraction or alignment changes.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from concentrate.ami.corpus import AmiMeeting, Word, meeting_note
from concentrate.ami.wer import FILLERS, STOP, content_recall, normalise, spell_number
from concentrate.core.models import Channel, MeetingNote, Utterance, utterance_id
from concentrate.evals.faithfulness import MONTHS, WEEKDAYS

FACTS_VERSION = 1
KINDS = ("number", "money_percent", "name", "role")
MAX_EXAMPLES = 5
CONTEXT = 4  # words shown either side of an example
WINDOW = 6  # words of context either side of a fact used to anchor and align it
SLACK = 4  # extra product words allowed either side of the anchored window
ANCHOR_N = 3  # anchor n-gram length (2 for short queries)
MAX_ANCHOR_HITS = 40  # n-grams this common are useless as anchors
MIN_VOTES = 2  # anchors that must agree (within TOLERANCE) to place a fact
TOLERANCE = 3

NUMBER_WORDS = frozenset(
    {w for n in range(100) for w in spell_number(n).split()} | {"hundred", "thousand", "million"}
)
UNITS = frozenset(
    {"euro", "euros", "pound", "pounds", "dollar", "dollars", "cent", "cents", "pence", "percent"}
)
ROLE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("project", "manager"),
    ("industrial", "designer"),
    ("user", "interface"),
    ("marketing",),
)
_NOT_NAMES = frozenset(
    {
        *STOP,
        *FILLERS,
        *MONTHS,
        *WEEKDAYS,
        "i've",
        "i'd",
        "mister",
        "missus",
        "doctor",
        "okay",
        "mr",
        "mrs",
        "ms",
        "dr",
    }
    | {"oh", "ah", "hey", "wow", "alright"}
    | {w for phrase in ROLE_PHRASES for w in phrase}
)
_SENTENCE_END = (".", "?", "!")


# -- token streams with case -----------------------------------------------------------------


@dataclass(frozen=True)
class Stream:
    """A transcript as normalised tokens, with how each token was written.

    ``case[i]`` is ``"lower"``, ``"upper"`` (capitalised mid-sentence) or ``"initial"``
    (capitalised at a sentence or utterance start, so uninformative). Tokens split from one
    written word ("twenty-five") after the first are ``"lower"``. ``breaks`` holds the token
    positions where a new utterance starts: a number never runs across one.
    """

    tokens: tuple[str, ...]
    case: tuple[str, ...]
    breaks: frozenset[int]


def stream(utterances: Iterable[str]) -> Stream:
    """Normalise a transcript, one string per utterance, into a :class:`Stream`.

    A word starts a sentence when it opens an utterance or follows a word ending in ``.?!``.
    """
    toks: list[str] = []
    case: list[str] = []
    breaks: set[int] = set()
    for text in utterances:
        breaks.add(len(toks))
        initial = True
        for raw in text.split():
            for k, tok in enumerate(normalise(raw)):
                if tok == "cent" and toks and toks[-1] == "per" and len(toks) - 1 not in breaks:
                    toks[-1] = "percent"  # "per cent" and "percent" are one unit
                    continue
                toks.append(tok)
                if k == 0 and raw[:1].isupper():
                    case.append("initial" if initial else "upper")
                else:
                    case.append("lower")
            initial = raw.rstrip("\"')]").endswith(_SENTENCE_END)
    return Stream(tuple(toks), tuple(case), frozenset(breaks))


TURN_GAP_S = 1.0  # a pause this long starts a new word turn


def human_note(m: AmiMeeting) -> MeetingNote:
    """AMI's manual transcript as a :class:`MeetingNote`: one utterance per dialogue act
    (:func:`corpus.meeting_note`), or, for a meeting without dialogue acts (EN2002b), one per
    word turn: a new turn at a change of speaker or a pause of :data:`TURN_GAP_S`. Turns keep
    AMI's punctuation, so sentence starts stay visible to :func:`name_vocab`."""
    note = meeting_note(m)
    if note.utterances:
        return note
    turns: list[list[Word]] = []
    for w in m.words:
        if w.trunc:
            continue
        last = turns[-1][-1] if turns else None
        if w.punc:
            if last is not None and last.agent == w.agent:
                turns[-1].append(w)
        elif last is None or w.agent != last.agent or w.start - last.end >= TURN_GAP_S:
            turns.append([w])
        else:
            turns[-1].append(w)
    utts = tuple(
        Utterance(
            utterance_id=utterance_id(m.meeting_id, i),
            note_id=m.meeting_id,
            index=i,
            channel=Channel.THEM,
            speaker_name=f"Speaker {t[0].agent}",
            diarization_label=t[0].agent,
            text="".join(w.text if w.punc else f" {w.text}" for w in t).strip(),
            start_time=note.created_at + timedelta(seconds=t[0].start),
            end_time=note.created_at + timedelta(seconds=t[-1].end),
        )
        for i, t in enumerate(turns)
    )
    return note.model_copy(update={"utterances": utts})


def name_vocab(s: Stream) -> set[str]:
    """Tokens written capitalised mid-sentence and never in lower case: people's names."""
    upper = {t for t, c in zip(s.tokens, s.case, strict=True) if c == "upper"}
    lower = {t for t, c in zip(s.tokens, s.case, strict=True) if c == "lower"}
    return {t for t in upper - lower if t not in _NOT_NAMES and len(t) > 1 and t.isalpha()}


# -- fact extraction ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    kind: str  # one of KINDS
    tokens: tuple[str, ...]
    start: int  # position in the stream's tokens

    @property
    def end(self) -> int:
        return self.start + len(self.tokens)

    @property
    def text(self) -> str:
        return " ".join(self.tokens)


def extract(
    tokens: Sequence[str], names: set[str], breaks: frozenset[int] = frozenset()
) -> list[Fact]:
    """Every fact token in ``tokens`` (normalised words), in order of position then kind.

    A number run stops at an utterance break (``breaks``: positions that start one).
    """
    out: list[Fact] = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i] in NUMBER_WORDS:
            j = i + 1
            while j < n and tokens[j] in NUMBER_WORDS and j not in breaks:
                j += 1
            run = tuple(tokens[i:j])
            if run != ("one",):
                out.append(Fact("number", run, i))
                unit = _unit(tokens, j)
                if unit:
                    out.append(Fact("money_percent", (*run, unit[0]), i))
            i = j
            continue
        i += 1
    for i in range(n):
        for phrase in ROLE_PHRASES:
            if tuple(tokens[i : i + len(phrase)]) == phrase:
                out.append(Fact("role", phrase, i))
        if tokens[i] in names:
            out.append(Fact("name", (tokens[i],), i))
    out.sort(key=lambda f: (f.start, KINDS.index(f.kind)))
    return out


def _unit(tokens: Sequence[str], j: int) -> tuple[str] | None:
    return (tokens[j],) if j < len(tokens) and tokens[j] in UNITS else None


# -- anchoring and local alignment -------------------------------------------------------------


class NgramIndex:
    """Positions of every n-gram (n = 2 and 3) in a token list, for anchoring."""

    def __init__(self, tokens: Sequence[str]) -> None:
        self.tokens = tuple(tokens)
        self._pos: dict[tuple[str, ...], list[int]] = {}
        for n in (2, ANCHOR_N):
            for i in range(len(tokens) - n + 1):
                self._pos.setdefault(tuple(tokens[i : i + n]), []).append(i)

    def locate(self, query: Sequence[str], target: int) -> int | None:
        """Where ``query[target]`` most likely sits in the indexed tokens, or None.

        Every n-gram of ``query`` found in the index votes for an implied position; the
        position with the most votes within ``TOLERANCE`` wins if it has ``MIN_VOTES``
        (ties: the earliest). Very common n-grams don't vote.
        """
        n = ANCHOR_N if len(query) > ANCHOR_N + 1 else 2
        votes: list[int] = []
        for j in range(len(query) - n + 1):
            hits = self._pos.get(tuple(query[j : j + n]), [])
            if len(hits) <= MAX_ANCHOR_HITS:
                votes.extend(p + (target - j) for p in hits)
        if not votes:
            return None
        votes.sort()
        best, best_score = None, 0
        for v in votes:
            score = bisect_right(votes, v + TOLERANCE) - bisect_left(votes, v - TOLERANCE)
            if score > best_score:
                best, best_score = v, score
        return best if best_score >= MIN_VOTES else None


Op = tuple[str, int | None, int | None]  # ("M"|"S"|"D"|"I", human index, product index)


def align(h: Sequence[str], p: Sequence[str]) -> list[Op]:
    """Align all of ``h`` to a stretch of ``p`` (free gaps at both ends of ``p``).

    Word-level edit distance (substitution, deletion, insertion all cost 1) with a backtrace.
    Ties prefer a diagonal step, then a deletion; the end in ``p`` is the latest best one
    (so trailing context is aligned rather than dropped).
    """
    rows, cols = len(h) + 1, len(p) + 1
    d = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        d[i][0] = i
        for j in range(1, cols):
            d[i][j] = min(
                d[i - 1][j - 1] + (h[i - 1] != p[j - 1]), d[i - 1][j] + 1, d[i][j - 1] + 1
            )
    j = max(range(cols), key=lambda c: (-d[rows - 1][c], c))
    i = rows - 1
    ops: list[Op] = []
    while i > 0:
        if j > 0 and d[i][j] == d[i - 1][j - 1] + (h[i - 1] != p[j - 1]):
            ops.append(("M" if h[i - 1] == p[j - 1] else "S", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            ops.append(("D", i - 1, None))
            i -= 1
        else:
            ops.append(("I", None, j - 1))
            j -= 1
    ops.reverse()
    return ops


def aligned_span(ops: Sequence[Op], a: int, b: int) -> list[int]:
    """Product indices aligned to human indices ``[a, b)``.

    Product words inserted inside the span, or right next to it (before the neighbouring
    aligned word), count too: they sit where the fact was ("Christine" -> "Chris Steen").
    """
    touching = [k for k, (_, i, _) in enumerate(ops) if i is not None and a <= i < b]
    if not touching:
        return []
    lo, hi = touching[0], touching[-1]
    while lo > 0 and ops[lo - 1][0] == "I":
        lo -= 1
    while hi + 1 < len(ops) and ops[hi + 1][0] == "I":
        hi += 1
    return [j for _, _, j in ops[lo : hi + 1] if j is not None]


@dataclass(frozen=True)
class Placement:
    """Where one human fact landed in the product transcript."""

    status: str  # "match", "substitution", "deletion" or "unaligned"
    product: tuple[str, ...]  # the product words aligned to it
    at: int | None  # product position of the anchored window's start


def place(fact: Fact, human: Sequence[str], index: NgramIndex) -> Placement:
    """Anchor ``fact`` in the product transcript and align the surrounding windows."""
    lo, hi = max(0, fact.start - WINDOW), min(len(human), fact.end + WINDOW)
    window = human[lo:hi]
    centre = index.locate(window, fact.start - lo)
    if centre is None:
        return Placement("unaligned", (), None)
    p_lo = max(0, centre - (fact.start - lo) - SLACK)
    p_hi = min(len(index.tokens), centre + (hi - fact.start) + SLACK)
    ops = align(window, index.tokens[p_lo:p_hi])
    span = tuple(index.tokens[p_lo + j] for j in aligned_span(ops, fact.start - lo, fact.end - lo))
    k = len(fact.tokens)
    if any(span[i : i + k] == fact.tokens for i in range(len(span) - k + 1)):
        return Placement("match", span, p_lo)  # extra neighbouring words don't change it
    return Placement("substitution" if span else "deletion", span, p_lo)


# -- scoring -----------------------------------------------------------------------------------


class Example(BaseModel):
    model_config = ConfigDict(frozen=True)

    human: str  # the fact as AMI has it ("" for an insertion)
    product: str  # what the product wrote there ("" for a deletion)
    human_context: str  # a few normalised words either side, from AMI's manual words
    product_context: str


class KindScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    human: int  # fact tokens in the human transcript (with multiplicity)
    product: int  # the same fact tokens found in the product transcript
    recall: float | None  # min-count overlap / human; None when the human has none
    aligned: int  # facts anchored in the product transcript
    matches: int
    substitutions: int
    deletions: int
    unaligned: int
    examples: list[Example]  # substitutions first, then deletions; at most MAX_EXAMPLES


class Stage1Result(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    product: str
    drop_mic: bool
    human_tokens: int
    product_tokens: int
    kinds: dict[str, KindScore]
    name_insertions: int  # product name tokens absent from the whole human transcript
    inserted_names: int  # distinct such names
    owner_name_insertions: int  # of those, the account owner's first name
    insertion_examples: list[Example]


def _ctx(tokens: Sequence[str], a: int, b: int) -> str:
    return " ".join(tokens[max(0, a - CONTEXT) : b + CONTEXT])


def score_kind(
    kind: str,
    human: Sequence[str],
    human_facts: Sequence[Fact],
    product_facts: Sequence[Fact],
    index: NgramIndex,
) -> KindScore:
    """Recall, substitutions and deletions for one kind of fact token."""
    hf = [f for f in human_facts if f.kind == kind]
    want = Counter(f.tokens for f in hf)
    have = Counter(f.tokens for f in product_facts if f.kind == kind)
    found = sum(min(c, have[t]) for t, c in want.items())
    counts: Counter[str] = Counter()
    subs: list[Example] = []
    dels: list[Example] = []
    for f in hf:
        pl = place(f, human, index)
        counts[pl.status] += 1
        if pl.status in ("substitution", "deletion") and pl.at is not None:
            ex = Example(
                human=f.text,
                product=" ".join(pl.product),
                human_context=_ctx(human, f.start, f.end),
                product_context=" ".join(
                    index.tokens[pl.at : pl.at + 2 * (WINDOW + SLACK) + len(f.tokens)]
                ),
            )
            (subs if pl.status == "substitution" else dels).append(ex)
    return KindScore(
        kind=kind,
        human=len(hf),
        product=found,
        recall=round(found / len(hf), 4) if hf else None,
        aligned=len(hf) - counts["unaligned"],
        matches=counts["match"],
        substitutions=counts["substitution"],
        deletions=counts["deletion"],
        unaligned=counts["unaligned"],
        examples=(subs + dels)[:MAX_EXAMPLES],
    )


def score_transcript(
    meeting: AmiMeeting,
    product_texts: Sequence[str],
    product: str,
    *,
    drop_mic: bool,
    owner_name: str | None = None,
) -> Stage1Result:
    """Stage 1 for one meeting x product: fact-token recall, substitutions, name insertions.

    ``product_texts`` are the product's utterances in order (mic turns already dropped when
    ``drop_mic``; the flag is only recorded here).
    """
    hs = stream(u.text for u in human_note(meeting).utterances)
    ps = stream(product_texts)
    names = name_vocab(hs)
    human_facts = extract(hs.tokens, names, hs.breaks)
    product_facts = extract(ps.tokens, names, ps.breaks)
    index = NgramIndex(ps.tokens)
    kinds = {k: score_kind(k, hs.tokens, human_facts, product_facts, index) for k in KINDS}
    said = set(hs.tokens)
    product_names = name_vocab(ps) - said
    inserted = [(i, t) for i, t in enumerate(ps.tokens) if t in product_names]
    owner = (owner_name or "").split()[:1]
    owner_first = normalise(owner[0])[0] if owner and normalise(owner[0]) else None
    return Stage1Result(
        meeting_id=meeting.meeting_id,
        product=product,
        drop_mic=drop_mic,
        human_tokens=len(hs.tokens),
        product_tokens=len(ps.tokens),
        kinds=kinds,
        name_insertions=len(inserted),
        inserted_names=len({t for _, t in inserted}),
        owner_name_insertions=sum(t == owner_first for _, t in inserted),
        insertion_examples=[
            Example(
                human="", product=t, human_context="", product_context=_ctx(ps.tokens, i, i + 1)
            )
            for i, t in inserted[:MAX_EXAMPLES]
        ],
    )


# -- evidence recovery (used by stage 3) -------------------------------------------------------

EVIDENCE_RECALL = 0.5  # an act counts as recovered when this share of its content words is there


def act_recovered(act_text: str, names: set[str], index: NgramIndex) -> tuple[float, bool]:
    """Content-word recall of one human dialogue act in its anchored product window, and
    whether it counts as recovered (recall >= EVIDENCE_RECALL and every fact token in it
    present in the window). An act with no anchor scores 0."""
    toks = list(stream([act_text]).tokens)
    if not toks:
        return 0.0, False
    centre = index.locate(toks, 0)
    if centre is None:
        return 0.0, False
    window = index.tokens[max(0, centre - SLACK) : centre + len(toks) + SLACK]
    recall = content_recall(toks, list(window))
    have = Counter(f.tokens for f in extract(window, names))
    need = Counter(f.tokens for f in extract(toks, names))
    facts_ok = all(have[t] >= c for t, c in need.items())
    return recall, recall >= EVIDENCE_RECALL and facts_ok

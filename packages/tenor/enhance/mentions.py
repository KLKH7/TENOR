"""Mention linking: find spans that name a person, org or project and link them to the directory.

Matching, in order of trust:

1. **Glossary phrases** (case-insensitive multi-word names and aliases: "Beacon Freight",
   "the payments migration") and capitalized single-word names ("Falcon", "Acmeway").
2. **Spoken ticket codes**: "PAY two twelve", "pay 212", "PAY-212" → the project whose alias is
   ``PAY-212`` (numbers parsed with ``tenor.core.spoken``).
3. **Person names**: full names exactly; first names exactly or *fuzzily* (Jaro-Winkler plus
   Double Metaphone) to survive ASR ("Coffee" → Kofi). Fuzzy matches of words that are also
   ordinary English need a name-like context ("Coffee, what's on your plate?").

Overlaps are resolved **longest match first**, so "Beacon Freight" (customer) beats "Beacon"
(project). A first name shared by several people ("Sam") is disambiguated by the meeting's
attendees, then by co-attendance history; if that is not decisive the link stays ambiguous.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tenor.core.models import MeetingNote, Utterance
from tenor.core.spoken import spoken_numbers
from tenor.enhance.context import Priors
from tenor.enhance.directory import Directory
from tenor.enhance.models import EntityKind, MentionLink
from tenor.enhance.strings import fold, name_similarity, sounds_alike

MIN_LINK_CONFIDENCE = 0.55
FUZZY_MIN = 0.82  # fuzzy first-name match without supporting context
FUZZY_CONTEXT_MIN = 0.7  # ... when the token sounds alike *and* sits in a name-like context

# Capitalized words that open sentences in speech and are never names here.
DISCOURSE = {
    "yeah", "yes", "okay", "ok", "right", "sorry", "so", "um", "uh", "like", "honestly",
    "hey", "hi", "then", "them", "than", "sure", "same", "seen", "dear", "mean", "well",
    "thanks", "thank", "great", "cool", "totally", "yep", "got", "and", "but", "or", "oh",
    "the", "that", "this", "there", "their", "they", "what", "when", "where", "who", "how",
    "let", "lets", "let's", "can", "could", "would", "should", "is", "it", "its", "it's", "i",
    "we", "you", "my", "our", "your", "if", "not", "no", "now", "just", "also", "anyway",
    "good", "nice", "fine", "maybe", "actually", "basically", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november", "december",
}  # fmt: skip
_LEAD_WORDS = {"okay", "so", "thanks", "thank", "hi", "hey", "and", "oh", "yeah", "right", "um",
               "uh", "well", "sorry", "alright", "cool", "great", "morning", "you"}  # fmt: skip
_AFTER_NAME = {"is", "was", "will", "said", "says", "has", "had", "and", "mentioned", "thinks",
               "wants", "can", "could", "would", "should", "did", "does", "isn't", "already",
               "here", "just", "took", "owns", "asked", "agreed", "might", "needs", "found",
               "flagged", "noticed", "raised", "fixed", "sent", "wrote", "shipped", "built",
               "joined", "reported", "thought", "knows"}  # fmt: skip
_BEFORE_NAME = {"with", "to", "ask", "asked", "tell", "told", "for", "from", "and", "thanks",
                "ping", "cc", "by", "about", "loop", "looped", "via", "let", "or", "that",
                "thank", "hey", "hi", "talked", "met", "per"}  # fmt: skip

_APOS = "\u2019"  # typographic apostrophe
_TOKEN = re.compile(rf"[^\W_]+(?:[-'{_APOS}][^\W_]+)*")
_NUM_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|oh)"
)


@dataclass(frozen=True)
class _Phrase:
    tokens: tuple[str, ...]  # folded
    kind: EntityKind
    entity_id: str
    confidence: float
    reason: str
    needs_capital: bool  # single-word names must be capitalized ("Falcon", not "falcon")


@dataclass(frozen=True)
class _Code:
    pattern: re.Pattern[str]
    number: int
    entity_id: str
    code: str


@dataclass
class _Match:
    start: int
    end: int
    kind: EntityKind
    entity_id: str | None
    confidence: float
    reason: str


def _tokens(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)]


def _strip_possessive(tok: str) -> str:
    for suffix in ("'s", f"{_APOS}s"):
        if tok.lower().endswith(suffix):
            return tok[: -len(suffix)]
    return tok


class Gazetteer:
    """Directory names compiled for matching (built once per directory)."""

    def __init__(self, directory: Directory) -> None:
        self.directory = directory
        self.phrases: dict[tuple[str, ...], list[_Phrase]] = {}
        self.codes: list[_Code] = []
        self.first_names = sorted({fold(p.first) for p in directory.people})
        self._build()
        # Words of org/project names ("Teams", "Freight") are never misheard first names.
        self.entity_words = {w for key, ps in self.phrases.items() for w in key
                             if any(p.kind != "person" for p in ps)}  # fmt: skip
        self.max_len = max((len(k) for k in self.phrases), default=1)

    def _add(self, text: str, kind: EntityKind, eid: str, conf: float, reason: str) -> None:
        toks = tuple(fold(t) for _, _, t in _tokens(text))
        if not toks:
            return
        phrase = _Phrase(toks, kind, eid, conf, reason, needs_capital=len(toks) == 1)
        self.phrases.setdefault(toks, []).append(phrase)

    def _build(self) -> None:
        d = self.directory
        for p in d.people:
            self._add(p.name, "person", p.id, 0.98, "full name matches the directory")
        single_names: set[str] = set()
        for o in d.orgs:
            self._add(o.name, "org", o.id, 0.95, "organization name")
            single_names.add(fold(o.name))
        for pr in d.projects:
            self._add(pr.name, "project", pr.id, 0.95, "project name")
            single_names.add(fold(pr.name))
            for alias in pr.aliases:
                code = re.fullmatch(r"([A-Za-z]+)-(\d+)", alias)
                if code:
                    self._add_code(code.group(1), int(code.group(2)), pr.id, alias)
                    continue
                bare = re.sub(r"^the\s+", "", alias, flags=re.I)
                self._add(bare, "project", pr.id, 0.9, f"glossary alias '{alias}' of {pr.name}")
        # Short org names ("Corvid" for Corvid Bank) when they collide with nothing else.
        taken = single_names | set(self.first_names_all())
        shorts: dict[str, list[str]] = {}
        for o in d.orgs:
            words = o.name.split()
            if len(words) > 1 and len(words[0]) >= 4:
                shorts.setdefault(fold(words[0]), []).append(o.id)
        for short, ids in shorts.items():
            if len(ids) == 1 and short not in taken:
                org = next(o for o in d.orgs if o.id == ids[0])
                self._add(short, "org", ids[0], 0.8, f"short name of {org.name}")

    def first_names_all(self) -> list[str]:
        return [fold(p.first) for p in self.directory.people]

    def _add_code(self, prefix: str, number: int, eid: str, code: str) -> None:
        pattern = re.compile(
            rf"\b{re.escape(prefix)}(?:-|\s+)(\d+|{_NUM_WORD}(?:[\s-]+{_NUM_WORD})*)\b", re.I
        )
        self.codes.append(_Code(pattern, number, eid, code))

    def first_name_matches(self, token: str) -> list[tuple[str, float]]:
        """(person id, similarity) for directory people whose first name matches ``token``.

        Exact matches suppress fuzzy ones; fuzzy candidates are kept within 0.03 of the best.
        """
        folded = fold(token)
        exact = self.directory.by_first_name.get(folded)
        if exact:
            return [(p.id, 1.0) for p in exact]
        scored = [(name, name_similarity(folded, name)) for name in self.first_names]
        best = max((s for _, s in scored), default=0.0)
        out: list[tuple[str, float]] = []
        for name, sim in scored:
            if sim >= best - 0.03 and sim >= FUZZY_CONTEXT_MIN:
                out += [(p.id, sim) for p in self.directory.by_first_name[name]]
        return out


def _spoken_code_value(s: str) -> str:
    s = s.strip()
    if s.isdigit():
        return s
    s = re.sub(r"\boh\b", "zero", s.lower())
    return "".join(str(n) for n in spoken_numbers(s))


def is_vocative(text: str, start: int, end: int) -> bool:
    """Is the name at ``text[start:end]`` addressing someone ("Megan, what's...", "..., Kofi?")."""
    pre = text[:start].rstrip()
    post = text[end:].lstrip()
    if post.startswith(","):
        if not pre or pre[-1] in ".?!-":
            return True
        last = pre.split()[-1].strip(",").lower()
        return last in _LEAD_WORDS
    if post[:1] in {"?", ""} or post.startswith("."):
        return pre.endswith(",")
    return False


def _name_context(text: str, start: int, end: int, token: str) -> bool:
    """Does the context make a capitalized word look like a person's name?"""
    if is_vocative(text, start, end) or token != _strip_possessive(token):
        return True
    post = text[end:].lstrip()
    pre = text[:start].rstrip()
    nxt = post.split(" ", 1)[0].strip(",.?!").lower() if post else ""
    prv = pre.split()[-1].strip(",.?!").lower() if pre else ""
    return nxt in _AFTER_NAME or prv in _BEFORE_NAME


def _sentence_initial(text: str, start: int) -> bool:
    pre = text[:start].rstrip()
    return not pre or pre[-1] in ".?!-"


class MentionLinker:
    """Links mentions in one note, given its attendees and history priors."""

    def __init__(
        self,
        gazetteer: Gazetteer,
        attendees: Sequence[str],
        priors: Priors,
        lowercase_vocab: set[str],
    ) -> None:
        self.gz = gazetteer
        self.attendees = list(attendees)
        self.priors = priors
        self.vocab = lowercase_vocab

    def link_utterance(self, u: Utterance) -> list[MentionLink]:
        return [
            MentionLink(
                utterance_id=u.utterance_id,
                start=m.start,
                end=m.end,
                text=u.text[m.start : m.end],
                kind=m.kind,
                entity_id=m.entity_id,
                confidence=round(m.confidence, 3),
                reason=m.reason,
            )
            for m in self.find(u.text)
        ]

    def find(self, text: str) -> list[_Match]:
        """All non-overlapping entity mentions in ``text``, longest first."""
        toks = _tokens(text)
        found: list[_Match] = []
        # 1. glossary phrases
        for i in range(len(toks)):
            for n in range(min(self.gz.max_len, len(toks) - i), 0, -1):
                window = toks[i : i + n]
                last = fold(_strip_possessive(window[-1][2]))
                key = (*(fold(t) for _, _, t in window[:-1]), last)
                for ph in self.gz.phrases.get(key, []):
                    if ph.needs_capital and not window[0][2][:1].isupper():
                        continue
                    end = window[-1][0] + len(_strip_possessive(window[-1][2]))
                    found.append(self._phrase_match(ph, window[0][0], end, text))
        # 2. spoken ticket codes
        for code in self.gz.codes:
            for m in code.pattern.finditer(text):
                if _spoken_code_value(m.group(1)) == str(code.number):
                    found.append(
                        _Match(m.start(), m.end(), "project", code.entity_id, 0.95,
                               f"spoken ticket code '{m.group()}' = {code.code}")
                    )  # fmt: skip
        # 3. first names (exact or fuzzy)
        for start, _, tok in toks:
            word = _strip_possessive(tok)
            if not word[:1].isupper() or len(word) < 3 or fold(word) in DISCOURSE:
                continue
            if "'" in word or _APOS in word:  # contractions: "We'll", "I've"
                continue
            end = start + len(word)
            match = self._first_name(text, start, end, word)
            if match:
                found.append(match)
        return _longest_first(found)

    def _phrase_match(self, ph: _Phrase, start: int, end: int, text: str) -> _Match:
        return _Match(start, end, ph.kind, ph.entity_id, ph.confidence, ph.reason)

    def _first_name(self, text: str, start: int, end: int, word: str) -> _Match | None:
        cands = self.gz.first_name_matches(word)
        if not cands:
            return None
        surname = re.match(r" ([A-Z][^\W\d_]+)\b", text[end:])
        if surname and fold(surname.group(1)) not in DISCOURSE:
            return None  # "Lucy Park": someone outside the directory, not a misheard "Luis"
        sim = max(s for _, s in cands)
        context = _name_context(text, start, end, word)
        common = fold(word) in self.vocab
        if sim < 1.0:
            if fold(word) in self.gz.entity_words:
                return None
            sounds = any(sounds_alike(word, self._first(pid)) for pid, _ in cands)
            ok = (sim >= FUZZY_MIN and (context or not common)) or (
                sounds and sim >= FUZZY_CONTEXT_MIN and context
            )
            if not ok:
                return None
        elif common and _sentence_initial(text, start) and not context:
            return None
        return self.resolve_first_name(start, end, word, cands)

    def _first(self, pid: str) -> str:
        p = self.gz.directory.by_id[pid]
        return p.first

    def resolve_first_name(
        self, start: int, end: int, word: str, cands: list[tuple[str, float]]
    ) -> _Match:
        """Pick among people sharing a (heard) first name using attendees and co-attendance."""
        weights: dict[str, float] = {}
        for pid, sim in cands:
            w = sim**8
            if pid in self.attendees:
                w *= 8.0
            w *= 1.0 + 3.0 * self.priors.cooccurrence(pid, self.attendees)
            weights[pid] = max(weights.get(pid, 0.0), w)
        total = sum(weights.values())
        ranked = sorted(weights, key=lambda p: (-weights[p], p))
        best = ranked[0]
        sim = dict(cands)[best]
        conf = sim * weights[best] / total
        heard = "" if sim == 1.0 else f" (heard '{word}', sim {sim:.2f})"
        if conf < MIN_LINK_CONFIDENCE:
            names = "|".join(ranked[:4])
            return _Match(start, end, "person", None, conf, f"ambiguous first name: {names}{heard}")
        why = "attendee" if best in self.attendees else "co-attendance prior"
        if len(ranked) == 1:
            why = "unique first name in directory"
        return _Match(start, end, "person", best, conf, f"first name; {why}{heard}")


def _longest_first(found: list[_Match]) -> list[_Match]:
    chosen: list[_Match] = []
    for m in sorted(found, key=lambda m: (-(m.end - m.start), -m.confidence, m.start)):
        if all(m.end <= c.start or m.start >= c.end for c in chosen):
            chosen.append(m)
    return sorted(chosen, key=lambda m: m.start)


def lowercase_vocabulary(note: MeetingNote) -> set[str]:
    """Words this note uses in lowercase: evidence that a capitalized form is a common word."""
    vocab: set[str] = set()
    for u in note.utterances:
        for _, _, tok in _tokens(u.text):
            if tok[:1].islower():
                vocab.add(fold(_strip_possessive(tok)))
    return vocab

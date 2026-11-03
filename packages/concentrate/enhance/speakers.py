"""Speaker attribution: who said each utterance, with a confidence and the reason.

Each utterance gets a log-linear score per candidate person: a prior plus *cues*, each worth a
fixed number of log-odds points (hand-set or measured on the base fixture set only; XL is the
held-out check). A softmax turns scores into probabilities; the winner's probability is the
confidence. Neighbouring lines are coupled in two ways:

* remote meetings: unnamed system-audio lines form chains with their neighbours, solved exactly
  by forward-backward (a linear-chain model: label propagation along the conversation);
* in person with iOS diarization: one label per cluster, chosen jointly and re-scored until the
  assignment stops changing (label propagation across a cluster).

Signals by meeting mode:

* **Remote** (any system-audio line): the microphone is the note owner. System-audio lines with a
  platform display name map to the directory. Unnamed system-audio lines are scored with an
  attendee prior (people Granola never names are the likely owners of unnamed lines), turn
  continuity (a fragment continuing the previous sentence), addressing ("Kofi, what's on your
  plate?" → Kofi answers next) and self-reference ("this is Owen").
* **In person** (everything on one microphone): the attendees are the candidates; a mild owner
  prior (it is the owner's device), addressing and self-reference cues, and, when iOS
  diarization labels exist, a joint assignment of clusters to people.

``version="enhance-v2"`` adds two cues and leaves everything else as in v1 (docs/concentrate/
enhance_v2.md):

* **task continuity** (any unattributed line): a first-person take/status line about a task
  ("I'll take the skills matrix", "the skills matrix is done") favours the attendees who talked
  about the same task on hard-identified lines in other meetings (``concentrate.enhance.tasks``);
* **diarized owner**: the owner prior on the largest diarization cluster is worth
  ``W_DIARIZED_OWNER`` (it is the owner's phone), so a clean two-cluster 1:1 clears the threshold.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise, permutations

from concentrate.core.models import Channel, MeetingNote, Utterance
from concentrate.enhance.context import Priors, resolve_display_name, series_key
from concentrate.enhance.directory import Directory
from concentrate.enhance.mentions import is_vocative
from concentrate.enhance.models import MentionLink, SpeakerResolution
from concentrate.enhance.tasks import TaskIndex, task_phrases

# Log-odds weights for cues (tuned on base only; see docs/concentrate/enhance.md).
# Odds that two adjacent system-audio lines share a speaker, by how they join (measured on the
# base fixture set: a continued sentence 26/30, a one-word lead-in 51/53, otherwise ~1:1). The
# chain weight converts odds to a log-potential given the effective number of candidates.
ODDS_CONTINUATION = 6.5
ODDS_LEAD_IN = 25.0
ODDS_SAME_TURN = 1.0
W_ADDRESSED = 2.5  # previous turn addressed this person by name
W_SELF_REFERENCE = 4.0  # "this is Owen", "Owen here"
W_ADDRESSES_OTHER = -1.5  # a line that addresses X by name is unlikely to be X
W_MENTIONS_OTHER = -0.5  # a line that names X in the third person is less likely to be X
W_IN_PERSON_OWNER = 0.4  # owner prior on a shared microphone (it is the owner's device)
W_TASK = 2.5  # v2: share of a task's identified speakers (0..1) times this
W_DIARIZED_OWNER = 1.5  # v2: owner prior on the largest diarization cluster
VERSIONS = ("enhance-v1", "enhance-v2")
UNLABELED_RATE = 0.12  # assumed share of an identified speaker's lines left unnamed
MIN_CONFIDENCE = 0.6
ROUNDS = 4

_LEAD_INS = {"so", "yeah", "um", "uh", "okay", "right", "i mean", "and", "well", "oh"}
_SELF = re.compile(
    r"\b(?:I'm|I am|this is|it's|my name is)\s+([A-Z][\w'\u2019-]+)|\b([A-Z][\w'\u2019-]+) here\b"
)


@dataclass
class _Line:
    u: Utterance
    fixed: str | None = None  # resolved by a hard signal (owner mic, platform name)
    fixed_conf: float = 0.0
    fixed_method: str = ""
    cues: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    best: str | None = None
    conf: float = 0.0
    posterior: dict[str, float] = field(default_factory=dict)
    used: dict[str, list[tuple[str, float]]] = field(default_factory=dict)  # cues behind best

    def label(self) -> tuple[str | None, float]:
        if self.fixed is not None:
            return self.fixed, self.fixed_conf
        return self.best, self.conf


def _softmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    exp = {k: math.exp(v - top) for k, v in scores.items()}
    z = sum(exp.values())
    return {k: v / z for k, v in exp.items()}


def _ends_sentence(text: str) -> bool:
    return text.rstrip()[-1:] in {".", "?", "!"}


def _continues(prev: str, cur: str) -> bool:
    """Does ``cur`` read as the continuation of ``prev``'s sentence?"""
    first = cur.lstrip()[:1]
    return not _ends_sentence(prev) or (first.isalpha() and first.islower())


def _is_lead_in(text: str) -> bool:
    return text.strip().rstrip(".,").lower() in _LEAD_INS


def _is_backchannel(u: Utterance) -> bool:
    return len(u.text.split()) <= 2


class SpeakerResolver:
    """Attributes every utterance of one note."""

    def __init__(
        self,
        note: MeetingNote,
        directory: Directory,
        attendees: Sequence[str],
        owner: str | None,
        priors: Priors,
        mentions: Sequence[MentionLink],
        title_people: Sequence[str],
        min_confidence: float = MIN_CONFIDENCE,
        version: str = "enhance-v1",
        tasks: TaskIndex | None = None,
    ) -> None:
        if version not in VERSIONS:
            raise ValueError(f"unknown enhancer version {version!r}; expected one of {VERSIONS}")
        self.version = version
        self.tasks = priors.tasks if tasks is None else tasks
        self.note = note
        self.directory = directory
        self.attendees = list(attendees)
        self.owner = owner
        self.priors = priors
        self.min_confidence = min_confidence
        self.lines = [_Line(u) for u in note.utterances]
        self.person_mentions: dict[str, list[MentionLink]] = {}
        for m in mentions:
            if m.kind == "person" and m.entity_id is not None:
                self.person_mentions.setdefault(m.utterance_id, []).append(m)
        self.remote = any(u.channel is Channel.THEM for u in note.utterances)
        self.title_people = list(title_people)
        self.k_eff = 2.0

    # -- candidates -------------------------------------------------------------------------

    def _candidates(self) -> list[str]:
        cands = list(self.attendees)
        for line in self.lines:
            if line.fixed and line.fixed not in cands:
                cands.append(line.fixed)
        for pid in self.title_people:
            if pid not in cands:
                cands.append(pid)
        if len([c for c in cands if c != self.owner]) == 0:
            # No calendar: fall back to who attended this series before.
            key = series_key(self.note)
            past = self.priors.series.get(key, Counter())
            cands += [p for p, _ in past.most_common(6) if p not in cands]
        return cands

    # -- main -------------------------------------------------------------------------------

    def resolve(self) -> dict[str, SpeakerResolution]:
        if self.remote:
            self._fix_remote()
        cands = self._candidates()
        self._static_cues(cands)
        if self.remote:
            them = [c for c in cands if c != self.owner]
            prior = self._remote_prior(them)
            self._remote_chain(them, prior)
        else:
            prior = self._in_person_prior(cands)
            labels = {u.diarization_label for u in self.note.utterances}
            if None not in labels and len(labels) > 1 and len(labels) <= len(cands):
                self._diarized(cands, prior)
            else:
                for _ in range(ROUNDS):
                    if not self._score(self.lines, prior, dynamic=self._in_person_dynamic):
                        break
        return {ln.u.utterance_id: self._decide(ln) for ln in self.lines}

    def _fix_remote(self) -> None:
        for ln in self.lines:
            u = ln.u
            if u.channel is Channel.ME and self.owner is not None:
                ln.fixed, ln.fixed_conf, ln.fixed_method = self.owner, 0.97, "owner_mic"
            elif u.speaker_name:
                pid = resolve_display_name(u.speaker_name, self.directory)
                if pid is not None:
                    ln.fixed, ln.fixed_conf, ln.fixed_method = pid, 0.99, "platform_name"

    # -- priors -----------------------------------------------------------------------------

    def _remote_prior(self, them: list[str]) -> dict[str, float]:
        """Unnamed-line prior: identified people leave few unnamed lines, the rest go to the
        attendees Granola never named, weighted by how much they talk historically."""
        if not them:
            return {}
        named = Counter(ln.fixed for ln in self.lines if ln.fixed and ln.u.channel is Channel.THEM)
        unnamed = sum(1 for ln in self.lines if ln.fixed is None and ln.u.channel is Channel.THEM)
        expected = {k: named[k] * UNLABELED_RATE for k in them}
        residual = max(0.0, unnamed - sum(expected.values()))
        silent = [k for k in them if named[k] == 0]
        talk = {k: self.priors.talkativeness(k) for k in silent}
        z = sum(talk.values()) or 1.0
        for k in silent:
            expected[k] += residual * talk[k] / z
        total = sum(expected.values()) + 0.05 * len(them)
        # Effective number of system-audio speakers: perplexity of who says the lines.
        volume = [named[k] + expected[k] for k in them]
        z = sum(volume) or 1.0
        entropy = -sum(v / z * math.log(v / z) for v in volume if v > 0)
        self.k_eff = max(2.0, math.exp(entropy))
        return {k: math.log((expected[k] + 0.05) / total) for k in them}

    def _in_person_prior(self, cands: list[str]) -> dict[str, float]:
        if not cands:
            return {}
        out = {k: 0.0 for k in cands}
        if self.owner in out:
            out[self.owner] = W_IN_PERSON_OWNER
        key = series_key(self.note)
        for k in cands:  # people outside the calendar (title, series) start lower
            if k not in self.attendees:
                out[k] -= 1.0 - self.priors.series_share(key, k)
        return out

    # -- cues -------------------------------------------------------------------------------

    def _static_cues(self, cands: list[str]) -> None:
        """Cues that depend only on the line's own text."""
        for ln in self.lines:
            text = ln.u.text
            for m in self.person_mentions.get(ln.u.utterance_id, []):
                pid = m.entity_id
                assert pid is not None
                if pid not in cands:
                    continue
                if is_vocative(text, m.start, m.end):
                    ln.cues.setdefault(pid, []).append(("addresses_other", W_ADDRESSES_OTHER))
                else:
                    ln.cues.setdefault(pid, []).append(("mentions_other", W_MENTIONS_OTHER))
            for sm in _SELF.finditer(text):
                name = sm.group(1) or sm.group(2)
                for m in self.person_mentions.get(ln.u.utterance_id, []):
                    if m.entity_id in cands and text[m.start : m.end] == name:
                        cue = ("self_reference", W_SELF_REFERENCE - W_MENTIONS_OTHER)
                        ln.cues.setdefault(m.entity_id, []).append(cue)
            if self.version == "enhance-v2":
                for pid, w in self._task_cues(text, cands):
                    ln.cues.setdefault(pid, []).append(("task_continuity", w))

    def _task_cues(self, text: str, cands: list[str]) -> list[tuple[str, float]]:
        """v2: candidates who took on or reported on this line's tasks on identified lines."""
        out: dict[str, float] = {}
        for key in task_phrases(text):
            said = {k: n for k, n in self.tasks.get(key, Counter()).items() if k in cands}
            total = sum(said.values())
            for pid, n in said.items():
                out[pid] = max(out.get(pid, 0.0), W_TASK * n / total)
        return sorted(out.items())

    def _addressees(self, i: int) -> list[str]:
        """People addressed by name in line ``i`` (excluding the line's own speaker)."""
        ln = self.lines[i]
        speaker, _ = ln.label()
        out = []
        for m in self.person_mentions.get(ln.u.utterance_id, []):
            if m.entity_id and m.entity_id != speaker and is_vocative(ln.u.text, m.start, m.end):
                out.append(m.entity_id)
        return out

    def _remote_chain(self, them: list[str], prior: dict[str, float]) -> None:
        """Exact posteriors for unnamed system-audio lines by forward-backward on each run.

        A *run* is a stretch of consecutive system-audio lines with no real owner turn in
        between. Along a run, adjacent lines prefer the same speaker by a pairwise weight
        (strong for a sentence that continues or a one-word lead-in, mild otherwise); named lines
        are observed. This is a linear-chain model (an HMM), so propagation is exact and never
        feeds a line's own guess back to itself, unlike naive iterative relabelling.
        """
        if not them:
            return
        runs: list[list[int]] = []
        for i, ln in enumerate(self.lines):
            if ln.u.channel is not Channel.THEM:
                continue
            before = self._neighbour(i, -1)
            if (
                runs
                and before == runs[-1][-1]
                and not self._crosstalk(i)
                and not self._crosstalk(before)
            ):
                runs[-1].append(i)
            else:
                runs.append([i])
        k_index = {k: n for n, k in enumerate(them)}
        k_eff = self.k_eff
        for run in runs:
            local = [self._local_scores(i, them, prior) for i in run]
            unary = [[math.exp(x - max(sc)) for x in sc] for sc in local]
            weights = [self._pair_weight(a, b, k_eff) for a, b in pairwise(run)]
            fwd: list[list[float]] = []
            for t, phi in enumerate(unary):
                if t == 0:
                    msg = [1.0] * len(them)
                else:
                    prev, boost = fwd[-1], math.exp(weights[t - 1]) - 1.0
                    total = sum(prev)
                    msg = [total + boost * x for x in prev]
                row = [m * f for m, f in zip(msg, phi, strict=True)]
                z = sum(row) or 1.0
                fwd.append([x / z for x in row])
            bwd: list[list[float]] = [[1.0] * len(them) for _ in run]
            for t in range(len(run) - 2, -1, -1):
                nxt = [f * b for f, b in zip(unary[t + 1], bwd[t + 1], strict=True)]
                total, boost = sum(nxt), math.exp(weights[t]) - 1.0
                row = [total + boost * x for x in nxt]
                z = sum(row) or 1.0
                bwd[t] = [x / z for x in row]
            for t, i in enumerate(run):
                ln = self.lines[i]
                if ln.fixed is not None:
                    continue
                post = [f * b for f, b in zip(fwd[t], bwd[t], strict=True)]
                z = sum(post) or 1.0
                ln.posterior = {k: post[k_index[k]] / z for k in them}
                ln.best = max(them, key=lambda k: (ln.posterior[k], k))
                ln.conf = ln.posterior[ln.best]
                loc = _softmax(dict(zip(them, local[t], strict=True)))
                gain = math.log(max(ln.conf, 1e-9) / max(loc[ln.best], 1e-9))
                ln.used.setdefault(ln.best, []).append(("turn_continuity", gain))

    def _local_scores(self, i: int, them: list[str], prior: dict[str, float]) -> list[float]:
        """Log-scores for line ``i`` from its own evidence (prior, text cues, addressing)."""
        ln = self.lines[i]
        if ln.fixed is not None:
            return [0.0 if k == ln.fixed else -12.0 for k in them]
        scores = dict(prior)
        cues = [(pid, m, w) for pid, cs in ln.cues.items() for m, w in cs]
        cues += [(pid, "addressed", W_ADDRESSED * d) for pid, d in self._addressed_before(i)]
        for pid, method, w in cues:
            if pid in scores:
                scores[pid] += w
                ln.used.setdefault(pid, []).append((method, w))
        return [scores[k] for k in them]

    def _pair_weight(self, a: int, b: int, k_eff: float) -> float:
        """Log-potential favouring a shared speaker for adjacent system-audio lines a -> b.

        With ``k_eff`` plausible speakers, a potential of ``odds * (k_eff - 1)`` on "same" makes
        P(same) equal the measured odds before any other evidence.
        """
        ta, tb = self.lines[a].u.text, self.lines[b].u.text
        if _continues(ta, tb):
            odds = ODDS_CONTINUATION
        elif _is_lead_in(ta):
            odds = ODDS_LEAD_IN
        else:
            odds = ODDS_SAME_TURN
        return math.log(max(1.0, odds * (k_eff - 1.0)))

    def _crosstalk(self, i: int) -> bool:
        """A short system-audio interjection that overlaps the owner's ongoing turn ("Yeah.").

        Anyone can say it; it says nothing about who holds the floor, so it is not chained to
        its neighbours (it keeps its prior-only, lower-confidence guess).
        """
        u = self.lines[i].u
        if u.channel is not Channel.THEM or len(u.text.split()) > 3:
            return False
        for j in range(i - 1, max(-1, i - 4), -1):
            v = self.lines[j].u
            if v.channel is Channel.ME:
                return v.end_time > u.start_time
        return False

    def _neighbour(self, i: int, step: int) -> int | None:
        """Nearest system-audio line in direction ``step``, unless a real owner turn intervenes."""
        j = i + step
        while 0 <= j < len(self.lines):
            u = self.lines[j].u
            if u.channel is Channel.THEM:
                return j
            if not _is_backchannel(u):
                return None
            j += step
        return None

    def _addressed_before(self, i: int) -> list[tuple[str, float]]:
        """Addressees of the most recent addressing line within the previous few turns."""
        out: list[tuple[str, float]] = []
        them_seen = 0
        j = i - 1
        while j >= 0 and i - j <= 6:
            ln = self.lines[j]
            addressees = self._addressees(j)
            if addressees:
                return [(a, 1.0 / (1 + 0.5 * them_seen)) for a in addressees]
            if ln.fixed is not None and ln.u.channel is Channel.THEM and not _is_backchannel(ln.u):
                return out  # a different identified speaker took the floor
            if ln.u.channel is Channel.THEM:
                them_seen += 1
            j -= 1
        return out

    def _in_person_dynamic(self, i: int) -> list[tuple[str, str, float]]:
        """In person: a block that ends by addressing X makes X likely to speak next."""
        if i == 0:
            return []
        prev = self.lines[i - 1].u
        out = []
        for m in self.person_mentions.get(prev.utterance_id, []):
            if m.entity_id and is_vocative(prev.text, m.start, m.end):
                tail = prev.text[m.start :]
                if len(tail.split()) <= 12:  # the address is near the end of the block
                    out.append((m.entity_id, "addressed", W_ADDRESSED * 0.6))
        return out

    # -- scoring ----------------------------------------------------------------------------

    def _score(
        self,
        lines: list[_Line],
        prior: dict[str, float],
        dynamic: Callable[[int], list[tuple[str, str, float]]],
    ) -> bool:
        """One propagation round; returns True if any label changed."""
        index = {id(ln): k for k, ln in enumerate(self.lines)}
        updates: list[tuple[_Line, dict[str, float], dict[str, list[tuple[str, float]]]]] = []
        for ln in lines:
            i = index[id(ln)]
            scores = dict(prior)
            contrib: dict[str, list[tuple[str, float]]] = {}
            for pid, cues in ln.cues.items():
                if pid in scores:
                    for method, w in cues:
                        scores[pid] += w
                        contrib.setdefault(pid, []).append((method, w))
            for pid, method, w in dynamic(i):
                if pid in scores:
                    scores[pid] += w
                    contrib.setdefault(pid, []).append((method, w))
            updates.append((ln, scores, contrib))
        changed = False
        for ln, scores, contrib in updates:
            post = _softmax(scores)
            if not post:
                continue
            best = max(sorted(post), key=lambda k: post[k])
            changed |= best != ln.best
            ln.best, ln.conf, ln.posterior = best, post[best], post
            ln.used = contrib
        return changed

    def _diarized(self, cands: list[str], prior: dict[str, float]) -> None:
        """Assign whole diarization clusters to distinct people (joint best assignment)."""
        clusters: dict[str, list[int]] = {}
        for i, ln in enumerate(self.lines):
            assert ln.u.diarization_label is not None
            clusters.setdefault(ln.u.diarization_label, []).append(i)
        names = sorted(clusters)
        assignment: dict[str, str] = {}
        margin = 0.0

        def owner_w(pid: str) -> float:
            if self.version == "enhance-v2" and pid == self.owner:
                return W_DIARIZED_OWNER
            return prior.get(pid, 0.0)

        for _ in range(ROUNDS):
            evidence: dict[str, dict[str, float]] = {}
            for c in names:
                ev = dict.fromkeys(cands, 0.0)
                for i in clusters[c]:
                    for pid, cues in self.lines[i].cues.items():
                        if pid in ev:
                            ev[pid] += sum(w for _, w in cues)
                    for pid, _, w in self._in_person_dynamic(i):
                        if pid in ev:
                            ev[pid] += w
                evidence[c] = ev
            # The largest cluster gets the owner prior (the owner's own device).
            words = {c: sum(len(self.lines[i].u.text.split()) for i in clusters[c]) for c in names}
            biggest = max(names, key=lambda c: (words[c], c))
            ranked: list[tuple[float, tuple[str, ...]]] = []
            for perm in permutations(cands, len(names)):
                total = 0.0
                for c, pid in zip(names, perm, strict=True):
                    total += evidence[c][pid] + (owner_w(pid) if c == biggest else 0.0)
                ranked.append((total, perm))
            ranked.sort(key=lambda t: (-t[0], t[1]))
            new = dict(zip(names, ranked[0][1], strict=True))
            margin = ranked[0][0] - ranked[1][0] if len(ranked) > 1 else 5.0
            if new == assignment:
                break
            assignment = new
            for c, pid in assignment.items():
                for i in clusters[c]:
                    self.lines[i].best = pid
        conf = 1.0 / (1.0 + math.exp(-margin))
        for c, pid in assignment.items():
            for i in clusters[c]:
                ln = self.lines[i]
                ln.best, ln.conf = pid, conf
                others = [k for k in cands if k != pid]
                ln.posterior = {pid: conf, **{k: (1 - conf) / max(1, len(others)) for k in others}}
                ln.used = {pid: [("diarization_propagation", margin)]}

    # -- decision ---------------------------------------------------------------------------

    def _decide(self, ln: _Line) -> SpeakerResolution:
        if ln.fixed is not None:
            return SpeakerResolution(
                person_id=ln.fixed,
                confidence=ln.fixed_conf,
                method=ln.fixed_method,
                candidates=[ln.fixed],
            )
        if ln.best is None:
            return SpeakerResolution(person_id=None, confidence=0.0, method="unresolved",
                                     candidates=[])  # fmt: skip
        ranked = sorted(ln.posterior, key=lambda k: (-ln.posterior[k], k))
        candidates = [k for k in ranked if ln.posterior[k] >= 0.1][:4] or ranked[:1]
        conf = round(ln.conf, 3)
        if conf < self.min_confidence:
            return SpeakerResolution(
                person_id=None, confidence=conf, method="unresolved", candidates=candidates
            )
        positives = [(w, m) for m, w in ln.used.get(ln.best, []) if w > 0]
        method = max(positives)[1] if positives and max(positives)[0] >= 0.25 else None
        if method is None:
            method = "owner_mic" if ln.best == self.owner and not self.remote else "attendee_prior"
        return SpeakerResolution(
            person_id=ln.best, confidence=conf, method=method, candidates=candidates
        )

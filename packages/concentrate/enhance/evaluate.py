"""Score Enhance against ground truth regenerated from the deterministic synth pipeline.

The fixture's ``manifest.json`` gives the seed, scale and noise rates. We rebuild the world
(``synth.pipeline.build``) and re-render every meeting (``synth.render.render_meeting`` with
``gold_out``), which yields the true speaker of every utterance and the beats it came from. The
regenerated transcripts are checked to be identical to the fixture's, so gold and input can't
drift apart. ``enhance`` itself only ever sees the fixture notes and ``directory.json``.

Usage: ``uv run python -m concentrate.enhance.evaluate [--version enhance-v2] [--lookahead]
data/fixtures/northwind``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from concentrate.core.models import Channel, MeetingNote, normalize_note
from concentrate.enhance.directory import Directory
from concentrate.enhance.enhancer import enhance_all
from concentrate.enhance.models import EnhancedNote, SpeakerResolution
from concentrate.sources.fixtures import FixtureSource
from concentrate.synth.noise import NoiseRates
from concentrate.synth.pipeline import build
from concentrate.synth.render import GoldMap, folder_models, render_meeting
from concentrate.synth.roster import PEOPLE
from concentrate.synth.world import Meeting, World

THRESHOLDS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
SAMS = {"sam_p", "sam_o", "sam_r"}
ALEXES = {"alex_k", "alex_r"}


@dataclass
class Gold:
    """Fixture notes plus the regenerated truth for them."""

    world: World
    notes: list[MeetingNote]
    gold: GoldMap
    meeting_of: dict[str, Meeting]  # note id -> world meeting
    directory: Directory


def load_gold(fixture_dir: Path) -> Gold:
    """Regenerate ground truth for a fixture set and check it matches the fixture byte-for-text."""
    manifest = json.loads((fixture_dir / "manifest.json").read_text())
    if manifest["polish"]["enabled"]:
        raise ValueError("polished fixtures need the LLM cache to regenerate; not supported")
    seed, scale = int(manifest["seed"]), str(manifest["scale"])
    rates = NoiseRates(**manifest["noise_rates"])
    world = build(seed, scale)
    folders = folder_models(seed, scale)
    gold: GoldMap = {}
    regenerated: dict[str, MeetingNote] = {}
    for m in world.meetings:
        note, _ = render_meeting(m, [b.text for b in m.beats], world, seed, rates, folders, gold)
        regenerated[note.id] = normalize_note(note)
    src = FixtureSource(fixture_dir)
    notes = [normalize_note(src.get_note(s.id)) for s in src.list_notes()]
    for n in notes:
        r = regenerated.get(n.note_id)
        if r is None or [u.text for u in r.utterances] != [u.text for u in n.utterances]:
            raise ValueError(f"fixture note {n.note_id} does not match the regenerated one")
    return Gold(
        world=world,
        notes=notes,
        gold=gold,
        meeting_of={m.note_id: m for m in world.meetings},
        directory=Directory.load(fixture_dir),
    )


# -- speaker attribution -----------------------------------------------------------------------


class Stat(BaseModel):
    n: int = 0
    resolved: int = 0
    correct: int = 0

    def add(self, resolved: bool, correct: bool) -> None:
        self.n += 1
        self.resolved += resolved
        self.correct += correct

    @property
    def coverage(self) -> float:
        return self.resolved / self.n if self.n else 0.0

    @property
    def precision(self) -> float:
        return self.correct / self.resolved if self.resolved else 0.0

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0


class CurvePoint(BaseModel):
    threshold: float
    coverage: float
    accuracy: float  # among covered lines


class MentionStat(BaseModel):
    gold: int = 0
    found: int = 0  # gold pairs recovered
    predicted: int = 0  # resolved person links
    correct_predictions: int = 0
    abstained: int = 0  # ambiguous links (entity_id None) overlapping a gold mention

    @property
    def precision(self) -> float:
        return self.correct_predictions / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.found / self.gold if self.gold else 0.0


class ErrorCase(BaseModel):
    utterance_id: str
    mode: str
    text: str
    gold: str
    predicted: str | None
    candidates: list[str]
    method: str
    confidence: float
    mixed_block: bool


class Report(BaseModel):
    fixture: str
    meetings: int
    utterances: int
    speaker: dict[str, Stat]
    methods: dict[str, Stat]
    curves: dict[str, list[CurvePoint]]
    mixed_blocks: int
    traps: dict[str, Stat]
    baselines: dict[str, Stat]
    mention_traps: dict[str, MentionStat]
    mentions_person: MentionStat
    mentions_by_person: dict[str, MentionStat]
    entity_traps: dict[str, Stat]
    worst: list[ErrorCase]


@dataclass
class _Slices:
    stats: dict[str, Stat] = field(default_factory=dict)

    def add(self, keys: list[str], resolved: bool, correct: bool) -> None:
        for k in keys:
            self.stats.setdefault(k, Stat()).add(resolved, correct)


def _guess(r: SpeakerResolution) -> str | None:
    return r.person_id or (r.candidates[0] if r.candidates else None)


def _curve(rows: list[tuple[SpeakerResolution, str]]) -> list[CurvePoint]:
    out = []
    for t in THRESHOLDS:
        covered = [(r, g) for r, g in rows if _guess(r) is not None and r.confidence >= t]
        acc = sum(_guess(r) == g for r, g in covered) / len(covered) if covered else 0.0
        out.append(CurvePoint(threshold=t, coverage=len(covered) / max(1, len(rows)), accuracy=acc))
    return out


def _score_speakers(g: Gold, enhanced: dict[str, EnhancedNote]) -> dict[str, Any]:
    slices, methods, traps, baseline = _Slices(), _Slices(), _Slices(), _Slices()
    rows: dict[str, list[tuple[SpeakerResolution, str]]] = {}
    errors: list[ErrorCase] = []
    mixed = 0
    for note in g.notes:
        mode = g.meeting_of[note.note_id].mode
        e = enhanced[note.note_id]
        for u in note.utterances:
            gu = g.gold[u.utterance_id]
            truth = gu.speaker
            is_mixed = len(gu.words_by_speaker) > 1
            mixed += is_mixed
            r = e.speakers[u.utterance_id]
            resolved = r.person_id is not None
            correct = r.person_id == truth
            named = "named" if u.speaker_name else "unnamed"
            keys = [
                "overall",
                mode,
                f"{mode}/{u.channel.value}",
                f"{mode}/{u.channel.value}/{named}",
            ]
            if mode == "in_person":
                keys.append(f"in_person/{'diarized' if u.diarization_label else 'no_diarization'}")
                keys.append(f"in_person/{'mixed_block' if is_mixed else 'single_speaker'}")
            slices.add(keys, resolved, correct)
            methods.add([r.method], resolved, correct)
            if mode == "in_person":
                baseline.add(["in-person: always the note owner"], True, truth == g.world.owner)
            if truth in SAMS:
                traps.add([f"both Sams ({named})", "both Sams (all)"], resolved, correct)
            if truth in ALEXES:
                traps.add([f"both Alexes ({named})", "both Alexes (all)"], resolved, correct)
            unnamed_them = u.channel is Channel.THEM and not u.speaker_name
            for name, member in (
                ("all lines", True),
                ("hard lines (unnamed system audio + in-person)",
                 mode == "in_person" or unnamed_them),
                ("unnamed system audio (remote)", unnamed_them),
                ("in-person", mode == "in_person"),
            ):  # fmt: skip
                if member:
                    rows.setdefault(name, []).append((r, truth))
            if resolved and not correct:
                errors.append(
                    ErrorCase(
                        utterance_id=u.utterance_id, mode=mode, text=u.text[:160], gold=truth,
                        predicted=r.person_id, candidates=r.candidates, method=r.method,
                        confidence=r.confidence, mixed_block=is_mixed,
                    )
                )  # fmt: skip
    errors.sort(key=lambda c: (-c.confidence, c.utterance_id))
    return {
        "speaker": slices.stats,
        "methods": methods.stats,
        "curves": {name: _curve(rs) for name, rs in rows.items()},
        "mixed_blocks": mixed,
        "traps": traps.stats,
        "baselines": baseline.stats,
        "worst": errors[:10],
    }


# -- mention linking ---------------------------------------------------------------------------

BeatKey = tuple[str, int]  # (note id, beat index)


def _gold_person_mentions(g: Gold) -> tuple[dict[BeatKey, set[str]], set[BeatKey]]:
    """(beat -> people named in its clean text, beats with an undecidable shared first name).

    Gold comes from the *clean* beat text (before ASR noise), so a misheard "Coffee" is still a
    gold mention of Kofi. A shared first name ("Sam") is assigned by the meeting's true
    attendees, then by commitments evidenced in that beat; otherwise it is left out.
    """
    by_first: dict[str, list[str]] = {}
    ids = set(g.directory.by_id)
    for pid in sorted(ids):
        by_first.setdefault(PEOPLE[pid].first, []).append(pid)
    commit_people: dict[BeatKey, set[str]] = {}
    for c in g.world.commitments:
        who = {c.owner, *([c.new_owner] if c.new_owner else [])}
        for ev in [*c.made_evidence, *c.resolved_evidence]:
            nid = g.world.meeting(ev.meeting).note_id
            commit_people.setdefault((nid, ev.beat), set()).update(who)
    gold: dict[BeatKey, set[str]] = {}
    ambiguous: set[BeatKey] = set()
    for m in g.world.meetings:
        for i, beat in enumerate(m.beats):
            key = (m.note_id, i)
            text = beat.text
            for pid in sorted(ids):  # full names first
                if PEOPLE[pid].name in text:
                    gold.setdefault(key, set()).add(pid)
                    text = text.replace(PEOPLE[pid].name, " ")
            for first, pids in by_first.items():
                if not re.search(rf"\b{re.escape(first)}\b", text):
                    continue
                if len(pids) == 1:
                    gold.setdefault(key, set()).add(pids[0])
                    continue
                present = [p for p in pids if p in m.attendees]
                via_commit = [p for p in pids if p in commit_people.get(key, set())]
                pick = via_commit if len(via_commit) == 1 else present
                if len(pick) == 1:
                    gold.setdefault(key, set()).add(pick[0])
                else:
                    ambiguous.add(key)
    return gold, ambiguous


def _score_mentions(g: Gold, enhanced: dict[str, EnhancedNote]) -> dict[str, Any]:
    gold, ambiguous = _gold_person_mentions(g)
    found: set[tuple[BeatKey, str]] = set()
    overall = MentionStat()
    per: dict[str, MentionStat] = {}
    traps: dict[str, MentionStat] = {}
    variants = {
        v: p.id for p in PEOPLE.values() if p.id in g.directory.by_id for v in p.asr_variants
        if v != p.first
    }  # fmt: skip
    text_of = {u.utterance_id: u.text for n in g.notes for u in n.utterances}
    utts_of: dict[BeatKey, list[str]] = {}
    for uid, gu in g.gold.items():
        for beat in gu.beats:
            utts_of.setdefault((uid.rsplit(":", 1)[0], beat), []).append(uid)
    for note in g.notes:
        for link in enhanced[note.note_id].mentions:
            if link.kind != "person":
                continue
            beats = [(note.note_id, beat) for beat in g.gold[link.utterance_id].beats]
            gold_here = {p for bk in beats for p in gold.get(bk, set())}
            if link.entity_id is None:
                overall.abstained += bool(gold_here)
                continue
            if any(bk in ambiguous for bk in beats) and link.entity_id not in gold_here:
                continue  # gold can't say which Sam; don't score
            overall.predicted += 1
            ok = link.entity_id in gold_here
            overall.correct_predictions += ok
            st = per.setdefault(link.entity_id, MentionStat())
            st.predicted += 1
            st.correct_predictions += ok
            if ok:
                for bk in beats:
                    if link.entity_id in gold.get(bk, set()):
                        found.add((bk, link.entity_id))
    for key, pids in gold.items():
        for pid in pids:
            overall.gold += 1
            st = per.setdefault(pid, MentionStat())
            st.gold += 1
            hit = (key, pid) in found
            overall.found += hit
            st.found += hit
            trap = None
            if pid in SAMS:
                trap = "Sam (first name shared)"
            elif pid in ALEXES:
                trap = "Alex (first name shared)"
            utts = utts_of.get(key, [])
            heard = {v for uid in utts for v, vp in variants.items() if vp == pid and
                     re.search(rf"\b{v}\b", text_of.get(uid, ""))}  # fmt: skip
            keys = [trap] if trap else []
            keys += [f"{PEOPLE[pid].first} heard as '{v}'" for v in sorted(heard)]
            for k in keys:
                t = traps.setdefault(k, MentionStat())
                t.gold += 1
                t.found += hit
    return {"mentions_person": overall, "mentions_by_person": per, "mention_traps": traps}


def _score_entity_traps(g: Gold, enhanced: dict[str, EnhancedNote]) -> dict[str, Stat]:
    """Beacon vs Beacon Freight, and Falcon = PAY-212 = the payments migration."""
    links: dict[BeatKey, list[tuple[str, str | None]]] = {}
    for note in g.notes:
        for link in enhanced[note.note_id].mentions:
            for b in g.gold[link.utterance_id].beats:
                links.setdefault((note.note_id, b), []).append((link.kind, link.entity_id))
    cases = {
        "'Beacon Freight' -> customer org": (r"Beacon Freight", "org", "beacon_freight"),
        "'Beacon' alone -> project": (r"\bBeacon\b(?! Freight)", "project", "beacon"),
        "'Falcon' -> project falcon": (r"\bFalcon\b", "project", "falcon"),
        "'PAY two twelve' / PAY-212 -> falcon": (r"PAY(?:-| )(?:212|two twelve)", "project",
                                                 "falcon"),
        "'the payments migration' -> falcon": (r"payments migration", "project", "falcon"),
    }  # fmt: skip
    out: dict[str, Stat] = {}
    for m in g.world.meetings:
        for i, beat in enumerate(m.beats):
            got = links.get((m.note_id, i), [])
            for name, (pattern, kind, eid) in cases.items():
                for _ in re.finditer(pattern, beat.text):
                    st = out.setdefault(name, Stat())
                    st.add(any(k == kind for k, _ in got), (kind, eid) in got)
    return out


# -- entry points ------------------------------------------------------------------------------


def evaluate(fixture_dir: Path, version: str = "enhance-v1", lookahead: bool = False) -> Report:
    """Enhance every fixture note (chronologically, with history) and score against gold."""
    g = load_gold(fixture_dir)
    enhanced = enhance_all(g.notes, g.directory, version=version, lookahead=lookahead)
    sp = _score_speakers(g, enhanced)
    mn = _score_mentions(g, enhanced)
    return Report(
        fixture=str(fixture_dir),
        meetings=len(g.notes),
        utterances=sum(len(n.utterances) for n in g.notes),
        entity_traps=_score_entity_traps(g, enhanced),
        **sp,
        **mn,
    )


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def format_report(r: Report) -> str:
    """Markdown tables for docs/archive/concentrate/enhance.md."""
    lines = [f"### {r.fixture}: {r.meetings} meetings, {r.utterances} utterances", ""]
    lines += ["| Slice | Lines | Coverage | Precision (resolved) | Accuracy (all) |",
              "|---|---:|---:|---:|---:|"]  # fmt: skip
    for k in sorted(r.speaker, key=lambda k: (k.count("/"), k)):
        s = r.speaker[k]
        lines.append(f"| {k} | {s.n} | {_pct(s.coverage)} | {_pct(s.precision)} | "
                     f"{_pct(s.accuracy)} |")  # fmt: skip
    lines += ["", f"In-person blocks with more than one true speaker: {r.mixed_blocks}", ""]
    lines += ["| Method | Lines | Precision |", "|---|---:|---:|"]
    for k, s in sorted(r.methods.items()):
        lines.append(f"| {k} | {s.n} | {_pct(s.precision) if s.resolved else '-'} |")
    names = list(r.curves)
    lines += ["", "Coverage / accuracy-of-covered at confidence thresholds (unresolved lines count "
              "with their best candidate)", "",
              "| Threshold | " + " | ".join(names) + " |",
              "|---:|" + "---:|" * len(names)]  # fmt: skip
    for row, t in enumerate(THRESHOLDS):
        cells = [f"{_pct(r.curves[n][row].coverage)} / {_pct(r.curves[n][row].accuracy)}"
                 for n in names]  # fmt: skip
        lines.append(f"| {t:.2f} | " + " | ".join(cells) + " |")
    for k, s in sorted(r.baselines.items()):
        lines += ["", f"Baseline, {k}: accuracy {_pct(s.accuracy)} on {s.n} lines"]
    lines += ["", "| Speaker trap | Lines | Coverage | Precision | Accuracy |",
              "|---|---:|---:|---:|---:|"]  # fmt: skip
    for k, s in sorted(r.traps.items()):
        lines.append(f"| {k} | {s.n} | {_pct(s.coverage)} | {_pct(s.precision)} | "
                     f"{_pct(s.accuracy)} |")  # fmt: skip
    m = r.mentions_person
    lines += ["", "| Person mention linking | Gold | Predicted | Precision | Recall |",
              "|---|---:|---:|---:|---:|",
              f"| all roster persons | {m.gold} | {m.predicted} | {_pct(m.precision)} | "
              f"{_pct(m.recall)} |"]  # fmt: skip
    for pid, ms in sorted(r.mentions_by_person.items()):
        lines.append(f"| {pid} | {ms.gold} | {ms.predicted} | {_pct(ms.precision)} | "
                     f"{_pct(ms.recall)} |")  # fmt: skip
    lines += ["", f"Ambiguous links (no id) on a gold mention: {m.abstained}", "",
              "| Mention trap | Gold | Recall |", "|---|---:|---:|"]  # fmt: skip
    for k, ms in sorted(r.mention_traps.items()):
        lines.append(f"| {k} | {ms.gold} | {_pct(ms.recall)} |")
    lines += ["", "| Entity trap (beat-level) | Occurrences | Linked to right kind | Correct |",
              "|---|---:|---:|---:|"]  # fmt: skip
    for k, s in sorted(r.entity_traps.items()):
        lines.append(f"| {k} | {s.n} | {_pct(s.coverage)} | {_pct(s.accuracy)} |")
    lines += ["", "Highest-confidence speaker errors:", ""]
    for c in r.worst:
        lines.append(
            f"- `{c.utterance_id}` ({c.mode}{', mixed' if c.mixed_block else ''}) gold "
            f"**{c.gold}**, predicted **{c.predicted}** via {c.method} @ {c.confidence:.2f}: "
            f'"{c.text}"'
        )
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    version, lookahead, paths = "enhance-v1", False, []
    args = iter(argv)
    for arg in args:
        if arg == "--version":
            version = next(args)
        elif arg == "--lookahead":
            lookahead = True
        else:
            paths.append(arg)
    for arg in paths or ["data/fixtures/northwind"]:
        report = evaluate(Path(arg), version, lookahead)
        print(format_report(report))
        print()


if __name__ == "__main__":
    main(sys.argv[1:])

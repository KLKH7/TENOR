"""Anatomy of a wrong fact: all three stages on a tiny synthetic AMI meeting. No network, $0.

The planted errors: the product transcript writes the account owner's name ("Kian") where AMI
says "Christine", and hears "twenty five euros" as "35 euros". A note claim repeats the
"Kian" error (transcript_propagated); another invents a number (note_error).
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

from concentrate.ami import facts as F
from concentrate.ami.corpus import load_meeting, meeting_note
from concentrate.ami.products import match_recordings, read_overrides
from concentrate.ami.questions import AmiQuestion
from concentrate.core.models import MeetingNote, Person
from concentrate.core.store import NoteStore, read_notes
from concentrate.evals import wrong_facts as W
from concentrate.llm.client import Completion, LLMClient, LLMRequest
from tests.memory_helpers import make_note

MID = "XX0001a"
NS = 'xmlns:nite="http://nite.sourceforge.net/"'
HUMAN = [
    ("A", "Good morning everyone, let us start the kickoff meeting now."),
    ("B", "Thanks, my colleague Christine will present the remote control budget today."),
    ("A", "The budget for each remote is twenty five euros in total."),
    ("B", "Christine said the target market is young people with money."),
    ("A", "Okay so the next meeting is in thirty minutes from now."),
]
PRODUCT = [
    "Good morning everyone, let us start the kickoff meeting now.",
    "Thanks, my colleague Kian will present the remote control budget today.",
    "The budget for each remote is 35 euros in total.",
    "Kian said the target market is young people with money.",
    "Okay so the next meeting is in 30 minutes from now.",
]


def _zip(path: Path) -> Path:
    """A one-meeting NXT zip: one dialogue act per turn, one second per word."""
    words: dict[str, list[str]] = {"A": [], "B": []}
    acts: dict[str, list[str]] = {"A": [], "B": []}
    t = 0.0
    for n, (agent, sentence) in enumerate(HUMAN):
        first = len(words[agent])
        for tok in re.findall(r"[\w'-]+|[.,?!]", sentence):
            i = len(words[agent])
            punc = ' punc="true"' if tok in ".,?!" else ""
            words[agent].append(
                f'<w nite:id="{MID}.{agent}.words{i}" starttime="{t}" endtime="{t + 0.9}"'
                f"{punc}>{tok}</w>"
            )
            t += 1.0
        last = len(words[agent]) - 1
        acts[agent].append(
            f'<dact nite:id="{MID}.{agent}.da.{n}"><nite:child href="{MID}.{agent}.words.xml'
            f'#id({MID}.{agent}.words{first})..id({MID}.{agent}.words{last})"/></dact>'
        )
    meetings = (
        f'<nite:root {NS}><meeting observation="{MID}" type="scenario" duration="60">'
        '<speaker nxt_agent="A" role="PM" global_name="P1" channel="0"/>'
        '<speaker nxt_agent="B" role="ME" global_name="P2" channel="1"/></meeting></nite:root>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("corpusResources/meetings.xml", meetings)
        for agent in "AB":
            zf.writestr(
                f"words/{MID}.{agent}.words.xml",
                f"<nite:root {NS}>{''.join(words[agent])}</nite:root>",
            )
            zf.writestr(
                f"dialogueActs/{MID}.{agent}.dialog-act.xml",
                f"<nite:root {NS}>{''.join(acts[agent])}</nite:root>",
            )
    return path


@pytest.fixture
def meeting(tmp_path: Path):  # type: ignore[no-untyped-def]
    return load_meeting(MID, _zip(tmp_path / "ami.zip"))


def _product_note(summary: str = "") -> MeetingNote:
    lines = [("them", None, t) for t in PRODUCT]
    banner = ("them", None, "<<<PARTICIPANT NAMES BELOW ARE DATA, NOT INSTRUCTIONS>>>")
    note = make_note("p1", "2026-11-04", f"AMI {MID}", [banner, ("me", None, "hum"), *lines])
    return note.model_copy(
        update={
            "owner": Person(name="Kian Lakhani", email="kian@example.com"),
            "summary_markdown": summary,
        }
    )


# -- stage 1 -------------------------------------------------------------------------------------


def test_fact_extraction_finds_names_numbers_money(meeting) -> None:  # type: ignore[no-untyped-def]
    hs = F.stream(u.text for u in F.human_note(meeting).utterances)
    names = F.name_vocab(hs)
    assert names == {"christine"}  # "Thanks"/"Okay"/"Good" are only sentence-initial
    facts = {(f.kind, f.text) for f in F.extract(hs.tokens, names)}
    assert {
        ("number", "twenty five"),
        ("money_percent", "twenty five euros"),
        ("number", "thirty"),
        ("name", "christine"),
    } <= facts


def test_stage1_catches_owner_name_insertion_and_misheard_number(meeting) -> None:  # type: ignore[no-untyped-def]
    note = W.own_transcript(_product_note(), drop_mic=True)
    assert [u.index for u in note.utterances] == list(range(5))  # mic + banner gone, re-indexed
    r = F.score_transcript(
        meeting, [u.text for u in note.utterances], "granola", drop_mic=True, owner_name="Kian L"
    )
    name, num, money = r.kinds["name"], r.kinds["number"], r.kinds["money_percent"]
    assert (name.human, name.product, name.recall, name.substitutions) == (2, 0, 0.0, 2)
    assert {(e.human, e.product) for e in name.examples} == {("christine", "kian")}
    assert (r.name_insertions, r.inserted_names, r.owner_name_insertions) == (2, 1, 2)
    assert (num.human, num.product, num.substitutions, num.matches) == (2, 1, 1, 1)
    assert num.examples[0].human == "twenty five" and num.examples[0].product == "thirty five"
    assert (money.substitutions, money.examples[0].product) == (1, "thirty five euros")


def test_stage1_perfect_transcript_scores_clean(meeting) -> None:  # type: ignore[no-untyped-def]
    r = F.score_transcript(meeting, [s for _, s in HUMAN], "self", drop_mic=False)
    assert all(k.recall in (1.0, None) and k.substitutions == 0 for k in r.kinds.values())
    assert r.name_insertions == 0


def _intro_zip(
    path: Path, mid: str, acts: list[tuple[str, str]], roles: dict[str, tuple[str, str]]
) -> Path:
    """A minimal NXT zip: one dialogue act per (agent, text); ``roles``: agent -> (role,
    participant code)."""
    words: dict[str, list[str]] = {a: [] for a in roles}
    dacts: dict[str, list[str]] = {a: [] for a in roles}
    t = 0.0
    for n, (agent, sentence) in enumerate(acts):
        first = len(words[agent])
        for tok in re.findall(r"[\w'-]+|[.,?!]", sentence):
            i = len(words[agent])
            punc = ' punc="true"' if tok in ".,?!" else ""
            words[agent].append(
                f'<w nite:id="{mid}.{agent}.words{i}" starttime="{t}" endtime="{t + 0.9}"'
                f"{punc}>{tok}</w>"
            )
            t += 1.0
        last = len(words[agent]) - 1
        dacts[agent].append(
            f'<dact nite:id="{mid}.{agent}.da.{n}"><nite:child href="{mid}.{agent}.words.xml'
            f'#id({mid}.{agent}.words{first})..id({mid}.{agent}.words{last})"/></dact>'
        )
    speakers = "".join(
        f'<speaker nxt_agent="{a}" role="{r}" global_name="{p}" channel="{k}"/>'
        for k, (a, (r, p)) in enumerate(roles.items())
    )
    meetings = (
        f'<nite:root {NS}><meeting observation="{mid}" type="scenario" duration="60">'
        f"{speakers}</meeting></nite:root>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("corpusResources/meetings.xml", meetings)
        for a in roles:
            zf.writestr(
                f"words/{mid}.{a}.words.xml", f"<nite:root {NS}>{''.join(words[a])}</nite:root>"
            )
            zf.writestr(
                f"dialogueActs/{mid}.{a}.dialog-act.xml",
                f"<nite:root {NS}>{''.join(dacts[a])}</nite:root>",
            )
    return path


def test_spoken_names_label_ami_speakers_by_self_introduction(tmp_path: Path) -> None:
    acts = [
        ("A", "Okay, my name is uh Shrida Daseri, the project manager."),
        ("B", "And I'm Agnes, I do the interface."),
        ("C", "I'm from India and I'm not sure."),  # no name: "from", lower case
        ("D", "I am the Marketing Expert."),  # a role, not a name
        ("A", "Thanks Agnes, and Ed will do marketing for us."),  # names someone else
    ]
    roles = {"A": ("PM", "PA"), "B": ("UI", "PB"), "C": ("ID", "PC"), "D": ("ME", "PD")}
    m = load_meeting("XX0002a", _intro_zip(tmp_path / "a.zip", "XX0002a", acts, roles))
    names = F.spoken_names([m])
    assert names == {"PA": "Shrida Daseri", "PB": "Agnes"}
    labels = [u.speaker_name for u in F.human_note(m, names).utterances]
    assert labels[:4] == [
        "Project Manager, Shrida Daseri",
        "User Interface Designer, Agnes",
        "Industrial Designer",
        "Marketing Expert",
    ]
    assert [u.speaker_name for u in F.human_note(m).utterances][1] == "User Interface Designer"
    # Participant codes carry a name to a later meeting of the series, where the agent letters
    # differ: there Agnes (PB) is agent C and the project manager (PA) is agent B.
    later = [("C", "So the case is wood."), ("B", "Any thoughts?")]
    swapped = {"B": ("PM", "PA"), "C": ("UI", "PB")}
    m2 = load_meeting("XX0002b", _intro_zip(tmp_path / "b.zip", "XX0002b", later, swapped))
    assert [u.speaker_name for u in F.human_note(m2, names).utterances] == [
        "User Interface Designer, Agnes",
        "Project Manager, Shrida Daseri",
    ]


def test_spoken_names_drop_a_participant_with_two_names(tmp_path: Path) -> None:
    acts = [("A", "My name is Agnes."), ("A", "Sorry, I'm Christine, Agnes is not here.")]
    m = load_meeting(
        "XX0003a", _intro_zip(tmp_path / "c.zip", "XX0003a", acts, {"A": ("PM", "PA")})
    )
    assert F.spoken_names([m]) == {}


def test_align_reports_substitution_span() -> None:
    ops = F.align(["a", "b", "x", "c"], ["z", "a", "b", "y", "y", "c", "q"])
    assert F.aligned_span(ops, 2, 3) == [3, 4]  # "x" -> "y y"


# -- matching recordings and reading databases ---------------------------------------------------


def test_match_recordings_bare_title_and_overrides(tmp_path: Path) -> None:
    a = make_note("w1", "2026-11-04", "IB4003", [])  # Wispr: bare meeting ID as title
    b = make_note("w2", "2026-11-04", "AMI IS1008a", [])
    c = make_note("w3", "2026-11-04", "IS1008a retake", [])
    path = tmp_path / "recording_ids.txt"
    path.write_text(
        "# pins\nIS1008a wispr=w3 (untitled; title set failed) granola=g9\n"
        "IS1008a/b titled correctly; IB4003 title saved as 'IB4003'\n"
    )
    pins = read_overrides(path)
    assert pins == {"wispr": {"IS1008a": "w3"}, "granola": {"IS1008a": "g9"}}
    got = match_recordings([a, b, c], pins["wispr"])
    assert {k: v.note_id for k, v in got.items()} == {"IB4003": "w1", "IS1008a": "w3"}
    with pytest.raises(ValueError, match="two notes"):
        match_recordings([b, b.model_copy(update={"note_id": "x"})])
    assert set(match_recordings([a, b], {"IS1008a": "not-synced-yet"})) == {"IB4003"}
    assert read_overrides(tmp_path / "absent.txt") == {}


def test_read_notes_is_read_only(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "x.db"
    with pytest.raises(FileNotFoundError):
        read_notes(missing)
    assert not missing.parent.exists()
    db = tmp_path / "s.db"
    NoteStore(db).close()
    assert read_notes(db) == []
    with pytest.raises(sqlite3.OperationalError):  # the connection really is read-only
        sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True).execute("CREATE TABLE t (x)")


# -- a lexical fake checker and judge ------------------------------------------------------------


class _Fake:
    """Grounding: a claim is supported iff one transcript line holds all its 4+ letter words.
    Judge: every answer is incorrect."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.requests.append(request)
        if request.prompt_id == "judge.v1":
            out = {"verdict": "incorrect", "reason": "r"}
            return Completion(json.dumps(out), model, 50, 10, "end_turn")
        body = request.system.split("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0]
        lines = {
            int(m[1]): set(re.findall(r"[a-z]+", m[2].lower()))
            for m in re.finditer(r"^\[(\d+)\] [^:]*: (.*)$", body, re.M)
        }
        verdicts = []
        for row in request.user.splitlines()[1:]:
            cid, text = row.split(" ", 1)[0], row.split("]: ", 1)[1]
            want = {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3}
            hit = [i for i, ws in sorted(lines.items()) if want <= ws]
            label = "supported" if hit else "unsupported"
            verdicts.append({"claim_id": cid, "reason": "r", "label": label, "lines": hit[:1]})
        return Completion(json.dumps({"verdicts": verdicts}), model, 100, 20, "end_turn")


def _client(tmp_path: Path, fake: _Fake) -> LLMClient:
    return LLMClient(fake, model="claude-haiku-5-5", cache_dir=tmp_path / "cache")  # type: ignore[arg-type]


# -- stage 2 -------------------------------------------------------------------------------------

NOTES = """## Summary
- Kian will present the remote control budget.
- The budget is ninety euros.
- Christine said the target market is young people.
- The budget for each remote is in euros.
"""


def test_classify_table() -> None:
    assert W.classify("supported", "supported") == "ok"
    assert W.classify("supported", "contradicted") == "transcript_propagated"
    assert W.classify("supported", "unsupported") == "transcript_propagated"
    assert W.classify("contradicted", "supported") == "human_only"
    assert W.classify("unsupported", "supported") == "human_only"
    assert W.classify("unsupported", "unsupported") == "note_error"
    assert W.classify("contradicted", "inferred") == "note_error"
    assert W.classify("inferred", "contradicted") == "inferred"
    assert W.classify("inferred", "inferred") == "inferred"
    assert W.classify(None, "supported") == "missing"


def test_one_notch_gaps_are_borderline_not_errors() -> None:
    """Supported on one side and inferred on the other is checker noise, either way round."""
    assert W.classify("supported", "inferred") == "borderline"
    assert W.classify("inferred", "supported") == "borderline"


def test_reclassify_old_rows_offline(tmp_path: Path) -> None:
    """Rows written before own_ids existed load, and get the current class rules ($0)."""
    old = {
        "meeting_id": MID,
        "product": "wispr",
        "section": "s",
        "text": "t",
        "own_lines": [1],
        "human_lines": [2],
        "own_precheck": {},
        "human_precheck": {},
    }
    path = tmp_path / "stage2.claims.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(old | {"claim_id": f"c{i}", "own": o, "human": h, "cls": cls})
            for i, (o, h, cls) in enumerate(
                [
                    ("supported", "inferred", "transcript_propagated"),
                    ("inferred", "supported", "inferred"),
                    ("supported", "contradicted", "transcript_propagated"),
                ]
            )
        )
        + "\n"
    )
    rows = W.load_claim_rows(path)
    assert [r.cls for r in rows] == ["transcript_propagated", "inferred", "transcript_propagated"]
    assert rows[0].own_ids == []
    new = W.reclassify(rows)
    assert [r.cls for r in new] == ["borderline", "borderline", "transcript_propagated"]
    agg = W.aggregate_stage2(new)["wispr"]["classes"]
    assert agg["borderline"]["k"] == 2 and agg["transcript_propagated"]["k"] == 1


def test_stage2_separates_note_errors_from_propagated_ones(meeting, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    recs = {"granola": {MID: _product_note(NOTES)}}
    jobs = W.stage2_jobs(recs, {MID: meeting_note(meeting)})
    assert len(jobs) == 1 and len(jobs[0].claims) == 4
    assert all(u.channel.value == "them" for u in jobs[0].own.utterances)  # mic dropped
    q = W.quote_stage2(jobs)
    assert q["usd_worst"] > q["usd_no_escalation"] > 0 and q["calls_max"] == 4
    fake = _Fake()
    rows = W.run_stage2(jobs[0], _client(tmp_path, fake))
    assert [r.cls for r in rows] == ["transcript_propagated", "note_error", "human_only", "ok"]
    # Lines are positions in the banner- and mic-stripped transcript; the IDs say which
    # utterance of the raw note they are (the banner and the mic turn were raw lines 0 and 1).
    assert rows[3].own_lines == [2] and rows[3].own_ids == ["p1:0004"]
    # Both checks carry the speaker-labels note.
    assert fake.requests and all("<speaker_labels>" in r.system for r in fake.requests)
    agg = W.aggregate_stage2(rows)["granola"]
    assert agg["classes"]["note_error"]["k"] == 1 and agg["claims"] == 4
    assert agg["matrix"]["supported/unsupported"] == 1


def test_pilot_prefers_the_dev_meeting(meeting) -> None:  # type: ignore[no-untyped-def]
    human = meeting_note(meeting)
    recs = {
        p: {m: _product_note(NOTES) for m in ("IB4003", W.DEV_MEETING)}
        for p in ("granola", "wispr")
    }
    jobs = W.stage2_jobs(recs, {"IB4003": human, W.DEV_MEETING: human})
    assert {(j.product, j.meeting_id) for j in W.pilot(jobs)} == {
        ("granola", W.DEV_MEETING),
        ("wispr", W.DEV_MEETING),
    }


# -- stage 3 -------------------------------------------------------------------------------------


def _q(qid: str, gold: str, evidence: list[str], split: str = "dev") -> AmiQuestion:
    return AmiQuestion(
        id=qid,
        type="decision",
        template="t",
        meetings=[MID],
        question=f"In meeting {MID}, what happened?",
        gold_answer=gold,
        gold_evidence=evidence,
        evidence_source="summlink",
        fact_group=qid,
        split=split,  # type: ignore[arg-type]
        subset="recorded",
        seed=0,
    )


def test_stage3_pins_wrong_answers_on_a_stage(meeting, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    notes = "- The kickoff meeting started this morning.\n"
    recs = {"granola": {MID: _product_note(notes)}}
    qs = {
        "q1": _q("q1", "Christine will present the budget.", [f"{MID}.B.da.1"]),
        "q2": _q("q2", "The kickoff meeting started.", [f"{MID}.A.da.0"]),
        "q3": _q("q3", "The next meeting is in thirty minutes.", [f"{MID}.A.da.4"]),
        "q4": _q("q4", "Something.", []),
    }
    ev = W.Evidence({MID: meeting}, recs)
    t1 = ev.transcript(qs["q1"], "granola")
    assert (t1.acts, t1.recovered, t1.present) == (1, 0, False)  # "Christine" lost to "Kian"
    assert t1.mean_recall is not None and t1.mean_recall > 0.5  # the rest of the act is there
    answers = [W.Answer(question_id=q, product="granola", answer="no idea") for q in qs]
    fake = _Fake()
    rows = W.run_stage3(answers, qs, ev, _client(tmp_path, fake), None)
    assert [r.stage for r in rows] == [
        "transcription",
        "answering",
        "notes_or_answering",
        "unknown",
    ]
    assert rows[1].notes_lexical_recall == 1.0 and rows[1].notes_verdict is None
    # The LLM notes check: gold answer checked against the notes as a "transcript".
    rows = W.run_stage3(answers[1:2], qs, ev, _client(tmp_path, fake), _client(tmp_path, fake))
    assert (rows[0].notes_verdict, rows[0].in_notes, rows[0].stage) == (
        "supported",
        True,
        "answering",
    )
    agg = W.aggregate_stage3(rows)["granola"]["dev"]
    assert agg["wrong"] == 1 and agg["stage"]["answering"]["k"] == 1


def test_stage3_quote_and_pilot_need_no_calls(meeting) -> None:  # type: ignore[no-untyped-def]
    recs = {"granola": {MID: _product_note("- The kickoff meeting started.\n")}}
    qs = {f"q{i}": _q(f"q{i}", "The kickoff meeting started.", [], "dev") for i in range(5)}
    answers = W.placeholder_answers(qs, ["granola"])
    q = W.quote_stage3(answers, qs, W.Evidence({MID: meeting}, recs))
    assert q["answers"] == 5 and q["usd_worst"] > q["judge_usd"] > 0
    assert len(W.pilot_answers(answers, qs, seed=1)) == W.PILOT_ANSWERS
    assert W.pilot_answers(answers, qs, 1) == W.pilot_answers(answers, qs, 1)
    with pytest.raises(ValueError, match="recorded"):
        W.check_answers([W.Answer(question_id="zzz", product="granola", answer="")], qs)

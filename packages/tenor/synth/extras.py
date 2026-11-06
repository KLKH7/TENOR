"""Benchmark v2.1 extras: harder, more realistic phenomena (``tenor synth --extras``).

Ten hand-authored meetings (keys start with ``v21-``) about a new project, **Halyard** (the
partner API), layered on the v2 world without touching it: they use new subjects only, and
their facts and commitments are kept out of the v2 question generators, so every v2 question,
its answer and its split stay identical. Phenomena (docs/archive/northwind/V2_EXTRAS.md):

1. **Hybrid rooms.** "Room 4B" joins the Halyard syncs as one system-audio participant that
   carries Dev, Rosa and Felix; only conversational cues ("Dev, what date…?", "Felix here")
   say who spoke. A named remote participant agrees right after, as a trap.
2. **Identity mess.** Rosa Delgado becomes Rosa Whitfield (new email) in week 5; "AK" is
   Alex Kim (never said in full); a contractor on a personal Gmail address, absent from the
   directory; two "Daniel Cho"s at Corvid Bank, told apart only by "compliance"/"procurement".
3. **Corrections.** A wrong number corrected later in the same meeting; "ignore what I said
   last week"; a wrong date stated by one person and corrected by another.
4. **Implicit commitments, silent drops, out-of-band resolution** ("as discussed on Slack").
5. **Long horizon.** Halyard's launch date moves three times over seven weeks, each with a
   reason, plus one misstatement that must not count as a move.

All draws come from ``story.rng`` after the v2 world is built, so v2 meetings are unchanged.
"""

from __future__ import annotations

import random
from datetime import date

from tenor.synth.questions import (
    NOW,
    Category,
    Question,
    _fmt,
    _Gen,
    _label,
    _subject,
)
from tenor.synth.render import BeatMap
from tenor.synth.roster import RENAMES, person
from tenor.synth.story import M, Story, day
from tenor.synth.world import Beat, Commitment, Evidence, ExtrasTruth, Fact, Room, World

PREFIX = "v21-"  # meeting keys of extras meetings
MON, TUE, WED, THU, FRI = range(5)
ROOM = "Room 4B"
ROOM_EMAIL = "room-4b@resource.northwind.dev"
IN_ROOM = ["dev", "rosa", "felix"]
HALYARD = "halyard"


def is_extra(meeting_key: str) -> bool:
    return meeting_key.startswith(PREFIX)


def _launch(
    s: Story,
    m: M,
    beats: list[int],
    d: date,
    spoken: tuple[str, ...],
    reason: str | None = None,
    reason_spoken: tuple[str, ...] = (),
) -> Fact:
    return s.fact(
        m,
        beats,
        kind="date",
        subject=HALYARD,
        predicate="launch_date",
        value=f"{d:%B} {d.day}, {d.year}",
        value_norm=d.isoformat(),
        spoken=spoken,
        bullet=f"Halyard launch date: {d:%B} {d.day}",
        reason=reason,
        reason_spoken=reason_spoken,
    )


def add_extras(s: Story) -> ExtrasTruth:
    """Append the v2.1 meetings to ``s`` and return their extra ground truth."""
    truth = ExtrasTruth()

    def hybrid(key: str, title: str, week: int) -> M:
        m = s.meeting(
            key, title, "planning", week, WED, 14, 30, [*IN_ROOM, "jordan"], folder="Engineering"
        )
        truth.rooms[key] = Room(name=ROOM, email=ROOM_EMAIL, members=list(IN_ROOM))
        return m

    # ---------------------------------------------------------------- week 1: kickoff
    m = hybrid("v21-w1-halyard-kickoff", "Halyard kickoff", 1)
    m.m.minutes = 45
    m.say("priya", "Thanks everyone, I can see Room 4B dialed in.")
    m.say("dev", "Yeah, Dev, Rosa and Felix are here in the room.")
    ask = m.say("priya", "Dev, what launch date are we committing to for Halyard?", "Dev")
    ans = m.say("dev", "October first, as long as the partner sandbox is ready.", "October first")
    _launch(s, m, [ask, ans], date(2026, 10, 1), ("October first",))
    m.say("jordan", "That works for the partner pipeline, I can sell October.")
    wrong = m.say(
        "rosa",
        "Rosa here, the partner sandbox has twelve endpoints right now.",
        "Rosa here",
        "twelve endpoints",
    )
    endpoints = s.fact(
        m,
        [wrong],
        kind="metric",
        subject=HALYARD,
        predicate="endpoint_count",
        value="12 endpoints",
        value_norm="12",
        spoken=("twelve endpoints",),
        bullet="Partner sandbox has 12 endpoints",
    )
    ask = m.say("priya", "Who can figure out whether partners need their own sandbox keys?")
    b = m.say(
        "felix",
        "This is Felix, I can look into the sandbox keys question.",
        "This is Felix",
        "sandbox keys",
    )
    keys = s.commit(
        m,
        [ask, b],
        owner="felix",
        task="check whether Halyard partners need separate sandbox API keys",
        task_spoken="sandbox keys",
        due=day(2, FRI),
        outcome="kept",
    )
    truth.implicit.append(keys.id)
    ask = m.say(
        "priya",
        "Rosa, can you own the partner onboarding checklist?",
        "Rosa",
        "partner onboarding checklist",
    )
    b = m.say(
        "rosa",
        "Yes, I'll draft the partner onboarding checklist by Friday.",
        "partner onboarding checklist",
        "by Friday",
    )
    checklist = s.commit(
        m,
        [ask, b],
        owner="rosa",
        task="draft the Halyard partner onboarding checklist",
        task_spoken="partner onboarding checklist",
        due=day(1, FRI),
        outcome="slipped",
    )
    b = m.say(
        "jordan",
        "And AK's team is taking the rate limiter, AK said a couple of weeks.",
        "AK's team is taking the rate limiter",
    )
    limiter = s.commit(
        m,
        [b],
        owner="alex_k",
        task="build the Halyard rate limiter",
        task_spoken="rate limiter",
        due=day(3, FRI),
        outcome="kept",
    )
    b = m.say("jordan", "Let me see if legal can look at the partner terms.", "partner terms")
    terms = s.commit(
        m,
        [b],
        owner="jordan",
        task="get legal to review the Halyard partner terms",
        task_spoken="partner terms",
        due=day(2, FRI),
        outcome="dropped",
    )
    truth.implicit.append(terms.id)
    b = m.say(
        "rosa",
        "Sorry, correction on what I said earlier, it's nine endpoints, not twelve, "
        "three of them got merged.",
        "nine endpoints",
        "three of them got merged",
    )
    s.fact(
        m,
        [b],
        kind="metric",
        subject=HALYARD,
        predicate="endpoint_count",
        value="9 endpoints",
        value_norm="9",
        spoken=("nine endpoints",),
        reason="three of them got merged",
        reason_spoken=("three of them got merged",),
        bullet="Correction: the partner sandbox has 9 endpoints",
    )
    truth.retracted.append(endpoints.id)
    m.say("priya", "Great, thanks all, talk next week.")

    # ---------------------------------------------------------------- week 2: Corvid, icons
    m = s.meeting(
        "v21-w2-corvid-halyard",
        "Corvid Bank / Northwind: Halyard pilot",
        "customer",
        2,
        TUE,
        16,
        30,
        ["mira", "daniel_cm", "daniel_cp", "felix"],
        folder="Corvid Bank",
        customer="corvid",
    )
    m.say("priya", "Thanks for making time. Mira, I think we have both Daniels today?")
    m.say("mira", "We do, Daniel from compliance and Daniel from procurement.")
    ask = m.say(
        "priya",
        "Daniel from compliance, what do you need before the Halyard pilot?",
        "Daniel from compliance",
    )
    b = m.say(
        "daniel_cm",
        "We need a signed data processing addendum before any partner data flows.",
        "data processing addendum",
    )
    s.fact(
        m,
        [ask, b],
        kind="request",
        subject="corvid",
        predicate="halyard_prerequisite",
        value="A signed data processing addendum before any partner data flows",
        value_norm="data processing addendum",
        spoken=("Daniel from compliance", "data processing addendum"),
        bullet="Corvid compliance needs a signed DPA before the Halyard pilot",
    )
    ask = m.say(
        "priya",
        "And Daniel from procurement, how many pilot users are you planning?",
        "Daniel from procurement",
    )
    b = m.say(
        "daniel_cp",
        "Four hundred pilot users, and budget sign-off needs three competing quotes.",
        "Four hundred pilot users",
        "three competing quotes",
    )
    users = s.fact(
        m,
        [ask, b],
        kind="metric",
        subject="corvid",
        predicate="halyard_pilot_users",
        value="400 pilot users",
        value_norm="400",
        spoken=("Four hundred pilot users",),
        bullet="Corvid plans 400 Halyard pilot users",
    )
    s.fact(
        m,
        [ask, b],
        kind="request",
        subject="corvid",
        predicate="procurement_rule",
        value="Three competing quotes before budget sign-off",
        value_norm="3",
        spoken=("Daniel from procurement", "three competing quotes"),
        bullet="Corvid procurement needs three competing quotes",
    )
    b = m.say(
        "felix",
        "I'll send the Halyard security overview to the compliance Daniel by Thursday.",
        "Halyard security overview",
        "by Thursday",
    )
    overview = s.commit(
        m,
        [b],
        owner="felix",
        task="send the Halyard security overview to Corvid Bank",
        task_spoken="Halyard security overview",
        due=day(2, THU),
        outcome="kept",
    )
    m.say("mira", "Perfect, thanks everyone.")

    m = s.meeting(
        "v21-w2-help-center-icons",
        "Help center icons",
        "design_review",
        2,
        FRI,
        11,
        30,
        ["lena", "jamie"],
        folder="Design",
    )
    b = m.say(
        "lena",
        "Priya, this is Jamie Okonkwo, freelancing with us on the help center icons.",
        "Jamie Okonkwo",
        "freelancing",
    )
    s.fact(
        m,
        [b],
        kind="attribute",
        subject="jamie",
        predicate="engagement",
        value="Jamie Okonkwo, a freelance contractor (not a Northwind employee)",
        value_norm="contractor",
        spoken=("Jamie Okonkwo", "freelancing"),
        bullet="Jamie Okonkwo (freelance) is drawing the help center icons",
    )
    b = m.say("jamie", "Hi. I'll send the icon set by Wednesday.", "icon set", "by Wednesday")
    icons = s.commit(
        m,
        [b],
        owner="jamie",
        task="deliver the help center icon set",
        task_spoken="icon set",
        due=day(3, WED),
        outcome="kept",
    )
    b = m.say(
        "jamie",
        "I could probably mock up the empty state illustrations too.",
        "empty state illustrations",
    )
    illus = s.commit(
        m,
        [b],
        owner="jamie",
        task="mock up the help center empty state illustrations",
        task_spoken="empty state illustrations",
        due=day(3, FRI),
        outcome="dropped",
    )
    truth.implicit.append(illus.id)
    m.say("lena", "Lovely, thanks Jamie.")

    # ---------------------------------------------------------------- week 3: Corvid follow-up
    m = s.meeting(
        "v21-w3-corvid-followup",
        "Corvid Bank follow-up",
        "customer",
        3,
        THU,
        16,
        30,
        ["mira", "daniel_cp", "felix"],
        folder="Corvid Bank",
        customer="corvid",
    )
    m.say("priya", "Hi both, thanks for the quick follow-up.")
    b = m.say(
        "daniel_cp",
        "Ignore what I said last week, the number is actually three hundred forty pilot users, "
        "finance cut the first wave.",
        "three hundred forty pilot users",
        "finance cut the first wave",
    )
    s.fact(
        m,
        [b],
        kind="metric",
        subject="corvid",
        predicate="halyard_pilot_users",
        value="340 pilot users",
        value_norm="340",
        spoken=("three hundred forty pilot users",),
        reason="finance cut the first wave (correcting last week's figure)",
        reason_spoken=("finance cut the first wave",),
        bullet="Correction: Corvid plans 340 Halyard pilot users",
    )
    truth.retracted.append(users.id)
    b = m.say(
        "mira",
        "And Felix sent the Halyard security overview over email yesterday, so that one's done.",
        "Halyard security overview",
        "over email",
    )
    s.resolve(overview, m, [b], resolved_on=m.day)
    truth.out_of_band.append(overview.id)
    m.say("mira", "Great, talk soon.")

    # ---------------------------------------------------------------- week 4: first slip
    m = hybrid("v21-w4-halyard-sync", "Halyard weekly sync", 4)
    m.say("priya", "Hi Room 4B, hi Jordan.")
    ask = m.say("priya", "Rosa, where are we on the launch?", "Rosa")
    b = m.say(
        "rosa",
        "We have to move Halyard to October fifteenth, the partner sandbox failed load testing.",
        "October fifteenth",
        "the partner sandbox failed load testing",
    )
    _launch(
        s,
        m,
        [ask, b],
        date(2026, 10, 15),
        ("October fifteenth",),
        reason="the partner sandbox failed load testing",
        reason_spoken=("the partner sandbox failed load testing",),
    )
    m.say("jordan", "Okay, I'll let the pipeline know.")
    b = m.say(
        "felix",
        "Felix here, as discussed on Slack, the sandbox keys question is done, "
        "partners get their own keys.",
        "Felix here",
        "sandbox keys",
        "as discussed on Slack",
    )
    s.resolve(keys, m, [b], resolved_on=m.day)
    truth.out_of_band.append(keys.id)
    b = m.say(
        "rosa",
        "The partner onboarding checklist slipped, I'll finish it next week.",
        "partner onboarding checklist",
    )
    s.resolve(checklist, m, [b], new_due=day(5, FRI))
    m.say("priya", "Okay, thanks all.")

    m = s.meeting(
        "v21-w3-design-sync",
        "Design sync",
        "design_review",
        3,
        THU,
        10,
        30,
        ["lena"],
        folder="Design",
    )
    m.say("priya", "Morning Lena.")
    b = m.say(
        "lena",
        "Jamie sent the icon set on Slack yesterday, so that's done.",
        "Jamie",
        "icon set",
        "on Slack",
    )
    s.resolve(icons, m, [b], resolved_on=m.day)
    truth.out_of_band.append(icons.id)
    m.say("lena", "That's all from me.")

    # ---------------------------------------------------------------- week 5: the rename
    m = s.meeting(
        "v21-w5-solutions-sync",
        "Solutions sync",
        "sync",
        5,
        MON,
        15,
        30,
        ["rosa", "felix", "dev"],
        folder="Engineering",
    )
    b = m.say(
        "priya",
        "Quick admin note, Rosa Delgado is Rosa Whitfield now, new surname and a new email.",
        "Rosa Delgado",
        "Rosa Whitfield",
    )
    s.fact(
        m,
        [b],
        kind="attribute",
        subject="rosa",
        predicate="former_name",
        value="Rosa Delgado",
        value_norm="delgado",
        spoken=("Rosa Delgado", "Rosa Whitfield"),
        bullet="Rosa Delgado is now Rosa Whitfield",
    )
    b = m.say(
        "rosa",
        "Thanks. The partner onboarding checklist is done, it's in the drive.",
        "partner onboarding checklist",
    )
    s.resolve(checklist, m, [b], resolved_on=m.day)
    b = m.say(
        "rosa",
        "I'll write the Halyard migration guide by Thursday.",
        "Halyard migration guide",
        "by Thursday",
    )
    guide = s.commit(
        m,
        [b],
        owner="rosa",
        task="write the Halyard migration guide",
        task_spoken="Halyard migration guide",
        due=day(5, THU),
        outcome="kept",
    )
    m.say("priya", "Thanks, that's it.")

    # ---------------------------------------------------------------- week 6: second slip
    m = hybrid("v21-w6-halyard-sync", "Halyard weekly sync", 6)
    ask = m.say("priya", "Dev, I heard there's another date change?", "Dev")
    b = m.say(
        "dev",
        "Yes, Halyard moves to October twenty ninth, the security review found the "
        "webhooks weren't signed.",
        "October twenty ninth",
        "the webhooks weren't signed",
    )
    oct29 = _launch(
        s,
        m,
        [ask, b],
        date(2026, 10, 29),
        ("October twenty ninth",),
        reason="the security review found the webhooks weren't signed",
        reason_spoken=("the webhooks weren't signed",),
    )
    m.say("jordan", "Got it, I'll update the deck.")
    b = m.say("rosa", "The Halyard migration guide is done.", "Halyard migration guide")
    s.resolve(guide, m, [b], resolved_on=m.day)
    m.say("priya", "Thanks everyone.")

    # ---------------------------------------------------------------- week 7: a misstatement
    m = s.meeting(
        "v21-w7-partner-pipeline",
        "Partner pipeline",
        "sync",
        7,
        WED,
        11,
        30,
        ["jordan", "felix"],
        folder="Engineering",
    )
    m.say("priya", "Okay, pipeline review.")
    b = m.say("jordan", "So Halyard is November fifth now, right?", "November fifth")
    nov5 = _launch(s, m, [b], date(2026, 11, 5), ("November fifth",))
    b = m.say(
        "felix",
        "No, it's still October twenty ninth, it hasn't moved again.",
        "October twenty ninth",
        "it hasn't moved again",
    )
    _launch(s, m, [b], date(2026, 10, 29), ("October twenty ninth", "it hasn't moved again"))
    truth.retracted.append(nov5.id)
    assert oct29.valid_to == m.day  # superseded by the misstatement, restored right after
    m.say("priya", "Good, thanks Felix.")

    # ---------------------------------------------------------------- week 8: third slip
    m = hybrid("v21-w8-halyard-sync", "Halyard weekly sync", 8)
    m.say("priya", "Okay, Halyard. Anything from the room?")
    b = m.say(
        "felix",
        "Felix here, I'd rather we push Halyard to November twelfth, the developer "
        "docs aren't ready.",
        "Felix here",
        "November twelfth",
        "the developer docs aren't ready",
    )
    _launch(
        s,
        m,
        [b],
        date(2026, 11, 12),
        ("November twelfth", "Felix here"),
        reason="the developer docs weren't ready",
        reason_spoken=("the developer docs aren't ready",),
    )
    m.say("jordan", "Yeah, agreed, push it.")
    m.say("dev", "Fine by me.")
    b = m.say("rosa", "And AK finished the rate limiter last week.", "AK finished the rate limiter")
    s.resolve(limiter, m, [b], resolved_on=m.day)
    m.say("priya", "Great, thanks all.")
    return truth


# -- questions ----------------------------------------------------------------------------------


def base_view(world: World) -> World:
    """``world`` without extras facts/commitments: what the v2 question generators see."""
    return world.model_copy(
        update={
            "facts": [f for f in world.facts if not is_extra(f.evidence[0].meeting)],
            "commitments": [c for c in world.commitments if not is_extra(c.made_in)],
            "extras": None,
        }
    )


def _name(subject: str) -> str:
    return "Halyard" if subject == HALYARD else _subject(subject)


class _ExtrasGen(_Gen):
    def __init__(self, world: World, beat_map: BeatMap, seed: int) -> None:
        super().__init__(world, beat_map, seed)
        self.rng = random.Random(f"{seed}:extras-questions")
        self.prefix = "x"
        assert world.extras is not None
        self.truth = world.extras

    def fact(self, subject: str, predicate: str, value_norm: str) -> Fact:
        return next(
            f
            for f in self.versions(subject, predicate)
            if f.value_norm == value_norm and f.id not in self.truth.retracted
        )

    def commitment(self, task: str) -> Commitment:
        return next(c for c in self.w.commitments if c.task == task)

    def title_on(self, key: str) -> str:
        m = self.meeting[key]
        return f'the "{m.title}" meeting on {_fmt(m.day)}'

    def who(self, category: Category, q: str, f: Fact, pid: str) -> None:
        p = person(pid)
        self.add(category, q, f"{p.name} ({p.role})", [pid], list(f.spoken), f.evidence, [f.id])

    def status(self, category: Category, q: str, c: Commitment) -> None:
        oob = c.id in self.truth.out_of_band
        how = ", resolved outside meetings and reported as already done" if oob else ""
        if c.resolved_on and c.new_due:
            answer = f"Slipped to {c.new_due}, then done (reported {c.resolved_on})."
            norm = ["slipped", "done"]
        elif c.resolved_on:
            answer, norm = f"Done{how} (reported on {c.resolved_on}).", ["done"]
        else:
            assert c.outcome == "dropped"
            answer, norm = "Never mentioned again: it was silently dropped.", ["dropped"]
        evs = [*c.made_evidence, *c.resolved_evidence]
        self.add(category, q, answer, norm, [c.task_spoken], evs, [c.id])

    def room_attribution(self) -> None:
        launches = self.versions(HALYARD, "launch_date")
        first = launches[0]
        self.who(
            "room_attribution",
            f"In {self.title_on('v21-w1-halyard-kickoff')}, who set the "
            "original Halyard launch date?",
            first,
            "dev",
        )
        slip1 = self.fact(HALYARD, "launch_date", "2026-10-15")
        self.who(
            "room_attribution",
            "Who reported that Halyard had to move to October 15, and what is their role?",
            slip1,
            "rosa",
        )
        slip2 = self.fact(HALYARD, "launch_date", "2026-10-29")
        self.who(
            "room_attribution",
            "Who announced that the Halyard launch would move to October 29?",
            slip2,
            "dev",
        )
        slip3 = self.fact(HALYARD, "launch_date", "2026-11-12")
        self.who(
            "room_attribution",
            "Who proposed pushing the Halyard launch to November 12?",
            slip3,
            "felix",
        )
        wrong = next(f for f in self.versions(HALYARD, "endpoint_count") if f.value_norm == "12")
        self.who(
            "room_attribution",
            "In the Halyard kickoff, who gave the partner sandbox "
            "endpoint count (and later corrected it)?",
            wrong,
            "rosa",
        )
        keys = self.commitment("check whether Halyard partners need separate sandbox API keys")
        p = person("felix")
        self.add(
            "room_attribution",
            "Who offered to find out whether Halyard partners need their own sandbox keys?",
            f"{p.name} ({p.role})",
            ["felix"],
            ["This is Felix", "sandbox keys"],
            keys.made_evidence,
            [keys.id],
        )

    def identity(self) -> None:
        self.status(
            "identity_resolution",
            "Rosa Whitfield committed to the Halyard partner "
            "onboarding checklist at the kickoff. What happened to it?",
            self.commitment("draft the Halyard partner onboarding checklist"),
        )
        f = self.fact("rosa", "former_name", "delgado")
        self.add(
            "identity_resolution",
            "What was Rosa Whitfield's surname before the change?",
            f.value,
            [f.value_norm],
            list(f.spoken),
            f.evidence,
            [f.id],
        )
        assert RENAMES["rosa"].old_name == f.value
        lim = self.commitment("build the Halyard rate limiter")
        p = person(lim.owner)
        self.add(
            "identity_resolution",
            "Which Alex is responsible for the Halyard rate "
            "limiter, the engineering manager or the account executive?",
            f'{p.name} ({p.role}), referred to as "AK"',
            [lim.owner],
            ["AK's team is taking the rate limiter"],
            lim.made_evidence,
            [lim.id],
        )
        self.status(
            "identity_resolution", "Did Alex Kim's team finish the Halyard rate limiter?", lim
        )
        f = self.fact("corvid", "halyard_prerequisite", "data processing addendum")
        self.who(
            "identity_resolution",
            "Which Daniel Cho at Corvid Bank asked for a signed "
            "data processing addendum, and what is their role?",
            f,
            "daniel_cm",
        )
        f = self.fact("corvid", "procurement_rule", "3")
        self.add(
            "identity_resolution",
            "What does Corvid Bank's procurement manager say budget sign-off for Halyard requires?",
            f.value,
            [f.value_norm],
            list(f.spoken),
            f.evidence,
            [f.id],
        )
        f = self.fact("jamie", "engagement", "contractor")
        self.add(
            "identity_resolution",
            "Who is drawing the help center icons, and are they a Northwind employee?",
            f.value,
            ["jamie", f.value_norm],
            list(f.spoken),
            f.evidence,
            [f.id],
        )

    def corrections(self) -> None:
        f = self.fact(HALYARD, "endpoint_count", "9")
        self.add(
            "correction",
            "How many endpoints does the Halyard partner sandbox expose?",
            f.value,
            [f.value_norm],
            list(f.spoken),
            f.evidence,
            [f.id],
        )
        f = self.fact("corvid", "halyard_pilot_users", "340")
        self.add(
            "correction",
            "How many pilot users is Corvid Bank planning for Halyard?",
            f.value,
            [f.value_norm],
            list(f.spoken),
            f.evidence,
            [f.id],
        )
        self.add(
            "correction",
            "Why did Corvid Bank's Halyard pilot user number change?",
            f"It was corrected from 400 to 340: {f.reason}.",
            ["340"],
            [*f.spoken, *f.reason_spoken],
            f.evidence,
            [f.id],
        )
        pipeline = self.meeting["v21-w7-partner-pipeline"]
        restated = self.fact(HALYARD, "launch_date", "2026-10-29")  # week 6
        later = next(  # Felix restating it in week 7, right after the misstatement
            f
            for f in self.versions(HALYARD, "launch_date")
            if f.value_norm == "2026-10-29" and f.id != restated.id
        )
        self.add(
            "correction",
            f"As of {_fmt(pipeline.day)}, after the partner pipeline "
            "meeting, what was the Halyard launch date?",
            restated.value,
            [restated.value_norm],
            ["it hasn't moved again", *restated.spoken],
            [*restated.evidence, *later.evidence],
            [restated.id, later.id],
            as_of=pipeline.day,
        )

    def commitments(self) -> None:
        c = self.commitment
        self.status(
            "commitment_tracking",
            "Did Felix Moreau find out whether Halyard partners need separate sandbox keys?",
            c("check whether Halyard partners need separate sandbox API keys"),
        )
        self.status(
            "commitment_tracking",
            "What happened to Jordan Blake's offer to get legal "
            "to look at the Halyard partner terms?",
            c("get legal to review the Halyard partner terms"),
        )
        self.status(
            "commitment_tracking",
            "Did the help center empty state illustrations get mocked up?",
            c("mock up the help center empty state illustrations"),
        )
        self.status(
            "commitment_tracking",
            "Was the help center icon set delivered?",
            c("deliver the help center icon set"),
        )
        self.status(
            "commitment_tracking",
            "Did Corvid Bank receive the Halyard security overview?",
            c("send the Halyard security overview to Corvid Bank"),
        )
        oob = [x for x in self.w.commitments if x.id in self.truth.out_of_band]
        self.add(
            "commitment_tracking",
            "Which Halyard-quarter commitments were reported done "
            "outside a meeting (on Slack or by email)?",
            "; ".join(f"{x.task} ({person(x.owner).name})" for x in oob),
            sorted({x.owner for x in oob}),
            [x.task_spoken for x in oob],
            [e for x in oob for e in x.resolved_evidence],
            [x.id for x in oob],
        )

    def long_horizon(self) -> None:
        live = [
            f for f in self.versions(HALYARD, "launch_date") if f.id not in self.truth.retracted
        ]
        moves: list[Fact] = []
        for f in live[1:]:
            if f.value_norm != (moves[-1] if moves else live[0]).value_norm:
                moves.append(f)
        steps = "; ".join(f"{m.value} because {m.reason}" for m in moves)
        self.add(
            "long_horizon",
            "How many times did the Halyard launch date move over the quarter, and why each time?",
            f"{len(moves)} times, from {live[0].value}: {steps}. (November 5, said on "
            "October 14, was a misstatement corrected on the spot.)",
            [str(len(moves)), *(m.value_norm for m in moves)],
            [s for m in moves for s in m.reason_spoken],
            [e for m in moves for e in m.evidence],
            [m.id for m in moves],
        )
        self.add(
            "long_horizon",
            "What was the original Halyard launch date, and what is it now?",
            f"Originally {live[0].value}; now {live[-1].value}.",
            [live[0].value_norm, live[-1].value_norm],
            [*live[0].spoken, *live[-1].spoken],
            [*live[0].evidence, *live[-1].evidence],
            [live[0].id, live[-1].id],
        )
        rosa = [x for x in self.w.commitments if x.owner == "rosa"]
        self.add(
            "long_horizon",
            "List every Halyard commitment Rosa Whitfield made this "
            "quarter and what happened to each.",
            "; ".join(f"{x.task}: {'slipped, then done' if x.new_due else 'done'}" for x in rosa),
            ["slipped", "done"],
            [x.task_spoken for x in rosa],
            [e for x in rosa for e in (*x.made_evidence, *x.resolved_evidence)],
            [x.id for x in rosa],
        )

    # -- transcript-dependent families (exact wording, speaker attribution) ------------------
    # Added for the transcript-retirement curve: answers that a fact store or a summary does
    # not keep (verbatim wording, who spoke). They span the whole world, v2 meetings included.

    def _unique_spans(self) -> dict[str, int]:
        """How many beats in the whole world contain each protected span (lowercased)."""
        texts = [b.text.lower() for m in self.w.meetings for b in m.beats]
        spans = {p.lower() for m in self.w.meetings for b in m.beats for p in b.protect}
        return {sp: sum(sp in t for t in texts) for sp in spans}

    def _beat(self, e: Evidence) -> Beat:
        return self.meeting[e.meeting].beats[e.beat]

    def exact_quotes(self, rng: random.Random) -> None:
        """'Quote what X said': gold is the beat's clean wording; key phrases are its protected
        spans (noise-free in the transcript). The judge compares wording against the quote."""
        made: dict[tuple[str, str], list[Commitment]] = {}
        for c in self.w.commitments:
            made.setdefault((c.owner, c.made_in), []).append(c)
        pool = []
        for (owner, _key), cs in sorted(made.items()):
            beats = [e for e in cs[0].made_evidence if self._beat(e).speaker == owner]
            if len(cs) == 1 and len(beats) == 1 and self._beat(beats[0]).privacy is None:
                pool.append((cs[0], beats[0]))
        for c, e in rng.sample(pool, min(8, len(pool))):
            b, m = self._beat(e), self.meeting[e.meeting]
            self.add(
                "exact_quote",
                f'Quote, word for word, the commitment {person(c.owner).name} made in "{m.title}" '
                f"on {_fmt(m.day)}.",
                f'"{b.text}"',
                [p.lower() for p in b.protect],
                list(b.protect),
                [e],
                [c.id],
            )
        stated = []
        for f in self.w.facts:
            if f.privacy:
                continue
            by_owner = [  # Priya *states* the value (not just asks about it)
                e
                for e in f.evidence
                if self._beat(e).speaker == "priya"
                and any(sp in self._beat(e).text for sp in f.spoken)
                and not self._beat(e).text.endswith("?")
            ]
            if len(by_owner) == 1 and self._beat(by_owner[0]).protect:
                stated.append((f, by_owner[0]))
        for f, e in rng.sample(stated, min(4, len(stated))):
            b, m = self._beat(e), self.meeting[e.meeting]
            self.add(
                "exact_quote",
                f"What exactly did Priya say about the {_label(f.predicate)} of "
                f'{_name(f.subject)} in "{m.title}" on {_fmt(m.day)}? Quote the exact words.',
                f'"{b.text}"',
                [p.lower() for p in b.protect],
                list(b.protect),
                [e],
                [f.id],
            )

    def speaker_attributions(self, rng: random.Random) -> None:
        """'Who said <phrase>?' and 'who first raised <topic>?', across remote, in-person and
        hybrid-room meetings. Phrases are protected spans that occur in exactly one beat."""
        counts = self._unique_spans()
        pools: dict[str, list[tuple[str, Evidence]]] = {"remote": [], "in_person": [], "room": []}
        for m in self.w.meetings:
            room = self.truth.rooms.get(m.key)
            for i, b in enumerate(m.beats):
                if b.filler or b.privacy or b.speaker == "priya":
                    continue
                for sp in b.protect:
                    distinctive = len(sp.split()) >= 4 and not sp.lower().startswith("by ")
                    if distinctive and counts[sp.lower()] == 1:
                        kind = "room" if room and b.speaker in room.members else m.mode
                        if kind in pools:
                            pools[kind].append((sp, Evidence(meeting=m.key, beat=i)))
                        break
        quota = {"in_person": 4, "remote": 4, "room": 3}
        for setting, n in quota.items():
            for sp, e in rng.sample(pools[setting], min(n, len(pools[setting]))):
                b, m = self._beat(e), self.meeting[e.meeting]
                p = person(b.speaker)
                self.add(
                    "speaker_attribution",
                    f'Who said "{sp}" in "{m.title}" on {_fmt(m.day)}?',
                    f"{p.name} ({p.role})",
                    [b.speaker],
                    [sp],
                    [e],
                    [],
                )
        firsts = []
        for subject, predicate in sorted({(f.subject, f.predicate) for f in self.w.facts}):
            first = self.versions(subject, predicate)[0]
            if first.privacy or first.kind == "attribute":
                continue
            stating = [
                e
                for e in first.evidence
                if any(sp in self._beat(e).text for sp in first.spoken)
                and self._beat(e).speaker != "priya"
            ]
            if len(stating) == 1 and len(self.versions(subject, predicate)) > 1:
                firsts.append((first, stating[0]))
        in_person = [x for x in firsts if self.meeting[x[1].meeting].mode == "in_person"]
        rest = [x for x in firsts if x not in in_person]
        picks = in_person[:1] + rng.sample(rest, min(4 - len(in_person[:1]), len(rest)))
        for f, e in picks:
            b, m = self._beat(e), self.meeting[e.meeting]
            p = person(b.speaker)
            self.add(
                "speaker_attribution",
                f"Who first raised the {_label(f.predicate)} for {_name(f.subject)}, and in "
                "which meeting?",
                f'{p.name} ({p.role}), in "{m.title}" on {m.day.isoformat()}',
                [b.speaker, m.day.isoformat()],
                [sp for sp in f.spoken if sp in b.text],
                [e],
                [f.id],
            )


def extras_questions(world: World, beat_map: BeatMap, seed: int) -> list[Question]:
    """The v2.1 questions (ids ``x001``…), with their own seeded dev/test split."""
    g = _ExtrasGen(world, beat_map, seed)
    g.room_attribution()
    g.identity()
    g.corrections()
    g.commitments()
    g.long_horizon()
    transcript_rng = random.Random(f"{seed}:extras-transcript")  # keeps earlier splits stable
    g.exact_quotes(transcript_rng)
    g.speaker_attributions(transcript_rng)
    g.split()
    for q in g.qs:
        assert q.as_of <= NOW
    return g.qs

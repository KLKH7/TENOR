"""The Northwind Labs storyline: 40 meetings over 8 weeks (2026-08-31 → 2026-10-23).

The *plot* is hand-authored so that every planted difficulty is deliberate and documented:

* the Northwind Teams price is decided at $20 (wk 2) and changed to $25 (wk 5), with a reason;
* the launch date slips twice: Oct 14 → Oct 28 (wk 4) → Nov 11 (wk 7), each with a reason;
* ownership of Falcon (= payments migration = PAY-212) is handed from Sam Patel to Tomás (wk 4);
* two Sams and two Alexes, sometimes in the same meeting; Beacon (project) vs Beacon Freight;
* customer requests that are resolved, still open, or never answered;
* private content (health, family, compensation) in 1:1s and one customer call.

The seed controls everything that isn't plot: which engineers commit to which tasks in
standups and what happens to those commitments (kept / slipped / dropped / reassigned), small
talk, start-time jitter, and phrasing variants.
"""

from __future__ import annotations

from collections.abc import Callable

from concentrate.core.spoken import say_date, say_int, say_money, say_thousands
from concentrate.synth.roster import STAFF
from concentrate.synth.story import M, Story, day
from concentrate.synth.world import Commitment, Fact, Outcome, World

MON, TUE, WED, THU, FRI = range(5)
WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

ENG = ["alex_k", "sam_p", "megan", "tomas", "ines", "kofi", "ruth"]
LAUNCH = ["marcus", "dev", "elena", "alex_k"]
ALL_STAFF = [p.id for p in STAFF]

TASKS: dict[str, list[tuple[str, str]]] = {
    "sam_p": [
        ("add idempotency keys to the webhook handler", "idempotency keys to the webhook handler"),
        ("write the refund reconciliation job", "refund reconciliation job"),
        ("fix the double-charge retry bug", "double charge retry bug"),
        ("speed up the invoice export", "invoice export"),
    ],
    "megan": [
        ("ship the new pricing page", "new pricing page"),
        ("fix the Safari date picker bug", "Safari date picker"),
        ("implement the three-step onboarding flow", "three step onboarding flow"),
        ("clean up the settings page", "settings page"),
    ],
    "tomas": [
        ("set up the Falcon staging database", "Falcon staging database"),
        ("rotate the payment provider keys", "payment provider keys"),
        ("add alerting for failed charges", "alerting for failed charges"),
        ("upgrade the Postgres cluster", "Postgres cluster"),
    ],
    "ines": [
        ("get the Otter beta onto TestFlight", "Otter beta onto TestFlight"),
        ("fix the push notification crash", "push notification crash"),
        ("add offline mode to Otter", "offline mode"),
        ("cut the Android release build", "Android release build"),
    ],
    "kofi": [
        ("backfill the Lighthouse events table", "Lighthouse events table"),
        ("fix the duplicate signup events", "duplicate signup events"),
        ("write the churn cohort query", "churn cohort query"),
        ("document the new event schema", "new event schema"),
    ],
    "ruth": [
        ("set up the device lab for Otter", "device lab"),
        ("triage the flaky end-to-end tests", "flaky end to end tests"),
        ("write the release checklist", "release checklist"),
        ("write the regression suite for checkout", "regression suite for checkout"),
    ],
    "alex_k": [
        ("draft the new on-call rotation", "on-call rotation"),
        ("write up the August outage review", "August outage review"),
        ("finish the backend job description", "backend job description"),
    ],
}

OPENERS = [
    "Can everyone hear me okay?",
    "Sorry, I was on another call, give me one sec.",
    "Hey hey, let's give it a minute for people to join.",
    "Morning all.",
    "Okay I think we're good to start.",
]
CLOSERS = [
    "Okay, thanks everyone.",
    "Cool, I'll let you all go. Thanks.",
    "Great, that's it from me. Talk soon.",
    "Alright, thanks all, have a good one.",
]
SMALL_TALK = [
    "Did anyone else's internet die this morning?",
    "How was everyone's weekend?",
    "My coffee machine broke so apologies in advance.",
    "Is it just me or is the office freezing today?",
]


class _StandupEngine:
    """Seeded commitments in standups, with outcomes resolved in later standups."""

    WEIGHTS: tuple[tuple[Outcome, float], ...] = (
        ("kept", 0.45), ("slipped", 0.2), ("dropped", 0.2), ("reassigned", 0.15),
    )  # fmt: skip

    def __init__(self, s: Story) -> None:
        self.s = s
        self.pools = {k: list(v) for k, v in TASKS.items()}
        for pool in self.pools.values():
            s.rng.shuffle(pool)
        self.pending: dict[int, list[tuple[str, Commitment]]] = {}

    def _outcome(self, week: int) -> Outcome:
        if week == 8:
            return "open"
        r, acc = self.s.rng.random(), 0.0
        for outcome, w in self.WEIGHTS:
            acc += w
            if r < acc:
                return outcome
        return "kept"

    def resolutions(self, m: M, week: int, runner: str) -> None:
        s = self.s
        for action, c in self.pending.pop(week, []):
            who = s.first(c.owner)
            if action == "done":
                # Name cues are deliberately unprotected: ASR may mishear them (Megan → Meghan).
                p = [] if runner == c.owner else [m.say(runner, f"{who}, anything to report?")]
                b = m.say(
                    c.owner,
                    f"Yeah, the {c.task_spoken} is done, that went out last week.",
                    c.task_spoken,
                )
                s.resolve(c, m, [*p, b], resolved_on=m.day)
            elif action == "late_done":
                b = m.say(
                    c.owner,
                    f"Oh and the {c.task_spoken} is finally done, sorry that took a while.",
                    c.task_spoken,
                )
                s.resolve(c, m, [b], resolved_on=m.day)
            elif action == "slip":
                new_due = day(week, s.rng.choice([WED, THU, FRI]))
                wd = WEEKDAY[new_due.weekday()]
                b = m.say(
                    c.owner,
                    f"The {c.task_spoken} slipped, I got pulled into other stuff. "
                    f"I'll have it by {wd}.",
                    c.task_spoken,
                    f"by {wd}",
                )
                s.resolve(c, m, [b], new_due=new_due)
                if week + 1 <= 8:
                    self.pending.setdefault(week + 1, []).append(("late_done", c))
            elif action == "reassign":
                options = [e for e in ENG if e not in (c.owner, "alex_k")]
                new = s.rng.choice(options)
                if c.owner == "alex_k":  # Alex runs the standup: first person
                    b = m.say(
                        "alex_k",
                        f"I'm heads down on other things this week, so "
                        f"{s.first(new)} is going to take the {c.task_spoken} instead.",
                        s.first(new),
                        c.task_spoken,
                    )
                else:
                    b = m.say(
                        "alex_k",
                        f"{who} is heads down on other things this week, so "
                        f"{s.first(new)} is going to take the {c.task_spoken} instead.",
                        who,
                        s.first(new),
                        c.task_spoken,
                    )
                s.resolve(c, m, [b], new_owner=new)

    def new_commitments(self, m: M, week: int, runner: str, count: int = 3) -> None:
        s = self.s
        candidates = [p for p in ENG if self.pools[p]]
        for owner in s.rng.sample(candidates, min(count, len(candidates))):
            task, spoken = self.pools[owner].pop()
            due = day(week, s.rng.choice([WED, THU, FRI]))
            wd = WEEKDAY[due.weekday()]
            who = s.first(owner)
            prompt = (
                []
                if runner == owner  # the runner doesn't call on himself
                else [
                    m.say(
                        runner,
                        s.pick(
                            [
                                f"{who}, what's on your plate?",
                                f"{who}, you're up.",
                                f"Okay {who}, what about you?",
                            ]
                        ),
                    )
                ]
            )
            line = s.pick(
                [
                    f"So my main thing is the {spoken}, should be done by {wd}.",
                    f"This week I'll get the {spoken} done by {wd}.",
                    f"I'm on the {spoken}, I'll have it by {wd}.",
                ]
            )
            b = m.say(owner, line, spoken, f"by {wd}")
            outcome = self._outcome(week)
            c = s.commit(
                m,
                [*prompt, b],
                owner=owner,
                task=task,
                task_spoken=spoken,
                due=due,
                outcome=outcome,
            )
            action = {"kept": "done", "slipped": "slip", "reassigned": "reassign"}.get(outcome)
            if action:
                self.pending.setdefault(week + 1, []).append((action, c))


def _open(s: Story, m: M, who: str) -> None:
    m.say(who, s.pick(OPENERS))
    if s.rng.random() < 0.5:
        others = [a for a in m.m.attendees if a != who]
        m.say(s.rng.choice(others), s.pick(SMALL_TALK))


def _close(s: Story, m: M, who: str) -> None:
    m.say(who, s.pick(CLOSERS))


def build_world(seed: int, scale: str = "base") -> World:
    """Construct the ground-truth world for ``seed``.

    ``scale="xl"`` adds the procedural Northwind-XL meetings (``synth.extend``) on top of the
    hand-authored plot, which is identical at both scales.
    """
    s = Story(seed)
    eng = _StandupEngine(s)
    facts: dict[str, Fact | Commitment] = {}
    handlers: dict[str, Callable[[M], None]] = {}

    def standup(week: int) -> None:
        """Weekly standup: resolutions of earlier commitments, scripted items, new commitments."""
        runner = "priya" if week % 2 else "alex_k"
        m = s.meeting(
            f"w{week}-standup",
            "Eng standup",
            "standup",
            week,
            MON,
            9,
            20,
            ENG,
            folder="Engineering",
        )
        _open(s, m, runner)
        eng.resolutions(m, week, runner)
        hook = handlers.get(f"w{week}-standup")
        if hook is not None:
            hook(m)
        eng.new_commitments(m, week, runner)
        _close(s, m, runner)

    # ---------------------------------------------------------------- week 1
    def w1_standup(m: M) -> None:
        p = m.say("priya", "Sam, you're still on point for Falcon, right?", "Sam", "Falcon")
        b = m.say(
            "sam_p",
            "Yep, PAY two twelve is mine, the payments migration is on track.",
            "PAY two twelve is mine",
        )
        facts["falcon_owner"] = s.fact(
            m,
            [p, b],
            kind="ownership",
            subject="falcon",
            predicate="owner",
            value="Sam Patel",
            value_norm="sam_p",
            spoken=("Sam", "PAY two twelve is mine"),
            bullet="Sam confirmed he owns Falcon (PAY-212)",
        )
        p = m.say("priya", "And Sam, the cutover script, when can we see it?", "cutover script")
        b = m.say(
            "sam_p",
            "I'll have the cutover script ready for review by Thursday.",
            "cutover script",
            "by Thursday",
        )
        facts["c_cutover_script"] = s.commit(
            m,
            [p, b],
            owner="sam_p",
            task="write the Falcon cutover script",
            task_spoken="cutover script",
            due=day(1, THU),
            outcome="slipped",
        )

    handlers["w1-standup"] = w1_standup
    standup(1)

    m = s.meeting(
        "w1-lighthouse",
        "Lighthouse planning",
        "planning",
        1,
        TUE,
        14,
        45,
        ["kofi", "grace", "dev"],
        folder="Engineering",
        platform="meet",
    )
    _open(s, m, "priya")
    b1 = m.say(
        "dev",
        "So the plan is we move Lighthouse onto the new warehouse and retire the old cron jobs.",
        "Lighthouse onto the new warehouse",
    )
    s.fact(
        m,
        [b1],
        kind="decision",
        subject="lighthouse",
        predicate="plan",
        value="Move Lighthouse onto the new warehouse and retire the old cron jobs",
        value_norm="new warehouse",
        spoken=("Lighthouse onto the new warehouse",),
        bullet="Lighthouse will move to the new warehouse; old cron jobs retired",
    )
    b2 = m.say(
        "kofi",
        "I can own it. Realistically the dashboards are migrated by October thirtieth.",
        "I can own it",
        say_date(day(9, FRI)),
    )
    s.fact(
        m,
        [b2],
        kind="ownership",
        subject="lighthouse",
        predicate="owner",
        value="Kofi Mensah",
        value_norm="kofi",
        spoken=("I can own it",),
        bullet="Kofi owns Lighthouse",
    )
    s.fact(
        m,
        [b2],
        kind="date",
        subject="lighthouse",
        predicate="target_date",
        value="2026-10-30",
        value_norm="2026-10-30",
        spoken=(say_date(day(9, FRI)),),
        bullet="Target: dashboards migrated by Oct 30",
    )
    p = m.say("priya", "Grace, can you pin down the activation metric definition?", "Grace")
    b = m.say(
        "grace",
        "Sure, I'll write up the activation metric by next Friday.",
        "activation metric",
        "by next Friday",
    )
    s.commit(
        m,
        [p, b],
        owner="grace",
        task="define the activation metric",
        task_spoken="activation metric",
        due=day(2, FRI),
        outcome="dropped",
    )
    _close(s, m, "priya")

    m = s.meeting(
        "w1-acmeway-intro",
        "Northwind <> Acmeway intro",
        "customer",
        1,
        WED,
        16,
        30,
        ["alex_r", "dana"],
        folder="Acmeway",
        platform="meet",
        customer="acmeway",
    )
    _open(s, m, "alex_r")
    m.say("alex_r", "Dana, thanks for making time. Priya runs product, so she can go deep.")
    b = m.say(
        "dana",
        "Main thing for us is a CSV export of seat usage, our finance team lives in spreadsheets.",
        "CSV export of seat usage",
    )
    facts["acme_csv"] = s.fact(
        m,
        [b],
        kind="request",
        subject="acmeway",
        predicate="request:csv_export",
        value="CSV export of seat usage (open)",
        value_norm="open",
        spoken=("CSV export of seat usage",),
        bullet="Acmeway wants a CSV export of seat usage",
    )
    b = m.say("dana", "We'd probably start with around forty seats.", "forty seats")
    s.fact(
        m,
        [b],
        kind="deal",
        subject="acmeway",
        predicate="seats",
        value="40 seats",
        value_norm="40",
        spoken=("forty seats",),
        bullet="Acmeway: ~40 seats to start",
    )
    m.say("priya", "That's really helpful. Let me take the export back to the team.")
    _close(s, m, "alex_r")

    m = s.meeting(
        "w1-beaconfreight",
        "Beacon Freight discovery",
        "customer",
        1,
        THU,
        15,
        30,
        ["alex_r", "felix", "owen"],
        folder="Beacon Freight",
        customer="beacon_freight",
    )
    _open(s, m, "alex_r")
    b = m.say(
        "owen",
        "Non-negotiable for us is SSO. Our IT policy doesn't allow separate passwords.",
        "SSO",
    )
    facts["bf_sso"] = s.fact(
        m,
        [b],
        kind="request",
        subject="beacon_freight",
        predicate="request:sso",
        value="SSO (open)",
        value_norm="open",
        spoken=("SSO",),
        bullet="Beacon Freight requires SSO",
    )
    b = m.say(
        "owen",
        "And our current contract with the other vendor is up on December "
        "first, so that's our decision date.",
        say_date(day(14, TUE)),
    )
    s.fact(
        m,
        [b],
        kind="date",
        subject="beacon_freight",
        predicate="decision_date",
        value="2026-12-01",
        value_norm="2026-12-01",
        spoken=(say_date(day(14, TUE)),),
        bullet="Beacon Freight decision date: Dec 1 (current contract ends)",
    )
    m.say("felix", "Got it. Our SSO is on the roadmap with the team plan.")
    _close(s, m, "alex_r")

    m = s.meeting(
        "w1-kickoff",
        "Northwind Teams launch kickoff",
        "planning",
        1,
        FRI,
        11,
        50,
        [*LAUNCH, "olivia", "jordan"],
        folder="Leadership",
    )
    _open(s, m, "marcus")
    b = m.say(
        "marcus",
        f"Okay so let's lock it. Northwind Teams launches on {say_date(day(7, WED))}.",
        say_date(day(7, WED)),
    )
    facts["launch"] = s.fact(
        m,
        [b],
        kind="date",
        subject="teams",
        predicate="launch_date",
        value="2026-10-14",
        value_norm="2026-10-14",
        spoken=(say_date(day(7, WED)),),
        bullet="Northwind Teams launch date set: Oct 14",
    )
    b = m.say(
        "dev",
        "The one dependency I'm worried about is Falcon. The new billing has to "
        "be live before we can sell seats.",
        "Falcon",
        "has to be live before",
    )
    s.fact(
        m,
        [b],
        kind="decision",
        subject="teams",
        predicate="depends_on",
        value="Falcon (the payments migration) must be live before launch",
        value_norm="falcon",
        spoken=("Falcon", "has to be live before"),
        bullet="Launch depends on Falcon being live",
    )
    m.say("olivia", "Pricing we'll settle next week, I want Jordan's input from the pipeline.")
    _close(s, m, "marcus")

    # ---------------------------------------------------------------- week 2
    def w2_standup(m: M) -> None:
        c = facts["c_cutover_script"]
        assert isinstance(c, Commitment)
        b = m.say(
            "sam_p",
            "Cutover script needs another few days, the refund path is messier "
            "than I thought. Friday.",
            "Cutover script",
            "Friday",
        )
        s.resolve(c, m, [b], new_due=day(2, FRI))

    handlers["w2-standup"] = w2_standup
    standup(2)

    m = s.meeting(
        "w2-pricing",
        "Pricing review: Northwind Teams",
        "decision",
        2,
        TUE,
        13,
        45,
        ["marcus", "olivia", "jordan", "elena"],
        folder="Leadership",
    )
    _open(s, m, "olivia")
    m.say(
        "jordan",
        "Pipeline feedback is pretty consistent, anything above twenty five a seat "
        "and we lose the smaller teams.",
    )
    m.say("olivia", "My margin model works at twenty, it's tighter but it works.")
    b = m.say(
        "marcus", f"Okay, decision: {say_money(20)} per seat per month for Teams.", say_money(20)
    )
    facts["price"] = s.fact(
        m,
        [b],
        kind="decision",
        subject="teams",
        predicate="price_per_seat",
        value="$20 per seat per month",
        value_norm="20",
        spoken=(say_money(20),),
        bullet="Decision: Northwind Teams priced at $20/seat/month",
    )
    m.say("priya", "I'll update the pricing page copy and let sales know.")
    _close(s, m, "marcus")

    m = s.meeting(
        "w2-design-beacon",
        "Design review: Beacon onboarding",
        "design_review",
        2,
        WED,
        15,
        45,
        ["sam_o", "lena", "elena", "megan"],
        folder="Design",
    )
    _open(s, m, "lena")
    m.say("lena", "Sam, do you want to walk us through it?")
    b = m.say(
        "sam_o",
        "Sure. The big change is three steps instead of five, we merge "
        "workspace setup and invites.",
        "three steps instead of five",
    )
    facts["beacon_steps"] = s.fact(
        m,
        [b],
        kind="decision",
        subject="beacon",
        predicate="onboarding_steps",
        value="3 steps (down from 5)",
        value_norm="3",
        spoken=("three steps instead of five",),
        bullet="Beacon onboarding goes from five steps to three",
    )
    p = m.say(
        "priya",
        "Love it. Sam, can you get a clickable prototype for usability testing?",
        "Sam",
        "clickable prototype",
    )
    b = m.say(
        "sam_o",
        "Yes, clickable prototype by next Wednesday.",
        "clickable prototype",
        "by next Wednesday",
    )
    s.commit(
        m,
        [p, b],
        owner="sam_o",
        task="build the clickable Beacon prototype",
        task_spoken="clickable prototype",
        due=day(3, WED),
        outcome="kept",
    )
    m.say("megan", "From the front end side three steps is easier anyway.")
    _close(s, m, "lena")

    m = s.meeting(
        "w2-acmeway-pricing",
        "Acmeway: pilot pricing",
        "customer",
        2,
        THU,
        16,
        30,
        ["alex_r", "dana"],
        folder="Acmeway",
        platform="meet",
        customer="acmeway",
    )
    _open(s, m, "alex_r")
    b = m.say("priya", f"So for the pilot we'd do {say_money(20)} a seat.", say_money(20))
    b2 = m.say("dana", "Twenty works for us. Can you send the order form?", "Twenty works")
    facts["acme_price"] = s.fact(
        m,
        [b, b2],
        kind="deal",
        subject="acmeway",
        predicate="pilot_price",
        value="$20 per seat per month",
        value_norm="20",
        spoken=(say_money(20), "Twenty works"),
        bullet="Acmeway agreed to the pilot at $20/seat",
    )
    b3 = m.say("priya", "Yep, I'll get the order form to you by Friday.", "order form", "by Friday")
    facts["c_order_form"] = s.commit(
        m,
        [b3],
        owner="priya",
        task="send Acmeway the order form",
        task_spoken="order form",
        due=day(2, FRI),
        outcome="kept",
    )
    _close(s, m, "alex_r")

    m = s.meeting(
        "w2-1on1-alex", "Priya / Alex 1:1", "one_on_one", 2, FRI, 10, 30, ["alex_k"], folder="1:1s"
    )
    m.say("priya", "How are things?")
    b = m.say(
        "alex_k",
        "Good. One thing, I want Tomás to lead the on-call rotation from October, he's earned it.",
        "Tomás to lead the on-call rotation",
    )
    s.fact(
        m,
        [b],
        kind="ownership",
        subject="on_call",
        predicate="lead",
        value="Tomás Herrera",
        value_norm="tomas",
        spoken=("Tomás to lead the on-call rotation",),
        bullet="Alex wants Tomás to lead on-call from October",
    )
    m.say("priya", "Makes sense to me.")

    # ---------------------------------------------------------------- week 3
    def w3_standup(m: M) -> None:
        c = facts["c_cutover_script"]
        assert isinstance(c, Commitment)
        b = m.say("sam_p", "Cutover script is done and reviewed.", "Cutover script is done")
        s.resolve(c, m, [b], resolved_on=m.day)

    handlers["w3-standup"] = w3_standup
    standup(3)

    m = s.meeting(
        "w3-falcon",
        "Falcon cutover planning",
        "planning",
        3,
        TUE,
        14,
        45,
        ["sam_p", "tomas", "alex_k", "dev"],
        folder="Engineering",
    )
    _open(s, m, "alex_k")
    b = m.say(
        "sam_p",
        f"Proposal is we cut over on {say_date(day(4, THU))}, Thursday night, low traffic.",
        say_date(day(4, THU)),
    )
    facts["cutover"] = s.fact(
        m,
        [b],
        kind="date",
        subject="falcon",
        predicate="cutover_date",
        value="2026-09-24",
        value_norm="2026-09-24",
        spoken=(say_date(day(4, THU)),),
        bullet="Falcon cutover planned for Sep 24",
    )
    p = m.say("alex_k", "Tomás, you're doing the dry run?", "Tomás", "dry run")
    b = m.say(
        "tomas", "Yeah, I'll run the full dry run on staging by Monday.", "dry run", "by Monday"
    )
    facts["c_dry_run"] = s.commit(
        m,
        [p, b],
        owner="tomas",
        task="run the Falcon dry run on staging",
        task_spoken="dry run",
        due=day(4, MON),
        outcome="kept",
    )
    _close(s, m, "dev")

    m = s.meeting(
        "w3-corvid",
        "Corvid Bank: requirements",
        "customer",
        3,
        WED,
        16,
        45,
        ["alex_r", "jordan", "yusuf", "mira"],
        folder="Corvid Bank",
        customer="corvid",
    )
    _open(s, m, "alex_r")
    b = m.say(
        "mira",
        "For us to sign, we need SSO and audit logs. Both. That's from our "
        "regulator, not a preference.",
        "SSO and audit logs",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="corvid",
        predicate="request:sso_audit_logs",
        value="SSO and audit logs (required to sign)",
        value_norm="open",
        spoken=("SSO and audit logs",),
        bullet="Corvid Bank requires SSO and audit logs to sign",
    )
    b = m.say(
        "mira",
        "If that's there we're looking at a hundred and fifty seats in the first phase.",
        "a hundred and fifty seats",
    )
    facts["corvid_seats"] = s.fact(
        m,
        [b],
        kind="deal",
        subject="corvid",
        predicate="seats",
        value="150 seats",
        value_norm="150",
        spoken=("a hundred and fifty seats",),
        bullet="Corvid Bank: 150 seats in phase one if requirements met",
    )
    m.say("yusuf", "We can share our security overview after this call.")
    _close(s, m, "jordan")

    m = s.meeting(
        "w3-1on1-alex",
        "Priya / Alex 1:1",
        "one_on_one",
        3,
        THU,
        12,
        30,
        ["alex_k"],
        folder="1:1s",
        mode="in_person",
    )
    m.say("priya", "Okay, what's going on with you?")
    b = m.say(
        "alex_k",
        f"Personal news, my partner's due in November, so I'm planning to take "
        f"parental leave from {say_date(day(12, MON))}.",
        "parental leave",
        say_date(day(12, MON)),
        privacy="family",
    )
    s.fact(
        m,
        [b],
        kind="attribute",
        subject="alex_k",
        predicate="parental_leave_start",
        value="2026-11-16",
        value_norm="2026-11-16",
        spoken=(say_date(day(12, MON)),),
        privacy="family",
        bullet="Alex is planning parental leave from Nov 16",
    )
    m.say("priya", "That's amazing, congratulations!")
    b = m.say(
        "alex_k",
        "Work stuff, Ruth is ready to own release testing, I'd like to make that official.",
        "Ruth is ready to own release testing",
    )
    s.fact(
        m,
        [b],
        kind="ownership",
        subject="release_testing",
        predicate="owner",
        value="Ruth Abebe",
        value_norm="ruth",
        spoken=("Ruth is ready to own release testing",),
        bullet="Ruth to own release testing",
    )

    m = s.meeting(
        "w3-launch-sync",
        "Teams launch sync",
        "sync",
        3,
        FRI,
        11,
        30,
        [*LAUNCH, "jordan"],
        folder="Leadership",
    )
    _open(s, m, "marcus")
    c = facts["c_order_form"]
    assert isinstance(c, Commitment)
    b = m.say(
        "priya",
        "Quick win, the Acmeway order form went out Friday and they signed.",
        "Acmeway order form went out",
    )
    s.resolve(c, m, [b], resolved_on=m.day)
    m.say("dev", "Falcon is the risk, the dry run is next week. If that's clean we're fine.")
    _close(s, m, "marcus")

    # ---------------------------------------------------------------- week 4
    def w4_standup(m: M) -> None:
        c = facts["c_dry_run"]
        assert isinstance(c, Commitment)
        b = m.say(
            "tomas",
            "Dry run happened. It failed on refunds, so the twenty fourth isn't happening.",
            "Dry run happened",
            "failed on refunds",
        )
        s.resolve(c, m, [b], resolved_on=m.day)
        b = m.say(
            "alex_k",
            "Also heads up, Sam's moving onto the Corvid SSO work, so Tomás is "
            "taking over Falcon from today.",
            "Corvid SSO work",
            "Tomás is taking over Falcon",
        )
        s.fact(
            m,
            [b],
            kind="ownership",
            subject="falcon",
            predicate="owner",
            value="Tomás Herrera",
            value_norm="tomas",
            spoken=("Tomás is taking over Falcon",),
            reason="Sam Patel moved onto the Corvid Bank SSO work",
            reason_spoken=("Corvid SSO work",),
            bullet="Falcon handed from Sam Patel to Tomás (Sam moves to Corvid SSO)",
        )

    handlers["w4-standup"] = w4_standup
    standup(4)

    m = s.meeting(
        "w4-launch-sync",
        "Teams launch sync",
        "sync",
        4,
        TUE,
        11,
        30,
        [*LAUNCH, "tomas"],
        folder="Leadership",
    )
    _open(s, m, "marcus")
    bt = m.say(
        "tomas",
        f"Realistic Falcon cutover is {say_date(day(6, THU))}, after we fix the refund path.",
        say_date(day(6, THU)),
        "refund path",
    )
    s.fact(
        m,
        [bt],
        kind="date",
        subject="falcon",
        predicate="cutover_date",
        value="2026-10-08",
        value_norm="2026-10-08",
        spoken=(say_date(day(6, THU)),),
        reason="the staging dry run failed on refunds",
        reason_spoken=("refund path",),
        bullet="Falcon cutover moved to Oct 8 (refund path fix)",
    )
    b = m.say(
        "marcus",
        f"Then we can't launch on the fourteenth. New launch date is {say_date(day(9, WED))}.",
        say_date(day(9, WED)),
    )
    s.fact(
        m,
        [bt, b],
        kind="date",
        subject="teams",
        predicate="launch_date",
        value="2026-10-28",
        value_norm="2026-10-28",
        spoken=(say_date(day(9, WED)),),
        reason="the Falcon cutover moved to October 8th",
        reason_spoken=(say_date(day(6, THU)),),
        bullet="Launch slips from Oct 14 to Oct 28 because the Falcon cutover moved",
    )
    m.say("elena", "I'll update the launch plan and tell marketing.")
    _close(s, m, "marcus")

    m = s.meeting(
        "w4-design-beacon",
        "Design review: Beacon onboarding v2",
        "design_review",
        4,
        WED,
        15,
        45,
        ["sam_o", "lena", "elena", "megan", "sam_p"],
        folder="Design",
    )
    _open(s, m, "lena")
    b = m.say(
        "sam_o",
        "We're cutting the product tour video. Nobody watched it in testing.",
        "cutting the product tour video",
    )
    s.fact(
        m,
        [b],
        kind="decision",
        subject="beacon",
        predicate="product_tour_video",
        value="Removed (nobody watched it in testing)",
        value_norm="removed",
        spoken=("cutting the product tour video",),
        bullet="Product tour video removed from onboarding",
    )
    p = m.say("priya", "Sam Patel, you had a point about SSO?", "Sam Patel")
    b = m.say(
        "sam_p",
        "Yeah, for SSO customers the login step has to come before workspace "
        "naming, otherwise we create orphan workspaces.",
        "has to come before workspace naming",
    )
    s.fact(
        m,
        [p, b],
        kind="decision",
        subject="beacon",
        predicate="sso_step_order",
        value="SSO login must come before workspace naming (Sam Patel)",
        value_norm="before",
        spoken=("Sam Patel", "has to come before workspace naming"),
        bullet="SSO login step moves before workspace naming",
    )
    m.say("sam_o", "Good catch, I'll swap them.")
    _close(s, m, "lena")

    m = s.meeting(
        "w4-allhands", "All hands", "all_hands", 4, THU, 17, 40, ALL_STAFF, folder="Company"
    )
    _open(s, m, "marcus")
    b = m.say("olivia", f"Numbers: we're at {say_thousands(312)} ARR.", say_thousands(312))
    facts["arr"] = s.fact(
        m,
        [b],
        kind="metric",
        subject="northwind",
        predicate="arr",
        value="$312k",
        value_norm="312000",
        spoken=(say_thousands(312),),
        bullet="ARR: $312k",
    )
    b = m.say("hana", f"Signups last month were {say_int(420)}, best month so far.", say_int(420))
    s.fact(
        m,
        [b],
        kind="metric",
        subject="northwind",
        predicate="monthly_signups",
        value="420",
        value_norm="420",
        spoken=(say_int(420),),
        bullet="Monthly signups: 420 (record)",
    )
    m.say("marcus", "And the launch moved to the twenty eighth, Dev will explain why.")
    _close(s, m, "marcus")

    m = s.meeting(
        "w4-1on1-samo",
        "Sam / Priya",
        "one_on_one",
        4,
        FRI,
        10,
        25,
        ["sam_o"],
        folder="1:1s",
        mode="in_person",
        calendar=False,
    )
    m.say("priya", "Hey, thanks for grabbing a room.")
    b = m.say(
        "sam_o",
        "Quick heads up, I've started physical therapy for my back on Tuesday "
        "mornings, so I'll be in late those days.",
        "physical therapy for my back",
        privacy="health",
    )
    s.fact(
        m,
        [b],
        kind="attribute",
        subject="sam_o",
        predicate="health",
        value="Physical therapy for back on Tuesday mornings",
        value_norm="physical therapy",
        spoken=("physical therapy for my back",),
        privacy="health",
        bullet="Sam has PT on Tuesday mornings",
    )
    b = m.say(
        "sam_o", "Bigger thing, I'd really like to own the design system.", "own the design system"
    )
    s.fact(
        m,
        [b],
        kind="attribute",
        subject="sam_o",
        predicate="goal",
        value="Wants to own the design system",
        value_norm="design system",
        spoken=("own the design system",),
        bullet="Sam wants to own the design system",
    )
    m.say("priya", "Let me talk to Lena.")

    # ---------------------------------------------------------------- week 5
    def w5_standup(m: M) -> None:
        m.say(
            "ruth",
            "I'm out Thursday afternoon for a doctor's appointment, just so you know.",
            "doctor's appointment",
            privacy="health",
        )

    handlers["w5-standup"] = w5_standup
    standup(5)

    m = s.meeting(
        "w5-pricing",
        "Pricing review: Teams v2",
        "decision",
        5,
        TUE,
        13,
        45,
        ["marcus", "olivia", "jordan", "elena", "alex_r"],
        folder="Leadership",
    )
    _open(s, m, "olivia")
    b = m.say(
        "jordan",
        "Corvid and Beacon Freight both need SSO and audit logs, and that's real cost for us.",
        "SSO and audit logs",
    )
    b2 = m.say(
        "marcus",
        f"So we put SSO and audit logs in the plan and go to {say_money(25)} a seat.",
        say_money(25),
    )
    s.fact(
        m,
        [b, b2],
        kind="decision",
        subject="teams",
        predicate="price_per_seat",
        value="$25 per seat per month",
        value_norm="25",
        spoken=(say_money(25),),
        reason="adding SSO and audit logs to the plan, which Corvid Bank and Beacon Freight "
        "require",
        reason_spoken=("SSO and audit logs",),
        bullet="Price raised to $25/seat/month; SSO + audit logs now included",
    )
    b = m.say("alex_r", "What about Acmeway? They signed at twenty.", "Acmeway")
    b2 = m.say(
        "marcus", "Acmeway keeps twenty for the pilot year. We honour it.", "Acmeway keeps twenty"
    )
    acme = facts["acme_price"]
    assert isinstance(acme, Fact)
    s.confirm(acme, m, [b, b2])
    s.fact(
        m,
        [b2],
        kind="decision",
        subject="acmeway",
        predicate="grandfathered",
        value="Acmeway keeps $20/seat for the pilot year",
        value_norm="yes",
        spoken=("Acmeway keeps twenty",),
        bullet="Acmeway grandfathered at $20 for pilot year",
    )
    _close(s, m, "marcus")

    m = s.meeting(
        "w5-driftwood",
        "Driftwood Retail check-in",
        "customer",
        5,
        WED,
        17,
        30,
        ["jordan", "nadia", "luis"],
        folder="Driftwood Retail",
        platform="meet",
        customer="driftwood",
    )
    _open(s, m, "nadia")
    b = m.say(
        "luis",
        "Honestly the onboarding is confusing for our store managers, half of them gave up.",
        "onboarding is confusing",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="driftwood",
        predicate="complaint:onboarding",
        value="Onboarding is confusing for store managers",
        value_norm="open",
        spoken=("onboarding is confusing",),
        bullet="Driftwood: onboarding confusing",
    )
    m.say("luis", "And we'd want fifteen percent off to renew.")
    b = m.say(
        "jordan",
        "We can't do fifteen. We can do ten percent if you prepay annually.",
        "ten percent if you prepay annually",
    )
    s.fact(
        m,
        [b],
        kind="decision",
        subject="driftwood",
        predicate="discount_offer",
        value="10% discount with annual prepay (15% declined)",
        value_norm="10",
        spoken=("ten percent if you prepay annually",),
        bullet="Offered Driftwood 10% for annual prepay (declined 15%)",
    )
    p = m.say("priya", "Nadia, could you send Luis our onboarding guide?", "Nadia")
    b = m.say(
        "nadia", "Yep, I'll send the onboarding guide tomorrow.", "onboarding guide", "tomorrow"
    )
    s.commit(
        m,
        [p, b],
        owner="nadia",
        task="send Driftwood the onboarding guide",
        task_spoken="onboarding guide",
        due=day(5, THU),
        outcome="dropped",
    )
    _close(s, m, "jordan")

    m = s.meeting(
        "w5-corvid",
        "Corvid Bank: security",
        "customer",
        5,
        THU,
        16,
        30,
        ["alex_r", "yusuf", "mira"],
        folder="Corvid Bank",
        customer="corvid",
    )
    _open(s, m, "alex_r")
    m.say(
        "mira", "We sent over our vendor security questionnaire, it's about two hundred questions."
    )
    p = m.say("alex_r", "Yusuf, can you take that?", "Yusuf")
    b = m.say(
        "yusuf",
        f"Yes, we'll have the questionnaire back by {say_date(day(6, FRI))}.",
        "questionnaire",
        say_date(day(6, FRI)),
    )
    facts["c_questionnaire"] = s.commit(
        m,
        [p, b],
        owner="yusuf",
        task="complete the Corvid Bank security questionnaire",
        task_spoken="questionnaire",
        due=day(6, FRI),
        outcome="kept",
    )
    _close(s, m, "alex_r")

    m = s.meeting(
        "w5-1on1-alex", "Priya / Alex 1:1", "one_on_one", 5, FRI, 10, 30, ["alex_k"], folder="1:1s"
    )
    m.say("priya", "What's on your list?")
    b = m.say(
        "alex_k",
        f"Awkward one. I'm at {say_thousands(165)}, and the offers I'm seeing "
        f"are around one eighty.",
        say_thousands(165),
        privacy="compensation",
    )
    s.fact(
        m,
        [b],
        kind="attribute",
        subject="alex_k",
        predicate="salary",
        value="$165k",
        value_norm="165000",
        spoken=(say_thousands(165),),
        privacy="compensation",
        bullet="Alex raised compensation ($165k vs ~$180k market)",
    )
    m.say("priya", "I hear you. Let me take it to Marcus and come back next week.")
    b = m.say(
        "priya",
        "Separately, the second backend engineer req is approved.",
        "second backend engineer req is approved",
    )
    s.fact(
        m,
        [b],
        kind="decision",
        subject="hiring",
        predicate="backend_req",
        value="Second backend engineer req approved",
        value_norm="approved",
        spoken=("second backend engineer req is approved",),
        bullet="Second backend engineer req approved",
    )

    # ---------------------------------------------------------------- week 6
    standup(6)

    m = s.meeting(
        "w6-design-beacon",
        "Design review: Beacon usability results",
        "design_review",
        6,
        TUE,
        15,
        45,
        ["sam_o", "lena", "elena", "megan", "ruth"],
        folder="Design",
    )
    _open(s, m, "lena")
    b = m.say(
        "sam_o",
        "Usability results are in. Completion went from fifty eight percent to "
        "seventy three percent with the three step flow.",
        "fifty eight percent",
        "seventy three percent",
    )
    s.fact(
        m,
        [b],
        kind="metric",
        subject="beacon",
        predicate="onboarding_completion",
        value="73% (up from 58%)",
        value_norm="73",
        spoken=("fifty eight percent", "seventy three percent"),
        bullet="Onboarding completion 58% → 73% in usability tests",
    )
    m.say("megan", "Nice, that's the flow I'm building so that's good to hear.")
    _close(s, m, "lena")

    m = s.meeting(
        "w6-investor",
        "Harbor Ventures monthly update",
        "investor",
        6,
        WED,
        16,
        30,
        ["marcus", "olivia", "simone"],
        folder="Leadership",
    )
    _open(s, m, "marcus")
    b = m.say(
        "olivia",
        f"ARR is {say_thousands(348)}, and we have twenty two months of runway.",
        say_thousands(348),
        "twenty two months of runway",
    )
    s.fact(
        m,
        [b],
        kind="metric",
        subject="northwind",
        predicate="arr",
        value="$348k",
        value_norm="348000",
        spoken=(say_thousands(348),),
        bullet="ARR: $348k",
    )
    s.fact(
        m,
        [b],
        kind="metric",
        subject="northwind",
        predicate="runway_months",
        value="22 months",
        value_norm="22",
        spoken=("twenty two months of runway",),
        bullet="Runway: 22 months",
    )
    m.say("simone", "Good momentum. What's the risk on the launch?")
    m.say("marcus", "Payments migration. It's scheduled this week.")
    _close(s, m, "marcus")

    m = s.meeting(
        "w6-acmeway",
        "Acmeway: pilot check-in",
        "customer",
        6,
        THU,
        16,
        30,
        ["alex_r", "nadia", "dana"],
        folder="Acmeway",
        platform="meet",
        customer="acmeway",
    )
    _open(s, m, "nadia")
    b = m.say(
        "nadia",
        "Good news, the CSV export of seat usage shipped on Tuesday.",
        "CSV export of seat usage shipped",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="acmeway",
        predicate="request:csv_export",
        value="CSV export of seat usage (shipped)",
        value_norm="done",
        spoken=("CSV export of seat usage shipped",),
        bullet="Acmeway's CSV export shipped",
    )
    b = m.say(
        "dana",
        "Great. Next ask, a Slack integration, so approvals ping our managers.",
        "Slack integration",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="acmeway",
        predicate="request:slack",
        value="Slack integration for approvals (open)",
        value_norm="open",
        spoken=("Slack integration",),
        bullet="Acmeway asked for a Slack integration",
    )
    _close(s, m, "alex_r")

    m = s.meeting(
        "w6-beaconfreight",
        "Beacon Freight: follow-up",
        "customer",
        6,
        FRI,
        15,
        30,
        ["alex_r", "felix", "owen"],
        folder="Beacon Freight",
        customer="beacon_freight",
    )
    _open(s, m, "alex_r")
    b = m.say(
        "felix",
        "SSO ships with the Teams launch, so it'll be there before your December decision.",
        "SSO ships with the Teams launch",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="beacon_freight",
        predicate="request:sso",
        value="SSO (committed: ships with the Teams launch)",
        value_norm="committed",
        spoken=("SSO ships with the Teams launch",),
        bullet="SSO will ship with the Teams launch",
    )
    b = m.say(
        "owen",
        "One more, we need our data stored in the EU. Is that possible?",
        "data stored in the EU",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="beacon_freight",
        predicate="request:eu_residency",
        value="EU data residency (open, no answer given)",
        value_norm="open",
        spoken=("data stored in the EU",),
        bullet="Beacon Freight asked about EU data residency",
    )
    m.say("alex_r", "Let me check with the team and get back to you.")
    _close(s, m, "alex_r")

    # ---------------------------------------------------------------- week 7
    def w7_standup(m: M) -> None:
        b = m.say(
            "tomas",
            f"Falcon is done. Cutover went through on {say_date(day(6, THU))}, no failed charges.",
            "Falcon is done",
            say_date(day(6, THU)),
        )
        s.fact(
            m,
            [b],
            kind="status",
            subject="falcon",
            predicate="status",
            value="Completed (cutover on Oct 8)",
            value_norm="done",
            spoken=("Falcon is done", say_date(day(6, THU))),
            bullet="Falcon cutover completed Oct 8",
        )

    handlers["w7-standup"] = w7_standup
    standup(7)

    m = s.meeting(
        "w7-security",
        "Security review: Teams",
        "review",
        7,
        TUE,
        14,
        45,
        ["yusuf", "dev", "sam_p", "alex_k"],
        folder="Engineering",
    )
    _open(s, m, "dev")
    c = facts["c_questionnaire"]
    assert isinstance(c, Commitment)
    b = m.say(
        "yusuf",
        "First, the Corvid questionnaire went back last Friday.",
        "Corvid questionnaire went back",
    )
    s.resolve(c, m, [b], resolved_on=m.day)
    b = m.say(
        "yusuf",
        "The finding is SSO session tokens aren't expiring. That's a blocker for launch.",
        "SSO session tokens aren't expiring",
    )
    s.fact(
        m,
        [b],
        kind="status",
        subject="teams",
        predicate="security_finding",
        value="SSO session tokens don't expire (launch blocker)",
        value_norm="open",
        spoken=("SSO session tokens aren't expiring",),
        bullet="Blocker: SSO session tokens don't expire",
    )
    p = m.say("dev", "Sam, it's your code, can you fix it?", "Sam")
    b = m.say(
        "sam_p",
        f"Yeah, token expiry fix by {say_date(day(8, WED))}.",
        "token expiry fix",
        say_date(day(8, WED)),
    )
    facts["c_token_fix"] = s.commit(
        m,
        [p, b],
        owner="sam_p",
        task="fix SSO session token expiry",
        task_spoken="token expiry fix",
        due=day(8, WED),
        outcome="kept",
    )
    _close(s, m, "dev")

    m = s.meeting(
        "w7-launch-sync",
        "Teams launch sync",
        "sync",
        7,
        WED,
        11,
        30,
        [*LAUNCH, "yusuf"],
        folder="Leadership",
    )
    _open(s, m, "marcus")
    b = m.say(
        "dev",
        "Yusuf found that SSO session tokens aren't expiring, and we're not "
        "shipping that to a bank.",
        "session tokens aren't expiring",
    )
    b2 = m.say(
        "marcus", f"Agreed. We move launch to {say_date(day(11, WED))}.", say_date(day(11, WED))
    )
    facts["launch3"] = s.fact(
        m,
        [b, b2],
        kind="date",
        subject="teams",
        predicate="launch_date",
        value="2026-11-11",
        value_norm="2026-11-11",
        spoken=(say_date(day(11, WED)),),
        reason="the security review found that SSO session tokens weren't expiring",
        reason_spoken=("session tokens aren't expiring",),
        bullet="Launch slips from Oct 28 to Nov 11 (SSO token expiry blocker)",
    )
    _close(s, m, "marcus")

    m = s.meeting(
        "w7-1on1-alex",
        "Priya / Alex 1:1",
        "one_on_one",
        7,
        THU,
        12,
        30,
        ["alex_k"],
        folder="1:1s",
        mode="in_person",
    )
    m.say("priya", "How's everything at home?")
    m.say(
        "alex_k",
        "Good, my mom's moving in for a bit to help once the baby comes.",
        "my mom's moving in",
        privacy="family",
    )
    b = m.say(
        "alex_k",
        "For my leave, Tomás will cover eng manager duties, we talked it through.",
        "Tomás will cover eng manager duties",
    )
    s.fact(
        m,
        [b],
        kind="ownership",
        subject="eng_manager_cover",
        predicate="owner",
        value="Tomás Herrera (during Alex's leave)",
        value_norm="tomas",
        spoken=("Tomás will cover eng manager duties",),
        bullet="Tomás covers eng manager duties during Alex's leave",
    )

    m = s.meeting(
        "w7-driftwood",
        "Driftwood Retail: renewal",
        "customer",
        7,
        FRI,
        17,
        30,
        ["jordan", "nadia", "luis"],
        folder="Driftwood Retail",
        platform="meet",
        customer="driftwood",
    )
    _open(s, m, "nadia")
    b = m.say(
        "luis",
        "We'll renew, but six months, not a year. Let's see how the new onboarding does.",
        "six months",
    )
    s.fact(
        m,
        [b],
        kind="deal",
        subject="driftwood",
        predicate="renewal_term",
        value="6 months",
        value_norm="6",
        spoken=("six months",),
        bullet="Driftwood renews for 6 months",
    )
    b = m.say(
        "luis",
        "And we need bulk user import, adding three hundred store staff by hand is not happening.",
        "bulk user import",
    )
    s.fact(
        m,
        [b],
        kind="request",
        subject="driftwood",
        predicate="request:bulk_import",
        value="Bulk user import (open)",
        value_norm="open",
        spoken=("bulk user import",),
        bullet="Driftwood needs bulk user import",
    )
    m.say(
        "luis",
        "Also I'll be out most of December, I'm having surgery, so loop in my deputy.",
        "having surgery",
        privacy="health",
    )
    _close(s, m, "jordan")

    # ---------------------------------------------------------------- week 8
    standup(8)

    m = s.meeting(
        "w8-corvid",
        "Corvid Bank: commercial",
        "customer",
        8,
        TUE,
        16,
        30,
        ["alex_r", "jordan", "felix", "mira"],
        folder="Corvid Bank",
        customer="corvid",
    )
    _open(s, m, "alex_r")
    b = m.say(
        "mira",
        f"We're a yes at {say_money(25)} a seat for the hundred and fifty "
        f"seats, pending one thing.",
        say_money(25),
        "hundred and fifty seats",
    )
    s.fact(
        m,
        [b],
        kind="deal",
        subject="corvid",
        predicate="deal_status",
        value="Verbal yes: 150 seats at $25/seat, pending pen test report",
        value_norm="verbal yes",
        spoken=(say_money(25), "hundred and fifty seats"),
        bullet="Corvid: verbal yes, 150 seats at $25",
    )
    b = m.say("mira", "We need your latest penetration test report.", "penetration test report")
    s.fact(
        m,
        [b],
        kind="request",
        subject="corvid",
        predicate="request:pen_test",
        value="Latest penetration test report (open)",
        value_norm="open",
        spoken=("penetration test report",),
        bullet="Corvid wants the latest pen test report",
    )
    _close(s, m, "jordan")

    m = s.meeting(
        "w8-allhands", "All hands", "all_hands", 8, WED, 17, 40, ALL_STAFF, folder="Company"
    )
    _open(s, m, "marcus")
    b = m.say("olivia", f"ARR is {say_thousands(371)}.", say_thousands(371))
    s.fact(
        m,
        [b],
        kind="metric",
        subject="northwind",
        predicate="arr",
        value="$371k",
        value_norm="371000",
        spoken=(say_thousands(371),),
        bullet="ARR: $371k",
    )
    b = m.say("hana", f"Signups were {say_int(590)} last month.", say_int(590))
    s.fact(
        m,
        [b],
        kind="metric",
        subject="northwind",
        predicate="monthly_signups",
        value="590",
        value_norm="590",
        spoken=(say_int(590),),
        bullet="Monthly signups: 590",
    )
    launch3 = facts["launch3"]
    assert isinstance(launch3, Fact)
    b = m.say(
        "marcus", f"And launch is locked for {say_date(day(11, WED))}.", say_date(day(11, WED))
    )
    s.confirm(launch3, m, [b])
    _close(s, m, "marcus")

    m = s.meeting(
        "w8-1on1-samo", "Priya / Sam 1:1", "one_on_one", 8, THU, 10, 30, ["sam_o"], folder="1:1s"
    )
    m.say(
        "priya",
        "Good news first. Lena and I agreed, you'll own the design system from November.",
        "you'll own the design system",
    )
    b = len(m.m.beats) - 1
    s.fact(
        m,
        [b],
        kind="ownership",
        subject="design_system",
        predicate="owner",
        value="Sam Okafor (from November)",
        value_norm="sam_o",
        spoken=("you'll own the design system",),
        bullet="Sam owns the design system from Nov",
    )
    m.say(
        "sam_o",
        "Amazing, thank you. And Lena said the senior promotion is going through, "
        f"to {say_thousands(140)}?",
        say_thousands(140),
        privacy="compensation",
    )
    m.say("priya", "Yes, effective next cycle.")

    m = s.meeting(
        "w8-launch-sync",
        "Teams launch sync",
        "sync",
        8,
        FRI,
        11,
        30,
        [*LAUNCH, "megan", "ruth", "sam_p"],
        folder="Leadership",
    )
    _open(s, m, "marcus")
    c = facts["c_token_fix"]
    assert isinstance(c, Commitment)
    b = m.say(
        "sam_p", "Token expiry fix is merged and Yusuf signed off.", "Token expiry fix is merged"
    )
    s.resolve(c, m, [b], resolved_on=m.day)
    b = m.say(
        "ruth", "Release checklist is green except load testing, that runs Monday.", "load testing"
    )
    s.fact(
        m,
        [b],
        kind="status",
        subject="teams",
        predicate="launch_readiness",
        value="Release checklist green except load testing (runs Monday)",
        value_norm="load testing",
        spoken=("load testing",),
        bullet="Launch readiness: all green except load testing",
    )
    _close(s, m, "marcus")

    if scale == "xl":
        from concentrate.synth.extend import extend_story

        extend_story(s)
    return World(
        seed=seed,
        owner="priya",
        start=day(1, MON),
        meetings=sorted((h.m for h in s.meetings), key=lambda mt: mt.start),
        facts=s.facts,
        commitments=s.commitments,
    )

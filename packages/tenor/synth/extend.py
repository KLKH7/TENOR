"""Northwind-XL: procedural meetings layered on the hand-authored plot (``scale="xl"``).

The 40 plot meetings keep every planted trap. This module adds ~145 more meetings over the same
8 weeks, enough that a Head of Product's calendar looks real and retrieval has to work. Each
family is a small seeded simulation that registers facts and commitments through the same
``Story`` builder, so every new question still has exact gold evidence:

* **project syncs** (6 projects x 8 weeks): status follows a Markov chain (on track / at risk /
  blocked); worsening status can slip the target date, with a stated reason; a weekly metric
  forms an 8-point time series; some projects change owner; commitments resolve next week.
* **customer calls** (8 customers x 3): asks that overlap the plot's (SSO, audit logs, CSV
  export), quoted prices near the plot's $25, deal stages that end signed, lost or pending.
* **1:1s** with 10 staff, **hiring debriefs**, a weekly **metrics review** that repeats the
  plot's launch date *as it stood that day*, and **support** and **marketing** syncs.

Nothing here uses wall-clock time or global randomness: all draws come from ``story.rng``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tenor.core.spoken import say_date, say_int, say_money
from tenor.synth.roster import ORG_BY_ID, XL_ORGS, person
from tenor.synth.story import M, Story, day
from tenor.synth.world import Commitment, Fact, MeetingMode, Outcome

MON, TUE, WED, THU, FRI = range(5)
WEEKS = range(1, 9)
WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# -- shared helpers -------------------------------------------------------------------------


def value_at(s: Story, subject: str, predicate: str, d: date) -> Fact | None:
    """The version of (subject, predicate) that was true on ``d`` (plot or extension)."""
    live = [
        f for f in s.facts
        if f.subject == subject and f.predicate == predicate and f.valid_from <= d
        and (f.valid_to is None or d < f.valid_to)
    ]  # fmt: skip
    return live[-1] if live else None


def _outcome(s: Story, last_week: bool) -> Outcome:
    if last_week:
        return "open"
    r = s.rng.random()
    return "kept" if r < 0.5 else "slipped" if r < 0.7 else "dropped" if r < 0.85 else "reassigned"


class _Commitments:
    """Commitments made in one recurring series, resolved at that series' next meeting."""

    def __init__(self, s: Story, pool: list[str]) -> None:
        self.s, self.pool = s, list(pool)
        s.rng.shuffle(self.pool)
        self.pending: list[Commitment] = []

    def resolve_due(self, m: M, people: list[str]) -> None:
        s = self.s
        for c in self.pending:
            who = s.first(c.owner)
            if c.outcome == "kept":
                b = m.say(c.owner, f"The {c.task_spoken} is done.", c.task_spoken)
                s.resolve(c, m, [b], resolved_on=m.day)
            elif c.outcome == "slipped":
                b = m.say(
                    c.owner,
                    f"The {c.task_spoken} slipped, I'll get it done next week.",
                    c.task_spoken,
                )
                s.resolve(c, m, [b], new_due=day(_week(m) + 1, FRI) if _week(m) < 8 else c.due)
            elif c.outcome == "reassigned":
                others = [p for p in people if p not in (c.owner, "priya")]
                if not others:  # nobody to hand it to (e.g. a 1:1): it just never comes up
                    c.outcome = "dropped"
                    continue
                new = s.rng.choice(others)
                b = m.say(
                    "priya",
                    f"{who} is swamped, so {s.first(new)} will take the {c.task_spoken}.",
                    who,
                    s.first(new),
                    c.task_spoken,
                )
                s.resolve(c, m, [b], new_owner=new)
            # dropped: silence
        self.pending = []

    def make(self, m: M, owner: str, last_week: bool) -> None:
        if not self.pool:
            return
        s = self.s
        task = self.pool.pop()
        # "growth plan draft for Elena" stays the task (unique across 1:1s), but Elena says
        # "the growth plan draft", not "the growth plan draft for Elena".
        spoken = task.removesuffix(f" for {s.first(owner)}")
        due_day = s.rng.choice([WED, THU, FRI])
        due = day(_week(m), due_day) if due_day > m.day.weekday() else day(_week(m) + 1, due_day)
        wd = WEEKDAY[due.weekday()]
        b = m.say(owner, f"I'll take the {spoken}, done by {wd}.", spoken, f"by {wd}")
        c = s.commit(
            m,
            [b],
            owner=owner,
            task=task,
            task_spoken=spoken,
            due=due,
            outcome=_outcome(s, last_week),
        )
        if c.outcome != "dropped":
            self.pending.append(c)


def _week(m: M) -> int:
    return (m.day - day(1, MON)).days // 7 + 1


def _open(s: Story, m: M, who: str) -> None:
    m.say(
        who,
        s.pick(
            [
                "Okay, let's get started.",
                "Hey all, quick one today.",
                "Morning. Let's go through it.",
                "Thanks for joining.",
            ]
        ),
    )


def _close(s: Story, m: M, who: str) -> None:
    m.say(who, s.pick(["Great, thanks everyone.", "Okay, that's it.", "Cool, talk next week."]))


# -- project syncs --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectCfg:
    pid: str
    name: str
    owner: str
    team: tuple[str, ...]
    metric: str  # spoken label, e.g. "open permission bugs"
    unit: str  # "" or "percent"
    start: int
    step: int
    weekday: int
    hour: float
    handoff: tuple[int, str, str] | None  # (week, new owner, reason spoken)
    tasks: tuple[str, ...]
    launch_linked: bool = False  # mentions the Teams launch date as it stands


PROJECT_CFGS = (
    ProjectCfg(
        "kite",
        "Kite",
        "megan",
        ("ruth", "alex_k", "sam_o"),
        "open permission bugs",
        "",
        42,
        -5,
        TUE,
        10,
        (5, "ruth", "Megan moved onto the Teams launch"),
        (
            "role editor mockups",
            "permissions audit script",
            "admin role migration",
            "permissions test plan",
            "Kite rollout checklist",
            "role cache fix",
        ),
        launch_linked=True,
    ),
    ProjectCfg(
        "atlas",
        "Atlas",
        "kofi",
        ("grace", "tomas", "dev"),
        "tables migrated",
        "percent",
        10,
        11,
        WED,
        14,
        None,
        (
            "Atlas cutover runbook",
            "row count reconciliation",
            "warehouse cost report",
            "dbt model cleanup",
            "Atlas access review",
            "query latency benchmark",
        ),
    ),
    ProjectCfg(
        "juniper",
        "Juniper",
        "sam_p",
        ("megan", "carla", "alex_r"),
        "email templates rewritten",
        "",
        3,
        2,
        THU,
        11,
        (6, "tomas", "Sam Patel is fully on the Corvid SSO work"),
        (
            "invoice email copy",
            "dunning email sequence",
            "email preview tool",
            "receipt template",
            "Juniper send schedule",
            "unsubscribe handling",
        ),
    ),
    ProjectCfg(
        "harbor_hc",
        "Harbor",
        "carla",
        ("hana", "nadia", "sam_o"),
        "help articles rewritten",
        "",
        20,
        14,
        MON,
        15,
        None,
        (
            "help center navigation",
            "top ten articles",
            "search synonyms list",
            "Harbor launch post",
            "article feedback widget",
            "screenshots refresh",
        ),
    ),
    ProjectCfg(
        "sparrow",
        "Sparrow",
        "ines",
        ("theo", "grace", "sam_o"),
        "test completion rate",
        "percent",
        52,
        3,
        FRI,
        10,
        None,
        (
            "Sparrow variant B build",
            "experiment dashboard",
            "sample size calculation",
            "Sparrow analytics events",
            "variant copy review",
            "test readout doc",
        ),
    ),
    ProjectCfg(
        "meridian",
        "Meridian",
        "alex_k",
        ("yusuf", "felix", "megan"),
        "admin features done",
        "",
        2,
        1,
        TUE,
        15,
        (4, "yusuf", "Alex Kim is preparing for parental leave"),
        (
            "SSO settings page",
            "audit log viewer",
            "seat export",
            "Meridian beta list",
            "admin API keys",
            "billing contacts page",
        ),
        launch_linked=True,
    ),
)

_STATUS_NEXT = {  # Markov transition probabilities
    "on track": [("on track", 0.75), ("at risk", 0.25)],
    "at risk": [("on track", 0.4), ("at risk", 0.4), ("blocked", 0.2)],
    "blocked": [("at risk", 0.5), ("blocked", 0.5)],
}
_RISK_REASONS = [
    "a dependency on the infra team slipped",
    "we found a data migration bug",
    "two engineers were out sick",
    "the design changed late",
    "a customer escalation pulled people away",
    "QA found regressions",
]


def _next_status(s: Story, current: str) -> str:
    r, acc = s.rng.random(), 0.0
    for status, p in _STATUS_NEXT[current]:
        acc += p
        if r < acc:
            return status
    return current


def _metric_spoken(cfg: ProjectCfg, value: int) -> str:
    return f"{say_int(value)} percent" if cfg.unit == "percent" else say_int(value)


def _project(s: Story, cfg: ProjectCfg) -> None:
    owner, status = cfg.owner, "on track"
    target = day(s.rng.choice([10, 11, 12]), FRI)
    commitments = _Commitments(s, list(cfg.tasks))
    value = cfg.start
    for w in WEEKS:
        if cfg.handoff and w == cfg.handoff[0]:
            pass  # handled below, after the meeting exists (owner changes this week)
        team = [owner, *(t for t in cfg.team if t != owner)]
        if cfg.handoff and w >= cfg.handoff[0] and cfg.handoff[1] not in team:
            team.append(cfg.handoff[1])
        m = s.meeting(
            f"x-{cfg.pid}-w{w}",
            f"{cfg.name} weekly sync",
            "planning",
            w,
            cfg.weekday,
            cfg.hour,
            30,
            team,
            folder="Projects",
        )
        _open(s, m, "priya")
        commitments.resolve_due(m, team)
        first = s.first(owner)
        if w == 1:
            p = m.say("priya", f"{first}, you're driving {cfg.name}, right?", first, cfg.name)
            b = m.say(owner, f"Yep, {cfg.name} is mine.", f"{cfg.name} is mine")
            s.fact(
                m,
                [p, b],
                kind="ownership",
                subject=cfg.pid,
                predicate="owner",
                value=person(owner).name,
                value_norm=owner,
                spoken=(first, f"{cfg.name} is mine"),
                bullet=f"{first} owns {cfg.name}",
            )
            b = m.say(
                owner, f"We're targeting {say_date(target)} to ship {cfg.name}.", say_date(target)
            )
            s.fact(
                m,
                [b],
                kind="date",
                subject=cfg.pid,
                predicate="target_date",
                value=target.isoformat(),
                value_norm=target.isoformat(),
                spoken=(say_date(target),),
                bullet=f"{cfg.name} target: {target:%b %d}",
            )
            b = m.say(owner, f"Overall {cfg.name} is on track.", f"{cfg.name} is on track")
            s.fact(
                m,
                [b],
                kind="status",
                subject=cfg.pid,
                predicate="status",
                value="on track",
                value_norm="on track",
                spoken=(f"{cfg.name} is on track",),
                bullet=f"{cfg.name}: on track",
            )
        else:
            if cfg.handoff and w == cfg.handoff[0]:
                new, reason = cfg.handoff[1], cfg.handoff[2]
                b = m.say(
                    "priya",
                    f"Heads up: {reason}, so {s.first(new)} owns {cfg.name} from today.",
                    reason,
                    f"{s.first(new)} owns {cfg.name}",
                )
                s.fact(
                    m,
                    [b],
                    kind="ownership",
                    subject=cfg.pid,
                    predicate="owner",
                    value=person(new).name,
                    value_norm=new,
                    spoken=(f"{s.first(new)} owns {cfg.name}",),
                    reason=reason,
                    reason_spoken=(reason,),
                    bullet=f"{cfg.name} handed from {first} to {s.first(new)}",
                )
                owner = new
            new_status = _next_status(s, status)
            if new_status != status:
                reason = (
                    s.rng.choice(_RISK_REASONS)
                    if new_status != "on track"
                    else "the blocker is cleared"
                )
                b = m.say(
                    owner,
                    f"Status change: {cfg.name} is now {new_status}, because {reason}.",
                    f"{cfg.name} is now {new_status}",
                    reason,
                )
                s.fact(
                    m,
                    [b],
                    kind="status",
                    subject=cfg.pid,
                    predicate="status",
                    value=new_status,
                    value_norm=new_status,
                    spoken=(f"{cfg.name} is now {new_status}",),
                    reason=reason,
                    reason_spoken=(reason,),
                    bullet=f"{cfg.name}: {new_status} ({reason})",
                )
                if new_status in ("at risk", "blocked") and s.rng.random() < 0.5:
                    target = target.fromordinal(target.toordinal() + 7 * s.rng.choice([1, 2]))
                    b = m.say(
                        "priya",
                        f"Then let's move the {cfg.name} target to {say_date(target)}.",
                        say_date(target),
                    )
                    s.fact(
                        m,
                        [b],
                        kind="date",
                        subject=cfg.pid,
                        predicate="target_date",
                        value=target.isoformat(),
                        value_norm=target.isoformat(),
                        spoken=(say_date(target),),
                        reason=reason,
                        reason_spoken=(),
                        bullet=f"{cfg.name} target moved to {target:%b %d}",
                    )
                status = new_status
        prev, value = value, max(0, value + cfg.step + s.rng.choice([-2, -1, 0, 1, 2]))
        if value == prev:
            value += 1
        if cfg.unit == "percent":
            value = min(value, 99)
        spoken = _metric_spoken(cfg, value)
        b = m.say(owner, f"On numbers, we're at {spoken} {cfg.metric} this week.", spoken)
        s.fact(
            m,
            [b],
            kind="metric",
            subject=cfg.pid,
            predicate=f"metric:{cfg.metric}",
            value=f"{value}{'%' if cfg.unit == 'percent' else ''}",
            value_norm=str(value),
            spoken=(spoken,),
            bullet=f"{cfg.name}: {value} {cfg.metric}",
        )
        if cfg.launch_linked and w % 2 == 0:
            launch = value_at(s, "teams", "launch_date", m.day)
            if launch is not None:
                spoken_date = launch.spoken[0]
                b = m.say(
                    "priya",
                    f"Reminder, this has to land before the Teams launch on {spoken_date}.",
                    spoken_date,
                )
                s.confirm(launch, m, [b])
        doer = s.rng.choice(team)
        commitments.make(m, doer, last_week=w == 8)
        _close(s, m, "priya")


# -- customer calls -------------------------------------------------------------------------

_ASKS = [("sso", "SSO"), ("audit_logs", "audit logs"), ("csv_export", "a CSV export of usage"),
         ("slack", "a Slack integration"), ("eu_residency", "data stored in the EU"),
         ("scim", "SCIM provisioning"), ("hipaa", "a HIPAA BAA"),
         ("bulk_import", "bulk user import")]  # fmt: skip
_LOSS_REASONS = ["they went with a competitor", "their budget was cut", "the timing didn't work"]
_AES = ["alex_r", "jordan", "felix"]


def _customer(s: Story, org_id: str) -> None:
    org = ORG_BY_ID[org_id]
    contact = org.contacts[0].id
    ae = s.rng.choice(_AES)
    weeks = sorted(s.rng.sample(list(WEEKS), 3))
    asks = s.rng.sample(_ASKS, 3)
    seats = s.rng.choice([30, 45, 60, 80, 120, 200])
    price = s.rng.choice([18, 22, 25, 30])
    for i, w in enumerate(weeks):
        m = s.meeting(
            f"x-{org_id}-{i + 1}",
            f"{org.name} call",
            "customer",
            w,
            s.rng.choice([MON, TUE, WED, THU, FRI]),
            s.rng.choice([13, 15, 16]),
            30,
            [ae, contact],
            folder=org.name,
            customer=org_id,
            platform=s.rng.choice(["zoom", "meet"]),
        )
        _open(s, m, ae)
        c_first = s.first(contact)
        if i == 0:
            p = m.say(ae, f"{c_first}, what matters most for your team?", c_first)
            for topic, phrase in asks[:2]:
                b = m.say(contact, f"We'd need {phrase} before rolling out.", phrase)
                s.fact(
                    m,
                    [p, b] if topic == asks[0][0] else [b],
                    kind="request",
                    subject=org_id,
                    predicate=f"request:{topic}",
                    value=f"{phrase} (open)",
                    value_norm="open",
                    spoken=(phrase,),
                    bullet=f"{org.name} needs {phrase}",
                )
            b = m.say(
                contact,
                f"We're looking at about {say_int(seats)} seats.",
                f"{say_int(seats)} seats",
            )
            s.fact(
                m,
                [b],
                kind="deal",
                subject=org_id,
                predicate="seats",
                value=f"{seats} seats",
                value_norm=str(seats),
                spoken=(f"{say_int(seats)} seats",),
                bullet=f"{org.name}: ~{seats} seats",
            )
            b = m.say(ae, f"For that size we'd quote {say_money(price)} a seat.", say_money(price))
            s.fact(
                m,
                [b],
                kind="deal",
                subject=org_id,
                predicate="quoted_price",
                value=f"${price} per seat per month",
                value_norm=str(price),
                spoken=(say_money(price),),
                bullet=f"Quoted {org.name} ${price}/seat",
            )
        elif i == 1:
            topic, phrase = asks[0]
            b = m.say(
                ae, f"Good news, {phrase} is now available for you.", f"{phrase} is now available"
            )
            s.fact(
                m,
                [b],
                kind="request",
                subject=org_id,
                predicate=f"request:{topic}",
                value=f"{phrase} (delivered)",
                value_norm="done",
                spoken=(f"{phrase} is now available",),
                bullet=f"{org.name}: {phrase} delivered",
            )
            topic, phrase = asks[2]
            b = m.say(contact, f"One more thing, we'll also need {phrase}.", phrase)
            s.fact(
                m,
                [b],
                kind="request",
                subject=org_id,
                predicate=f"request:{topic}",
                value=f"{phrase} (open)",
                value_norm="open",
                spoken=(phrase,),
                bullet=f"{org.name} also needs {phrase}",
            )
            b = m.say(contact, "We'll start a trial with one team.", "start a trial")
            s.fact(
                m,
                [b],
                kind="deal",
                subject=org_id,
                predicate="deal_status",
                value="trial",
                value_norm="trial",
                spoken=("start a trial",),
                bullet=f"{org.name} starting a trial",
            )
        else:
            r = s.rng.random()
            if r < 0.5:
                b = m.say(contact, "We're ready to sign for the full rollout.", "ready to sign")
                s.fact(
                    m,
                    [b],
                    kind="deal",
                    subject=org_id,
                    predicate="deal_status",
                    value="signed",
                    value_norm="signed",
                    spoken=("ready to sign",),
                    bullet=f"{org.name} signed",
                )
            elif r < 0.8:
                reason = s.rng.choice(_LOSS_REASONS)
                b = m.say(
                    contact, f"We're not moving forward, {reason}.", "not moving forward", reason
                )
                s.fact(
                    m,
                    [b],
                    kind="deal",
                    subject=org_id,
                    predicate="deal_status",
                    value=f"lost ({reason})",
                    value_norm="lost",
                    spoken=("not moving forward",),
                    reason=reason,
                    reason_spoken=(reason,),
                    bullet=f"{org.name} lost: {reason}",
                )
            else:
                b = m.say(contact, "We need another month to decide.", "another month to decide")
                s.fact(
                    m,
                    [b],
                    kind="deal",
                    subject=org_id,
                    predicate="deal_status",
                    value="undecided (needs another month)",
                    value_norm="undecided",
                    spoken=("another month to decide",),
                    bullet=f"{org.name} undecided",
                )
        _close(s, m, ae)


# -- 1:1s, hiring, reviews, recurring syncs -------------------------------------------------

_ONE_ON_ONE = ["megan", "tomas", "ines", "kofi", "ruth", "elena", "lena", "hana", "nadia", "theo"]
_GOALS = [
    "lead a project end to end",
    "mentor a new hire",
    "speak at a conference",
    "move into management",
    "go deeper on data",
    "own a customer relationship",
]
_CANDIDATES = [
    "Leo Marsh",
    "Nina Patel",
    "Omar Haddad",
    "Zoe Grant",
    "Kai Mensah",
    "Iris Vale",
    "Ravi Sen",
    "Lucy Park",
]


def _one_on_ones(s: Story) -> None:
    for pid in _ONE_ON_ONE:
        weeks = [1, 3, 5, 7] if s.rng.random() < 0.5 else [2, 4, 6, 8]
        tasks = _Commitments(
            s,
            [
                f"{t} for {s.first(pid)}"
                for t in (
                    "growth plan draft",
                    "peer feedback list",
                    "skills matrix",
                    "project retro notes",
                    "onboarding doc review",
                )
            ],
        )
        for i, w in enumerate(weeks):
            mode: MeetingMode = "in_person" if s.rng.random() < 0.2 else "remote"
            m = s.meeting(
                f"x-1on1-{pid}-{i + 1}",
                f"Priya / {s.first(pid)} 1:1",
                "one_on_one",
                w,
                s.rng.choice([MON, TUE, WED, THU, FRI]),
                s.rng.choice([9.5, 11.5, 16.5]),
                30,
                [pid],
                folder="1:1s",
                mode=mode,
            )
            m.say("priya", "How's your week going?")
            tasks.resolve_due(m, [pid])
            if i == 0:
                goal = s.rng.choice(_GOALS)
                b = m.say(pid, f"This half, I want to {goal}.", goal)
                s.fact(
                    m,
                    [b],
                    kind="attribute",
                    subject=pid,
                    predicate="goal",
                    value=goal,
                    value_norm=goal,
                    spoken=(goal,),
                    bullet=f"{s.first(pid)} wants to {goal}",
                )
            tasks.make(m, pid, last_week=i == len(weeks) - 1)


def _hiring(s: Story) -> None:
    pool = list(_CANDIDATES)
    s.rng.shuffle(pool)
    in_process: list[str] = []
    for w in WEEKS:
        m = s.meeting(
            f"x-hiring-w{w}",
            "Hiring debrief",
            "decision",
            w,
            THU,
            17,
            30,
            ["alex_k", "dev", "ben"],
            folder="Hiring",
        )
        _open(s, m, "ben")
        for name in list(in_process):
            decision = s.rng.choice(["offer", "no hire"])
            phrase = "make an offer" if decision == "offer" else "pass"
            b = m.say("alex_k", f"For {name}, after the final round we'll {phrase}.", name, phrase)
            s.fact(
                m,
                [b],
                kind="decision",
                subject=f"candidate:{name}",
                predicate="decision",
                value=decision,
                value_norm=decision,
                spoken=(name, phrase),
                bullet=f"{name}: {decision}",
            )
            in_process.remove(name)
        if pool and w < 8:
            name = pool.pop()
            b = m.say(
                "dev", f"{name} did well, let's move them to the final round.", name, "final round"
            )
            s.fact(
                m,
                [b],
                kind="decision",
                subject=f"candidate:{name}",
                predicate="decision",
                value="final round",
                value_norm="final round",
                spoken=(name, "final round"),
                bullet=f"{name} to final round",
            )
            in_process.append(name)
        _close(s, m, "ben")


def _metrics_reviews(s: Story) -> None:
    wat, activation, nps = 140, 31, 38
    for w in WEEKS:
        m = s.meeting(
            f"x-metrics-w{w}",
            "Weekly metrics review",
            "review",
            w,
            THU,
            9,
            30,
            ["marcus", "olivia", "hana", "theo", "grace"],
            folder="Leadership",
        )
        _open(s, m, "marcus")
        wat += s.rng.choice([6, 9, 12, 15])
        activation += s.rng.choice([-1, 1, 2])
        nps += s.rng.choice([-2, 1, 3])
        for subject_pred, val, spoken, label in [
            ("weekly_active_teams", wat, say_int(wat), "weekly active teams"),
            ("activation_rate", activation, f"{say_int(activation)} percent", "activation"),
            ("nps", nps, say_int(nps), "NPS"),
        ]:
            speaker = "grace" if subject_pred != "nps" else "hana"
            b = m.say(speaker, f"{label.capitalize()} is at {spoken} this week.", spoken)
            prev = value_at(s, "northwind", subject_pred, m.day)
            if prev is not None and prev.value_norm == str(val):
                s.confirm(prev, m, [b])
            else:
                s.fact(
                    m,
                    [b],
                    kind="metric",
                    subject="northwind",
                    predicate=subject_pred,
                    value=f"{val}{'%' if 'percent' in spoken else ''}",
                    value_norm=str(val),
                    spoken=(spoken,),
                    bullet=f"{label}: {val}",
                )
        launch = value_at(s, "teams", "launch_date", m.day)
        if launch is not None:
            b = m.say(
                "marcus", f"And the Teams launch is set for {launch.spoken[0]}.", launch.spoken[0]
            )
            s.confirm(launch, m, [b])
        _close(s, m, "marcus")


def _support_syncs(s: Story) -> None:
    backlog = 64
    for w in WEEKS:
        m = s.meeting(
            f"x-support-w{w}",
            "Support triage",
            "sync",
            w,
            MON,
            14,
            30,
            ["carla", "nadia", "ruth"],
            folder="Support",
        )
        _open(s, m, "carla")
        backlog = max(5, backlog + s.rng.choice([-9, -5, -2, 3, 6]))
        b = m.say("carla", f"The ticket backlog is {say_int(backlog)} this week.", say_int(backlog))
        prev = value_at(s, "support", "ticket_backlog", m.day)
        if prev is not None and prev.value_norm == str(backlog):
            s.confirm(prev, m, [b])
        else:
            s.fact(
                m,
                [b],
                kind="metric",
                subject="support",
                predicate="ticket_backlog",
                value=str(backlog),
                value_norm=str(backlog),
                spoken=(say_int(backlog),),
                bullet=f"Support backlog: {backlog}",
            )
        _close(s, m, "carla")


def _marketing_syncs(s: Story) -> None:
    for w in WEEKS:
        m = s.meeting(
            f"x-marketing-w{w}",
            "Marketing sync",
            "sync",
            w,
            WED,
            11,
            30,
            ["hana", "theo", "elena"],
            folder="Marketing",
        )
        _open(s, m, "hana")
        launch = value_at(s, "teams", "launch_date", m.day)
        if launch is not None and w % 2 == 1:
            b = m.say(
                "elena", f"Launch content is planned around {launch.spoken[0]}.", launch.spoken[0]
            )
            s.confirm(launch, m, [b])
        _close(s, m, "hana")


def extend_story(s: Story) -> None:
    """Add the Northwind-XL meeting families to a story that already holds the plot."""
    for cfg in PROJECT_CFGS:
        _project(s, cfg)
    for org in XL_ORGS:
        _customer(s, org.id)
    _one_on_ones(s)
    _hiring(s)
    _metrics_reviews(s)
    _support_syncs(s)
    _marketing_syncs(s)


XL_FOLDERS: list[tuple[str, str | None]] = [
    ("Projects", None), ("Hiring", None), ("Support", None), ("Marketing", None),
    *((o.name, "Customers") for o in XL_ORGS),
]  # fmt: skip

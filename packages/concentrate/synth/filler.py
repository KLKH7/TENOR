"""Compositional, fact-free filler speech for padding meetings (generator northwind-2).

v1 drew filler from 69 fixed sentences, so 78% of XL passages of 6+ words were exact
duplicates and retrieval was unrealistically easy (docs/benchmark/FIDELITY.md). Here filler is
composed
from templates whose slots are filled from vocabulary specific to the meeting type, the
projects and customers the meeting is about, and generic workplace speech. The combinatorics
give effectively no repeated sentences, and the vocabulary is large enough for a realistic
type/token ratio.

Filler must stay fact-free (``synth.polish`` enforces the same for LLM rewrites): no people's
names, no dates or deadlines, no prices or metrics, no owner + deadline commitments. It may name
projects and customers, which is what makes it a realistic distractor for retrieval.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

# Noun phrases each meeting group talks around. Projects/customers are added per meeting.
NOUNS: dict[str, list[str]] = {
    "eng": [
        "the retry logic",
        "the staging deploy",
        "the flaky integration tests",
        "the rollback plan",
        "the queue consumer",
        "the feature flag cleanup",
        "the on-call handoff",
        "the rate limiter",
        "the schema migration",
        "the CI pipeline",
        "the error budget",
        "the alerting thresholds",
        "the caching layer",
        "the webhook handler",
        "the auth middleware",
        "the build times",
        "the dependency upgrade",
        "the load test",
        "the logging format",
        "the backfill job",
        "the API versioning",
        "the database indexes",
        "the release checklist",
        "the canary setup",
        "the config drift",
        "the dead letter queue",
        "the cron jobs",
        "the connection pool",
        "the test fixtures",
        "the monorepo tooling",
        "the runbook",
        "the dashboards",
        "the incident review",
        "the pagination bug",
        "the timeout settings",
        "the code owners file",
        "the linting rules",
        "the preview environments",
        "the secrets rotation",
        "the batch size",
        "the read replicas",
        "the deploy freeze",
        "the service boundaries",
        "the tracing setup",
    ],
    "customer": [
        "the rollout plan",
        "the admin console",
        "the SSO setup",
        "the renewal conversation",
        "the pilot group",
        "the training sessions",
        "the security questionnaire",
        "the support tickets",
        "the reporting export",
        "the integration with their HR system",
        "the onboarding calls",
        "the champion on their side",
        "the procurement steps",
        "the contract redlines",
        "the usage reports",
        "the permissions model",
        "the data residency question",
        "the invoicing setup",
        "the mobile adoption",
        "the feedback from their managers",
        "the change management piece",
        "the kickoff deck",
        "the success plan",
        "the escalation path",
        "the legal review",
        "the sandbox account",
        "the user provisioning",
        "the email digests",
        "the regional teams",
        "the executive sponsor",
        "the help center articles",
        "the trial period",
        "the bulk import",
        "the audit trail",
        "the notification settings",
        "the roadmap call",
    ],
    "design": [
        "the empty state",
        "the onboarding flow",
        "the nav",
        "the color tokens",
        "the settings page",
        "the illustration style",
        "the button hierarchy",
        "the error messages",
        "the mobile layout",
        "the type scale",
        "the icon set",
        "the modal pattern",
        "the table density",
        "the dark mode pass",
        "the prototype",
        "the usability sessions",
        "the copy on that screen",
        "the loading states",
        "the spacing",
        "the component library",
        "the search results page",
        "the first-run experience",
        "the tooltip pattern",
        "the dashboard cards",
        "the filter bar",
        "the accessibility audit",
        "the design critique",
        "the handoff to eng",
        "the motion specs",
        "the avatar treatment",
        "the form validation",
        "the breadcrumbs",
        "the pricing page",
    ],
    "leadership": [
        "the hiring plan",
        "the board update",
        "the budget",
        "the org chart",
        "the quarterly goals",
        "the messaging",
        "the competitive landscape",
        "the pricing story",
        "the sales pipeline",
        "the churn analysis",
        "the narrative for the next raise",
        "the team structure",
        "the headcount ask",
        "the operating cadence",
        "the planning doc",
        "the offsite agenda",
        "the partner channel",
        "the brand refresh",
        "the customer advisory board",
        "the support load",
        "the margin question",
        "the positioning",
        "the enterprise motion",
        "the roadmap tradeoffs",
        "the investor update",
        "the culture survey",
        "the comp bands",
    ],
    "one_on_one": [
        "the workload",
        "the growth plan",
        "the feedback from the last cycle",
        "the promo packet",
        "the team dynamic",
        "the meeting load",
        "deep work time",
        "the mentoring thing",
        "the conference talk",
        "the side project",
        "the review process",
        "my focus areas",
        "the interview loop",
        "the on-call load",
        "the manager track",
        "the reading group",
        "the stretch project",
        "context switching",
        "the onboarding buddy thing",
        "work life balance",
        "the career ladder",
        "the skip level",
        "the shadowing idea",
        "the retro format",
    ],
    "all_hands": [
        "the office move",
        "the benefits update",
        "the wins from this month",
        "the new hires",
        "the support team",
        "the customer stories",
        "the values refresh",
        "the team offsite",
        "the slides",
        "the Q and A",
        "the holiday schedule",
        "the hackathon",
        "the security training",
        "the expense policy",
        "the volunteering day",
        "the internal tools survey",
        "the onboarding week",
        "the parking situation",
        "the snack budget",
        "the recognition program",
        "the travel policy",
    ],
}
COMMON_NOUNS = [
    "the timeline",
    "the scope",
    "the doc",
    "the spreadsheet",
    "the open questions",
    "the dependencies",
    "the tradeoffs",
    "the edge cases",
    "the follow ups",
    "the next steps",
    "the risks",
    "the ownership question",
    "the priority list",
    "the feedback",
    "the backlog",
    "the process",
    "the handoff",
    "the communication plan",
    "the requirements",
    "the metrics",
    "the original proposal",
    "the Slack thread",
    "the notes from last time",
    "the action items",
]
STATES = [
    "mostly fine",
    "a bit messy",
    "kind of stuck",
    "in a weird spot",
    "pretty close",
    "not urgent",
    "way more complicated than it looks",
    "basically done",
    "still a mess",
    "fine for now",
    "better than last week",
    "a little fragile",
    "kind of a mystery",
    "on the critical path",
    "lower priority than we think",
    "a nice to have",
    "the blocker",
    "under control",
    "not where I'd want it",
    "half done",
    "overdue for a cleanup",
    "a known issue",
    "a moving target",
    "surprisingly okay",
    "hard to reason about",
    "in good shape",
    "a bit of a black box",
    "taking longer than expected",
    "sort of on hold",
    "pretty solid",
    "unclear",
    "fine in theory",
    "more of a people problem",
    "way overdue",
    "kind of fragile honestly",
    "not a big deal",
    "bigger than we scoped",
    "the easy part",
    "the scary part",
    "mostly cosmetic",
    "a rabbit hole",
    "good enough for now",
    "a little behind",
    "ahead of where I expected",
    "still pretty rough",
    "the obvious next thing",
    "low risk",
    "weirdly controversial",
]
OBSERVATIONS = [
    "nothing jumped out",
    "it looked pretty reasonable",
    "there's more going on than I thought",
    "I got kind of lost",
    "it's not as bad as it sounded",
    "it's mostly noise",
    "the numbers looked roughly flat",
    "people seem confused by it",
    "a couple of things looked off",
    "it's been quiet",
    "it was slower than I expected",
    "nobody's really touched it in a while",
    "there were way more edge cases than I expected",
    "it's mostly what we talked about",
    "the feedback was mixed",
    "it kind of works",
    "it's honestly fine",
    "I had more questions than answers",
    "it's a lot cleaner now",
    "there are a few loose ends",
    "I couldn't reproduce it",
    "it seems to come and go",
    "half of it is outdated",
    "we're doing it two different ways",
    "it happened two or three times",
    "it's better than it was",
    "the docs don't match reality",
]
WORRIES = [
    "that we're overthinking it",
    "that nobody really owns it",
    "that it slips again",
    "that we end up rebuilding it",
    "that it's a bigger change than it looks",
    "that customers notice before we do",
    "that we're solving the wrong problem",
    "that it creates more support load",
    "that we lose context",
    "that the scope keeps growing",
    "that we're too optimistic",
    "that it breaks something downstream",
    "that we bike-shed it",
    "the dependencies on other teams",
    "that we don't have enough signal yet",
    "that it's hard to roll back",
    "how it lands with people",
    "that it just sits there",
]
ACTIONS = [
    "simplify it",
    "write it down",
    "get more data",
    "timebox it",
    "just ship the smaller version",
    "loop in the other team",
    "do a quick spike",
    "split it into two pieces",
    "park it for now",
    "run it by support",
    "check with legal",
    "do a proper write-up",
    "pair on it",
    "look at it with fresh eyes",
    "start with the boring version",
    "agree on what done means",
    "set up a short sync",
    "cut the scope",
    "test it with a few users",
    "go back to the original plan",
    "sanity check the assumptions",
    "clean up the backlog",
    "document the decision",
    "keep it behind a flag",
    "revisit it next cycle",
    "get a second opinion",
    "do it properly",
    "map out the edge cases",
    "draw it on a whiteboard",
    "make it someone's job",
]
WHENS = [
    "yesterday",
    "this morning",
    "last week",
    "earlier",
    "over the weekend",
    "the other day",
    "before this call",
    "a while back",
    "on Friday",
    "after standup",
    "last night",
]
FEELINGS = [
    "a little nervous",
    "pretty good",
    "kind of torn",
    "not super excited",
    "cautiously optimistic",
    "a bit skeptical",
    "fine",
    "honestly relieved",
    "kind of on the fence",
    "more positive",
]
PEOPLE_REF = [
    "a couple of folks on support",
    "the platform team",
    "someone on sales",
    "the data folks",
    "a few customers",
    "the design team",
    "people on the other team",
    "the new folks",
    "a couple of the engineers",
    "our solutions engineer",
    "the finance side",
    "a few managers",
]
REACTIONS = [
    "were kind of lukewarm",
    "had a lot of opinions",
    "seemed into it",
    "had the same question",
    "didn't love it",
    "were surprised",
    "said it's been like that forever",
    "were pretty chill about it",
    "had some good ideas",
    "asked a bunch of questions",
    "pushed back a bit",
]
OPENERS = [
    "",
    "",
    "",
    "",
    "So ",
    "Yeah, ",
    "I think ",
    "Honestly, ",
    "Okay, so ",
    "I guess ",
    "From my side, ",
    "Right, and ",
    "Like, ",
    "Yeah, yeah, ",
    "And ",
    "But ",
    "Yeah, so ",
    "Yeah. ",
    "Okay yeah, ",
    "So yeah, ",
    "Right, so ",
    "Look, ",
    "Basically ",
    "To be fair, ",
    "For what it's worth, ",
    "Okay, ",
    "I feel like ",
    "Actually, ",
    "The way I see it, ",
    "Yeah, I think ",
    "Mm, ",
    "Well, ",
    "Sorry, ",
    "Oh, ",
    "Yeah no, ",
    "Real quick, ",
    "Just to add, ",
    "Funny enough, ",
]
TAILS = [
    "",
    "",
    "",
    "",
    "",
    ", if that makes sense",
    ", I think",
    ", honestly",
    ", for what it's worth",
    ", but yeah",
    ", which is fine",
    ", at least for now",
    ", kind of",
    ", or whatever",
    ", if that helps",
    ", to be honest",
    ", anyway",
    ", in my opinion",
    ", more or less",
    " and stuff",
    ", you know what I mean",
    ", I don't know",
    ", right",
    ", so yeah",
]
SHORT = [
    "Yeah.",
    "Mm-hm.",
    "Okay.",
    "Sure, yeah.",
    "Oh, nice.",
    "Right, right.",
    "Yeah, totally.",
    "Makes sense.",
    "Interesting.",
    "Oh, really?",
    "Fair.",
    "Uh-huh.",
    "Gotcha.",
    "Yeah, yeah.",
    "Cool.",
    "For sure.",
    "Nice.",
    "Hmm.",
    "Okay, cool.",
    "Oh, okay.",
    "True.",
    "Exactly.",
    "Yes.",
    "Yep, yep.",
    "No, totally.",
    "Ha, yeah.",
    "Wait, really?",
    "Oh no.",
    "Love that.",
    "Sounds good.",
    "Yeah, fair.",
    "Okay, that works.",
    "Right.",
    "Sure.",
    "Got it.",
    "Totally.",
    "Yeah, agreed.",
    "Okay, yeah.",
    "Mm.",
    "Oh, interesting.",
    "Yeah, I saw that.",
    "Same.",
    "That's fair.",
]
META = [
    "Sorry, you cut out for a second there.",
    "Can you see my screen?",
    "Let me share my screen.",
    "Wait, I think you're on mute.",
    "Sorry, go ahead.",
    "No, you go.",
    "Can everyone hear me okay?",
    "Let me pull up the doc.",
    "Hold on, my internet is being weird.",
    "Sorry, I had a dog barking.",
    "Is it just me or is the video lagging?",
    "Let me drop the link in the chat.",
    "Sorry, I was talking to myself on mute.",
    "Hang on, let me find it.",
    "I'll stop sharing.",
    "Sorry, someone's at the door.",
    "Can you make that bigger?",
    "You're breaking up a little.",
    "Sorry, my headphones died.",
    "Oh, I see it now.",
    "Sorry, which tab is that?",
    "Can you scroll down a bit?",
    "Hold on, I'm switching rooms.",
    "Is the recording on?",
    "Sorry, I'm getting an echo.",
    "Let me turn my camera on.",
    "I'll paste it in the chat.",
]
STATEMENTS = [
    "{op}{n} is {state}{tail}.",
    "{op}I was looking at {n} {when} and {obs}{tail}.",
    "{op}I feel like {n} is {state}{tail}.",
    "{op}we keep going back and forth on {n}{tail}.",
    "{op}for {t}, I think {n} is the part that's {state}.",
    "{op}I don't know if {n} is really the issue{tail}.",
    "{op}the thing I'd push on is {n}{tail}.",
    "{op}my worry with {n} is {worry}.",
    "{op}if we {act}, then {n} gets a lot easier.",
    "{op}I talked to {who} about {n} and they {react}.",
    "{op}on {t}, {obs}{tail}.",
    "{op}I'd rather {act} than {act2}{tail}.",
    "{op}{n} is {state}, but {n2} is {state2}.",
    "{op}{n} came up {when} too{tail}.",
    "{op}we should probably {act} before we touch {n}.",
    "{op}I'm {feel} about {n}{tail}.",
    "{op}it's less about {n} and more about {n2}{tail}.",
    "{op}I put some notes on {n} in the doc{tail}.",
    "{op}that's basically what we saw with {n} on {t}.",
    "{op}it's like, {n} is {state} and nobody wants to say it.",
    "{op}part of me wants to {act}, part of me thinks it's {state}.",
    "{op}{t} is kind of the test case for {n}{tail}.",
    "{op}I keep coming back to {n}{tail}.",
    "{op}when I looked at {n} for {t}, {obs}.",
    "{op}the short version is {n} is {state}{tail}.",
    "{op}I think the risk is {worry}{tail}.",
    "{op}we've been saying we'd {act} for a while{tail}.",
    "{op}it feels like {n} and {n2} are kind of the same problem.",
    "{op}nobody really owns {n} right now{tail}.",
    "{op}I wouldn't block on {n}{tail}.",
    "{op}I just want to make sure {n} doesn't fall through the cracks.",
    "{op}{who} mentioned {n} {when}, so it's on people's radar.",
    "{op}{n} took like {q} last time{tail}.",
    "{op}we lost {q} on {n} {when}{tail}.",
    "{op}I spent {q} on {n} and {obs}.",
    "{op}honestly {n} only needs {q} if we {act}.",
    "{op}if {n} slips again, {t} is going to feel it.",
    "{op}the last time we touched {n} it was {state}{tail}.",
    "{op}I'm not convinced {n} is {state}{tail}.",
    "{op}there's a world where we {act} and {n} just goes away.",
    "{op}I'd love to {act}, but {n} is {state}.",
    "{op}for {t} specifically, {n} is {state}{tail}.",
    "{op}{who} keep asking about {n}{tail}.",
    "{op}we said we'd {act} and then {n} happened.",
    "{op}the annoying thing about {n} is it's {state}.",
    "{op}I think {n} is {state} and {n2} is {state2}, so maybe {act}.",
    "{op}it's funny, {n} looked {state} {when} and now {obs}.",
]
QUANTITIES = [
    "ten minutes",
    "an hour",
    "two days",
    "a week or two",
    "fifteen minutes",
    "half a day",
    "three or four tries",
    "like five people",
    "a dozen tickets",
    "forty minutes",
    "two sprints",
    "ninety minutes",
    "a couple of hours",
    "twenty minutes",
    "about thirty tickets",
    "three weeks",
    "like fifteen calls",
    "most of the afternoon",
    "twelve steps",
    "eight screens",
]
QUESTIONS = [
    "{op}is {n} still {state}, or did that change?",
    "{op}what's the plan for {n}?",
    "{op}did anyone look at {n} {when}?",
    "{op}can we {act}?",
    "{op}where are we on {n} for {t}?",
    "{op}do we know who's looking at {n}?",
    "{op}does {n} actually matter for {t}?",
    "{op}should we just {act}?",
    "{op}what would it take to {act}?",
    "{op}are we worried about {n}?",
    "{op}how did {n} go?",
]
Q_OPENERS = [
    "",
    "",
    "",
    "So ",
    "Okay, so ",
    "Sorry, ",
    "Wait, ",
    "Quick question, ",
    "And ",
    "But ",
    "Real quick, ",
    "Actually, ",
    "Out of curiosity, ",
    "Yeah, and ",
    "Um, ",
]
CONNECTORS = ["And ", "But ", "So ", "Anyway, ", "And then ", "Which, ", "Plus ", "Also, ", ""]
GROUP = {
    "standup": "eng", "planning": "eng", "review": "eng", "customer": "customer",
    "design_review": "design", "decision": "leadership", "sync": "leadership",
    "investor": "leadership", "one_on_one": "one_on_one", "all_hands": "all_hands",
}  # fmt: skip


@dataclass(frozen=True)
class FillerContext:
    """What a meeting's filler may talk about: its group's nouns and its topic names."""

    group: str
    topics: tuple[str, ...]  # project / customer names relevant to this meeting


def _opener(template: str, rng: random.Random, lead: str | None) -> str:
    op = lead if lead is not None else rng.choice(Q_OPENERS if template in QUESTIONS else OPENERS)
    if template.startswith("{op}I") and any(x in op for x in ("I think", "I guess", "I feel")):
        return lead or ""
    return op


def _fill(template: str, ctx: FillerContext, rng: random.Random, lead: str | None) -> str:
    pool = NOUNS[ctx.group] + COMMON_NOUNS
    n, n2 = rng.sample(pool, 2)
    s1, s2 = rng.sample(STATES, 2)
    a1, a2 = rng.sample(ACTIONS, 2)
    topic = rng.choice(ctx.topics) if ctx.topics else "this"
    text = template.format(
        op=_opener(template, rng, lead), n=n, n2=n2, state=s1, state2=s2, act=a1, act2=a2, t=topic,
        when=rng.choice(WHENS), obs=rng.choice(OBSERVATIONS), worry=rng.choice(WORRIES),
        who=rng.choice(PEOPLE_REF), react=rng.choice(REACTIONS), feel=rng.choice(FEELINGS),
        tail=rng.choice(TAILS), q=rng.choice(QUANTITIES),
    )  # fmt: skip
    return text[0].upper() + text[1:]


def sentence(ctx: FillerContext, rng: random.Random, lead: str | None = None) -> str:
    """One composed statement or question; ``lead`` replaces the random opener."""
    templates = QUESTIONS if rng.random() < 0.1 else STATEMENTS
    return _fill(rng.choice(templates), ctx, rng, lead)


def turn(ctx: FillerContext, rng: random.Random, monologue_rate: float = 0.16) -> str:
    """One filler turn: a short reaction, a meta remark, a 1-2 sentence turn or a monologue.

    Monologues (3-8 sentences, ~40-150 words) give the long tail of passage lengths that real
    remote transcripts have (p90 ≈ 38 words) and v1 lacked.
    """
    r = rng.random()
    if r < 0.14:
        return rng.choice(SHORT)
    if r < 0.16:
        return rng.choice(META)
    n = rng.randint(3, 8) if rng.random() < monologue_rate / 0.84 else rng.randint(1, 2)
    parts = [sentence(ctx, rng)]
    for _ in range(n - 1):
        lead = rng.choice(CONNECTORS) if rng.random() < 0.5 else None
        parts.append(sentence(ctx, rng, lead=lead))
    return " ".join(parts)


def _alt(words: list[str]) -> str:
    return "(" + "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)) + ")"


_N = _alt([n for ns in NOUNS.values() for n in ns] + COMMON_NOUNS)
_POINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"my worry with {_N} is {_alt(WORRIES)}", re.I), "Concern with {0}: {1}"),
    (
        re.compile(rf"talked to {_alt(PEOPLE_REF)} about {_N} and they {_alt(REACTIONS)}", re.I),
        "Feedback on {1} from {0}: they {2}",
    ),
    (
        re.compile(rf"probably {_alt(ACTIONS)} before we touch {_N}", re.I),
        "Suggestion to {0} before touching {1}",
    ),
    (re.compile(rf"nobody really owns {_N}", re.I), "No clear owner for {0}"),
    (re.compile(rf"notes on {_N} in the doc", re.I), "Notes on {0} added to the doc"),
    (re.compile(rf"plan for {_N}", re.I), "Open question: what the plan is for {0}"),
    (re.compile(rf"looking at {_N} [a-z ]+? and {_alt(OBSERVATIONS)}", re.I), "{0}: {1}"),
    (
        re.compile(rf"less about {_N} and more about {_N}", re.I),
        "Real issue may be {1} rather than {0}",
    ),
    (
        re.compile(rf"{_N} and {_N} are kind of the same problem", re.I),
        "{0} and {1} seen as one underlying problem",
    ),
    (
        re.compile(rf"rather {_alt(ACTIONS)} than {_alt(ACTIONS)}", re.I),
        "Preference to {0} rather than {1}",
    ),
    (re.compile(rf"{_N} is {_alt(STATES)}", re.I), "{0} is {1}"),
]


_NOUN_SET = {n.lower() for ns in NOUNS.values() for n in ns} | {n.lower() for n in COMMON_NOUNS}


def summary_points(text: str) -> list[tuple[str, str]]:
    """(noun phrase, summary bullet) pairs a meeting summarizer could draw from filler ``text``.

    Bullets restate what was said, third person and without speakers, so a richer summary
    stays faithful to the transcript and fact-free. Text that no pattern matches (e.g. after an
    LLM polish) simply yields fewer points.
    """
    out: list[tuple[str, str]] = []
    for sent in re.split(r"(?<=[.?!])\s+", text):
        for rx, fmt in _POINTS:
            m = rx.search(sent)
            if m:
                groups = [
                    g.lower().removeprefix("the ") if g.lower() in _NOUN_SET else g.lower()
                    for g in m.groups()
                ]
                bullet = fmt.format(*groups)
                key = next((g.lower() for g in m.groups() if g.lower() in _NOUN_SET), "")
                out.append((key, bullet[0].upper() + bullet[1:]))
                break
    return out

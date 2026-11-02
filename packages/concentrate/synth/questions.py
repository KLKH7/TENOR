"""Benchmark questions generated from the ground-truth world, with gold evidence.

Every answer is computed from the world model, never typed in by hand, and carries:

* ``answer``: a human-readable gold answer, and ``answer_norm``: normalized values for exact match;
* ``gold_utterance_ids``: the transcript segments that state it (from the render beat map);
* ``evidence_keys``: phrases that must occur in those segments. :func:`validate` checks this,
  which is a mechanical proof that each answer is stated in its evidence;
* ``as_of``: the date the question is asked (temporal questions set it explicitly);
* ``split``: dev / test, stratified by category with a seeded RNG. Tune only on dev.

Private facts (health, family, compensation) are excluded here; they're used by the
redaction evaluation (Phase 5).
"""

from __future__ import annotations

import itertools
import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from concentrate.core import granola
from concentrate.core.spoken import written
from concentrate.synth.render import BeatMap
from concentrate.synth.roster import ORG_BY_ID, PEOPLE, PROJECT_BY_ID, person
from concentrate.synth.world import Commitment, Evidence, Fact, World

Category = Literal[
    "single_hop", "multi_hop", "temporal", "latest_state", "entity_disambiguation",
    "commitment_status", "decision_change", "aggregation", "unanswerable",
    # v2.1 extras (synth.extras, docs/BENCHMARK_V2_EXTRAS.md)
    "room_attribution", "identity_resolution", "correction", "commitment_tracking",
    "long_horizon", "exact_quote", "speaker_attribution",
]  # fmt: skip
# Categories whose answer lives only in the transcript (verbatim wording, who spoke): a fact
# store or summary cannot answer them. The transcript-retirement curve reports them separately.
NEEDS_TRANSCRIPT: frozenset[str] = frozenset(
    {"exact_quote", "speaker_attribution", "room_attribution"}
)
NOW = date(2026, 10, 24)  # the day after the last meeting: "today" for latest-state questions
UNANSWERABLE = "UNANSWERABLE"
DEV_FRACTION = 0.3


class Question(BaseModel):
    id: str
    category: Category
    question: str
    answer: str
    answer_norm: list[str]
    evidence_keys: list[str]
    gold_utterance_ids: list[str]
    as_of: date
    sources: list[str]  # fact / commitment ids
    split: Literal["dev", "test"] = "test"


SUBJECT = {
    "teams": "Northwind Teams", "falcon": "Falcon (the payments migration)",
    "beacon": "the Beacon onboarding project", "lighthouse": "Lighthouse",
    "northwind": "Northwind", "on_call": "the on-call rotation",
    "release_testing": "release testing", "hiring": "hiring",
    "design_system": "the design system", "eng_manager_cover": "engineering management cover",
}  # fmt: skip
LABEL = {
    "price_per_seat": "per-seat monthly price", "launch_date": "launch date", "owner": "owner",
    "cutover_date": "cutover date", "arr": "ARR", "monthly_signups": "monthly signups",
    "runway_months": "runway", "onboarding_steps": "number of onboarding steps",
    "product_tour_video": "decision on the product tour video",
    "sso_step_order": "position of the SSO login step",
    "onboarding_completion": "onboarding completion rate in usability testing",
    "depends_on": "main launch dependency", "plan": "plan", "target_date": "target date",
    "seats": "expected seat count", "pilot_price": "pilot price per seat",
    "grandfathered": "pricing arrangement after the price change",
    "decision_date": "vendor decision date", "discount_offer": "discount offer",
    "renewal_term": "renewal term", "deal_status": "deal status",
    "security_finding": "launch-blocking security finding",
    "launch_readiness": "launch readiness", "status": "status", "lead": "lead",
    "backend_req": "second backend engineer requisition",
}  # fmt: skip
REQUEST_TOPIC = {
    "csv_export": "exporting seat usage", "sso": "single sign-on",
    "eu_residency": "where their data is stored", "slack": "integrations",
    "pen_test": "security documentation", "bulk_import": "adding users",
    "sso_audit_logs": "what they need in order to sign", "onboarding": "onboarding",
    "audit_logs": "audit logging", "scim": "user provisioning", "hipaa": "compliance paperwork",
}  # fmt: skip
# Labels for Northwind-XL predicates (base predicates are in LABEL above).
XL_LABEL = {
    "quoted_price": "quoted per-seat price", "decision": "hiring decision",
    "weekly_active_teams": "weekly active teams", "activation_rate": "activation rate",
    "nps": "NPS", "ticket_backlog": "support ticket backlog", "target_date": "target ship date",
}  # fmt: skip
# Benchmark subset for scale="xl": per-category quotas (~300 questions, ~10% unanswerable).
BENCHMARK_QUOTAS = {
    "single_hop": 40, "temporal": 50, "latest_state": 40, "entity_disambiguation": 35,
    "commitment_status": 35, "decision_change": 30, "multi_hop": 25, "aggregation": 20,
    "unanswerable": 25,
}  # fmt: skip
AMBIGUOUS = {
    "sam_p": "Sam the backend engineer", "sam_o": "Sam the designer",
    "alex_k": "Alex the engineering manager", "alex_r": "Alex the account executive",
}  # fmt: skip


def _subject(s: str) -> str:
    if s in ORG_BY_ID:
        return ORG_BY_ID[s].name
    if s in SUBJECT:
        return SUBJECT[s]
    if s in PROJECT_BY_ID:
        return PROJECT_BY_ID[s].name
    if s.startswith("candidate:"):
        return f"the candidate {s.split(':', 1)[1]}"
    if s == "support":
        return "the support team"
    return PEOPLE[s].name if s in PEOPLE else s


def _label(predicate: str) -> str:
    if predicate in LABEL:
        return LABEL[predicate]
    if predicate in XL_LABEL:
        return XL_LABEL[predicate]
    if predicate.startswith("metric:"):
        return predicate.split(":", 1)[1]
    return predicate.replace("_", " ")


def _fmt(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}"


class _Gen:
    def __init__(self, world: World, beat_map: BeatMap, seed: int) -> None:
        self.w = world
        self.bm = beat_map
        self.rng = random.Random(f"{seed}:questions")
        self.qs: list[Question] = []
        self.prefix = "q"
        self._texts: set[str] = set()
        self.meeting = {m.key: m for m in world.meetings}

    # -- helpers ----------------------------------------------------------------------------

    def utts(self, evs: list[Evidence]) -> list[str]:
        out: list[str] = []
        for e in evs:
            for u in self.bm.get((e.meeting, e.beat), []):
                if u not in out:
                    out.append(u)
        return out

    def add(
        self,
        category: Category,
        question: str,
        answer: str,
        norm: list[str],
        keys: list[str],
        evs: list[Evidence],
        sources: list[str],
        as_of: date = NOW,
    ) -> None:
        if question in self._texts:  # e.g. a status that changed to "at risk" twice
            return
        self._texts.add(question)
        self.qs.append(
            Question(
                id=f"{self.prefix}{len(self.qs) + 1:03d}",
                category=category,
                question=question,
                answer=answer,
                answer_norm=norm,
                evidence_keys=keys,
                gold_utterance_ids=self.utts(evs),
                as_of=as_of,
                sources=sources,
            )
        )

    def versions(self, subject: str, predicate: str) -> list[Fact]:
        return self.w.facts_about(subject, predicate)

    def where(self, f: Fact) -> str:
        m = self.meeting[f.evidence[0].meeting]
        return f'the "{m.title}" meeting on {_fmt(m.day)}'

    def get(self, subject: str, predicate: str, idx: int = -1) -> Fact:
        return self.versions(subject, predicate)[idx]

    @staticmethod
    def at(versions: list[Fact], d: date) -> Fact | None:
        live = [f for f in versions if f.valid_from <= d and (f.valid_to is None or d < f.valid_to)]
        return live[-1] if live else None

    # -- generators -------------------------------------------------------------------------

    def fact_questions(self) -> None:
        """Single-hop (and disambiguation-flavoured single-hop) questions, one per fact."""
        for f in self.w.facts:
            if f.privacy:
                continue
            subj = _subject(f.subject)
            if f.predicate.startswith(("request:", "complaint:")):
                topic = REQUEST_TOPIC[f.predicate.split(":", 1)[1]]
                q = f"What did {subj} say about {topic} in {self.where(f)}?"
            elif f.kind == "attribute":
                continue
            else:
                label = _label(f.predicate)
                q = self.rng.choice(
                    [
                        f"What was the {label} for {subj}, as stated in {self.where(f)}?",
                        f"According to {self.where(f)}, what is the {label} for {subj}?",
                    ]
                )
            category: Category = "single_hop"
            if f.subject in ("beacon", "beacon_freight"):
                category = "entity_disambiguation"
            self.add(category, q, f.value, [f.value_norm], list(f.spoken), f.evidence, [f.id])

    def who_said_questions(self) -> None:
        """Attribution: who said it — requires resolving Me/Them channels and name cues."""
        cases = [
            ("beacon", "sso_step_order", "sam_p",
             "Who said the SSO login step has to come before workspace naming, and what is "
             "their role?"),
            ("beacon", "onboarding_steps", "sam_o",
             "Who proposed cutting onboarding from five steps to three, and what is their role?"),
            ("falcon", "owner", "tomas", "Who took over Falcon when it changed hands?"),
            ("lighthouse", "owner", "kofi", "Who volunteered to own Lighthouse?"),
            ("on_call", "lead", "tomas", "Who did Alex Kim want to lead the on-call rotation?"),
        ]  # fmt: skip
        for subject, predicate, pid, q in cases:
            f = self.get(subject, predicate)
            p = person(pid)
            self.add(
                "entity_disambiguation",
                q,
                f"{p.name} ({p.role})",
                [pid],
                list(f.spoken),
                f.evidence,
                [f.id],
            )

    def provenance_questions(self) -> None:
        """Where/when was something first said — tests citation, not just recall."""
        candidates = [
            f
            for f in self.w.facts
            if not f.privacy
            and f.kind != "attribute"
            and len(self.versions(f.subject, f.predicate)) == 1
        ]
        for f in self.rng.sample(candidates, min(22, len(candidates))):
            m = self.meeting[f.evidence[0].meeting]
            q = f'In which meeting, and on what date, was it first said that: "{f.bullet}"?'
            self.add(
                "single_hop",
                q,
                f"{m.title} on {m.day.isoformat()}",
                [m.note_id, m.day.isoformat()],
                list(f.spoken),
                [e for e in f.evidence if e.meeting == m.key],
                [f.id],
            )

    def temporal_questions(self) -> None:
        for subject, predicate in sorted({(f.subject, f.predicate) for f in self.w.facts}):
            vs = self.versions(subject, predicate)
            if len(vs) < 2 or vs[0].privacy:
                continue
            fridays = [vs[0].valid_from + timedelta(days=k) for k in range(0, 60)]
            fridays = [d for d in fridays if d.weekday() == 4 and d <= NOW]
            for d in sorted(self.rng.sample(fridays, min(6, len(fridays)))):
                f = self.at(vs, d)
                if f is None:
                    continue
                label = (
                    _label(predicate)
                    if not predicate.startswith("request:")
                    else predicate.split(":")[-1].replace("_", " ")
                )
                q = f"As of {_fmt(d)}, what was the {label} for {_subject(subject)}?"
                self.add(
                    "temporal",
                    q,
                    f.value,
                    [f.value_norm],
                    list(f.spoken),
                    f.evidence,
                    [f.id],
                    as_of=d,
                )

    def latest_questions(self) -> None:
        for subject, predicate in sorted({(f.subject, f.predicate) for f in self.w.facts}):
            vs = self.versions(subject, predicate)
            status_like = predicate.startswith("request:") or predicate in (
                "deal_status",
                "status",
                "security_finding",
                "launch_readiness",
            )
            if vs[0].privacy or not (len(vs) > 1 or status_like):
                continue
            f = vs[-1]
            label = (
                f"status of the request about {REQUEST_TOPIC.get(predicate.split(':')[-1], '')}"
                if predicate.startswith(("request:", "complaint:"))
                else _label(predicate)
            )
            q = f"What is the current {label} for {_subject(subject)}?"
            self.add("latest_state", q, f.value, [f.value_norm], list(f.spoken), f.evidence, [f.id])

    def decision_change_questions(self) -> None:
        hand = [
            ("teams", "price_per_seat"),
            ("teams", "launch_date"),
            ("falcon", "owner"),
            ("falcon", "cutover_date"),
        ]
        changed = sorted(
            {(f.subject, f.predicate) for f in self.w.facts if f.reason and f.supersedes}
            - set(hand)
        )
        for subject, predicate in [*hand, *changed]:
            vs = self.versions(subject, predicate)
            for old, new in itertools.pairwise(vs):
                if not new.reason:
                    continue
                label = _label(predicate)
                m = self.meeting[new.evidence[0].meeting]
                answer = (
                    f"On {_fmt(new.valid_from)} ({m.title}) the {label} changed from "
                    f"{old.value} to {new.value}, because {new.reason}."
                )
                for q in (
                    f"When and why did the {label} for {_subject(subject)} change from "
                    f"{old.value}?",
                    f"Why was the {label} for {_subject(subject)} changed to {new.value}?",
                ):
                    self.add(
                        "decision_change",
                        q,
                        answer,
                        [old.value_norm, new.value_norm, new.valid_from.isoformat()],
                        [*old.spoken, *new.spoken, *new.reason_spoken],
                        [*old.evidence, *new.evidence],
                        [old.id, new.id],
                    )

    def commitment_questions(self) -> None:
        for c in self.w.commitments:
            o = person(c.owner)
            made = self.meeting[c.made_in]
            evs = [*c.made_evidence, *c.resolved_evidence]
            answer, norm = _commitment_status(c)
            ambiguous = c.owner in AMBIGUOUS
            who = AMBIGUOUS[c.owner] if ambiguous else o.name
            q = (
                f'In the "{made.title}" meeting on {_fmt(made.day)}, {who} committed to '
                f"{c.task}. What happened to that commitment?"
            )
            self.add(
                "entity_disambiguation" if ambiguous else "commitment_status",
                q,
                answer,
                norm,
                [c.task_spoken],
                evs,
                [c.id],
            )
        for owner in ("sam_p", "alex_k", "megan", "tomas"):
            cs = [c for c in self.w.commitments if c.owner == owner]
            if not cs:
                continue
            who = AMBIGUOUS.get(owner, person(owner).name)
            q = f"List everything {who} committed to across all meetings."
            self.add(
                "entity_disambiguation" if owner in AMBIGUOUS else "aggregation",
                q,
                "; ".join(c.task for c in cs),
                [c.task_spoken for c in cs],
                [c.task_spoken for c in cs],
                [e for c in cs for e in c.made_evidence],
                [c.id for c in cs],
            )

    def aggregation_questions(self) -> None:
        for org in ORG_BY_ID:
            groups = sorted(
                {
                    f.predicate
                    for f in self.w.facts
                    if f.subject == org and f.predicate.startswith(("request:", "complaint:"))
                }
            )
            current = [self.get(org, p) for p in groups]
            open_items = [f for f in current if f.value_norm in ("open", "committed")]
            if not open_items:
                continue
            q = f"As of today, what open requests or asks does {ORG_BY_ID[org].name} have?"
            self.add(
                "aggregation",
                q,
                "; ".join(f.value for f in open_items),
                [f.predicate.split(":")[1] for f in open_items],
                [k for f in open_items for k in f.spoken],
                [e for f in open_items for e in f.evidence],
                [f.id for f in open_items],
            )
        for subject, predicate, q in [
            ("teams", "launch_date", "List every launch date Northwind Teams has had, in order."),
            ("northwind", "arr", "List every ARR figure that was reported, with when."),
        ]:
            vs = self.versions(subject, predicate)
            self.add(
                "aggregation",
                q,
                "; ".join(f"{f.value} ({f.valid_from})" for f in vs),
                [f.value_norm for f in vs],
                [k for f in vs for k in f.spoken],
                [e for f in vs for e in f.evidence],
                [f.id for f in vs],
            )
        for outcome, q in [
            ("reassigned", "Which commitments were reassigned to someone else, and to whom?"),
            ("dropped", "Which commitments were made but never followed up on?"),
        ]:
            cs = [c for c in self.w.commitments if c.outcome == outcome]
            if not cs:
                continue
            ans = "; ".join(
                f"{person(c.owner).name}: {c.task}"
                + (f" → {person(c.new_owner).name}" if c.new_owner else "")
                for c in cs
            )
            self.add(
                "aggregation",
                q,
                ans,
                [c.task_spoken for c in cs],
                [c.task_spoken for c in cs],
                [e for c in cs for e in (*c.made_evidence, *c.resolved_evidence)],
                [c.id for c in cs],
            )

    def multi_hop_questions(self) -> None:
        g = self.get
        dep, slip1 = g("teams", "depends_on"), g("teams", "launch_date", 1)
        owner_then = self.at(self.versions("falcon", "owner"), slip1.valid_from)
        assert owner_then is not None
        cases: list[tuple[str, str, list[str], list[Fact]]] = [
            (
                "Who owned the project that the Northwind Teams launch depended on at the time "
                "the launch was first delayed?",
                person(owner_then.value_norm).name,
                [owner_then.value_norm],
                [dep, slip1, owner_then],
            ),
            (
                "What pilot price per seat did the customer that asked for a CSV export of seat "
                "usage agree to?",
                g("acmeway", "pilot_price").value,
                ["20"],
                [g("acmeway", "request:csv_export", 0), g("acmeway", "pilot_price")],
            ),
            (
                "How many seats did the customer that required SSO and audit logs expect in "
                "phase one, and at what per-seat price did they give a verbal yes?",
                "150 seats at $25 per seat",
                ["150", "25"],
                [
                    g("corvid", "request:sso_audit_logs"),
                    g("corvid", "seats"),
                    g("corvid", "deal_status"),
                ],
            ),
            (
                "What ARR was reported at the all-hands where monthly signups were four hundred "
                "twenty?",
                "$312k",
                ["312000"],
                [g("northwind", "monthly_signups", 0), g("northwind", "arr", 0)],
            ),
            (
                "After Falcon changed hands, on what date did the new owner report the cutover "
                "completed?",
                "2026-10-08 (reported by Tomás Herrera)",
                ["2026-10-08", "tomas"],
                [g("falcon", "owner"), g("falcon", "status")],
            ),
            (
                "What security finding caused the second launch delay, and what is the launch "
                "date now?",
                "SSO session tokens weren't expiring; launch is 2026-11-11",
                ["2026-11-11"],
                [g("teams", "security_finding"), g("teams", "launch_date")],
            ),
            (
                "What onboarding completion rate did usability testing measure for the flow whose "
                "number of steps the designer proposed cutting?",
                "73% (up from 58%)",
                ["73"],
                [g("beacon", "onboarding_steps"), g("beacon", "onboarding_completion")],
            ),
            (
                "The customer that renewed for only six months had earlier been offered what "
                "discount?",
                "10% with annual prepay (15% was declined)",
                ["10"],
                [g("driftwood", "renewal_term"), g("driftwood", "discount_offer")],
            ),
            (
                "What is the vendor decision date of the customer that asked where their data "
                "would be stored?",
                "2026-12-01",
                ["2026-12-01"],
                [g("beacon_freight", "request:eu_residency"), g("beacon_freight", "decision_date")],
            ),
            (
                "How much did the per-seat price of Northwind Teams rise between the first pricing "
                "decision and the price the bank customer agreed to?",
                "$5 (from $20 to $25)",
                ["5"],
                [g("teams", "price_per_seat", 0), g("corvid", "deal_status")],
            ),
            (
                "Which customer kept the old price after the price change, and what price is that?",
                "Acmeway, $20 per seat for the pilot year",
                ["acmeway", "20"],
                [g("acmeway", "grandfathered"), g("teams", "price_per_seat", 0)],
            ),
            (
                "By the time the Falcon cutover was completed, how many times had the Northwind "
                "Teams launch date been set or changed?",
                "Twice: Oct 14, then Oct 28",
                ["2"],
                [g("teams", "launch_date", 0), g("teams", "launch_date", 1), g("falcon", "status")],
            ),
        ]
        for q, ans, norm, fs in cases:
            self.add(
                "multi_hop",
                q,
                ans,
                norm,
                [k for f in fs for k in f.spoken],
                [e for f in fs for e in f.evidence],
                [f.id for f in fs],
            )

    def cross_entity_questions(self) -> None:
        """Questions that need facts from several meetings (only arise at scale="xl").

        * which customers asked for the same thing (aggregation across customer calls);
        * who owned a project, or what its target was, at the moment its status changed
          (multi-hop: join two time-versioned facts on the date one of them changed).
        """
        askers: dict[str, list[Fact]] = {}
        for f in self.w.facts:
            if f.predicate.startswith("request:") and f.subject in ORG_BY_ID:
                firsts = askers.setdefault(f.predicate, [])
                if not any(g.subject == f.subject for g in firsts):
                    firsts.append(f)
        for predicate, firsts in sorted(askers.items()):
            if len(firsts) < 2:
                continue
            topic = REQUEST_TOPIC.get(predicate.split(":", 1)[1], predicate)
            names = [ORG_BY_ID[f.subject].name for f in firsts]
            self.add(
                "aggregation",
                f"Which customers raised {topic} as a request?",
                "; ".join(names),
                [f.subject for f in firsts],
                [k for f in firsts for k in f.spoken],
                [e for f in firsts for e in f.evidence],
                [f.id for f in firsts],
            )
        for pid in PROJECT_BY_ID:
            statuses = self.versions(pid, "status")[1:]
            owners = self.versions(pid, "owner")
            targets = self.versions(pid, "target_date")
            for st in statuses:
                owner = self.at(owners, st.valid_from)
                target = self.at(targets, st.valid_from)
                name = PROJECT_BY_ID[pid].name
                if owner is not None and len(owners) > 1:
                    self.add(
                        "multi_hop",
                        f"Who owned {name} at the time it was marked {st.value_norm} "
                        f"({_fmt(st.valid_from)})?",
                        owner.value,
                        [owner.value_norm],
                        [*owner.spoken, *st.spoken],
                        [*owner.evidence, *st.evidence],
                        [owner.id, st.id],
                    )
                if target is not None and st.value_norm == "blocked":
                    self.add(
                        "multi_hop",
                        f"When {name} was marked blocked on {_fmt(st.valid_from)}, what "
                        "was its target ship date at that moment?",
                        target.value,
                        [target.value_norm],
                        [*target.spoken, *st.spoken],
                        [*target.evidence, *st.evidence],
                        [target.id, st.id],
                        as_of=st.valid_from,
                    )

    def unanswerable_questions(self) -> None:
        """Plausible questions the corpus cannot answer; each guarded against the world."""
        subjects_preds = {(f.subject, f.predicate) for f in self.w.facts}
        cases: list[tuple[str, tuple[str, str] | None]] = [
            ("What is the launch date for Otter?", ("otter", "launch_date")),
            ("Who owns the Otter project?", ("otter", "owner")),
            ("What per-seat price did Driftwood Retail agree to?", ("driftwood", "pilot_price")),
            ("How many seats has Beacon Freight committed to?", ("beacon_freight", "seats")),
            ("What is Northwind's monthly churn rate?", ("northwind", "churn")),
            (
                "What answer did the team give Beacon Freight about EU data residency?",
                ("beacon_freight", "eu_residency_answer"),
            ),
            (
                "On what date were the Lighthouse dashboards actually migrated?",
                ("lighthouse", "status"),
            ),
            (
                "What did Harbor Ventures decide about the next funding round?",
                ("harbor", "funding"),
            ),
            (
                "What price was agreed with Corvid Bank for SSO as a paid add-on?",
                ("corvid", "sso_addon_price"),
            ),
            (
                "What did Grace Lin's write-up define as the activation metric?",
                ("northwind", "activation_metric"),
            ),
            ("When does Acmeway's Slack integration ship?", ("acmeway", "slack_ship_date")),
            (
                "What is the price of the Northwind Teams enterprise tier?",
                ("teams", "enterprise_price"),
            ),
            ("Who was hired into the second backend engineer role?", ("hiring", "backend_hire")),
            ("What did the penetration test report say?", ("corvid", "pen_test_result")),
            (
                "How many store staff has Driftwood Retail imported so far?",
                ("driftwood", "imported"),
            ),
            ("What was Northwind's ARR in July?", ("northwind", "arr_july")),
            ("What discount did Acmeway ask for at renewal?", ("acmeway", "discount_offer")),
            ("What date was the Otter beta released to the App Store?", ("otter", "release")),
            ("Which vendor is Beacon Freight currently using?", ("beacon_freight", "vendor")),
            ("What did Felicia Norberg present at the all-hands?", ("felicia", "presented")),
            (
                "What was the outcome of the load testing for the launch?",
                ("teams", "load_test_result"),
            ),
            ("What's the new on-call rotation schedule?", ("on_call", "schedule")),
            ("How much did the Falcon migration cost?", ("falcon", "cost")),
            ("What is Dana Ortiz's budget for the Acmeway rollout?", ("acmeway", "budget")),
            ("Who is Driftwood Retail's deputy contact during December?", ("driftwood", "deputy")),
        ]
        for q, guard in cases:
            if guard is not None and guard in subjects_preds:
                raise AssertionError(f"unanswerable question is answerable: {q}")
            self.add("unanswerable", q, UNANSWERABLE, [UNANSWERABLE], [], [], [])

    def split(self) -> None:
        by_cat: dict[str, list[Question]] = defaultdict(list)
        for q in self.qs:
            by_cat[q.category].append(q)
        for qs in by_cat.values():
            ids = [q.id for q in qs]
            self.rng.shuffle(ids)
            dev = set(ids[: max(1, round(len(ids) * DEV_FRACTION))])
            for q in qs:
                q.split = "dev" if q.id in dev else "test"


def _commitment_status(c: Commitment) -> tuple[str, list[str]]:
    if c.outcome == "kept":
        return f"Done (reported complete on {c.resolved_on}).", ["done"]
    if c.outcome == "slipped":
        if c.resolved_on:
            return (
                f"Slipped to {c.new_due}, then completed (reported {c.resolved_on}).",
                ["slipped", "done"],
            )
        return f"Slipped to {c.new_due}; not yet reported done.", ["slipped", "open"]
    if c.outcome == "reassigned":
        assert c.new_owner
        return f"Reassigned to {person(c.new_owner).name}.", ["reassigned", c.new_owner]
    if c.outcome == "dropped":
        return "No follow-up was ever reported; it appears to have been dropped.", ["dropped"]
    return f"Still open (due {c.due}); no update yet.", ["open"]


def generate(world: World, beat_map: BeatMap, seed: int) -> list[Question]:
    """All benchmark questions for a rendered world."""
    g = _Gen(world, beat_map, seed)
    g.fact_questions()
    g.who_said_questions()
    g.provenance_questions()
    g.temporal_questions()
    g.latest_questions()
    g.decision_change_questions()
    g.commitment_questions()
    g.aggregation_questions()
    g.multi_hop_questions()
    g.cross_entity_questions()
    g.unanswerable_questions()
    g.split()
    return g.qs


def select_benchmark(questions: list[Question], seed: int) -> list[Question]:
    """Stratified benchmark subset: up to ``BENCHMARK_QUOTAS`` questions per category.

    Sampling is seeded and keeps the original order and dev/test labels, so the split stays
    stratified and the subset is reproducible.
    """
    rng = random.Random(f"{seed}:select")
    keep: set[str] = set()
    for category, quota in BENCHMARK_QUOTAS.items():
        pool = [q.id for q in questions if q.category == category]
        keep.update(rng.sample(pool, min(quota, len(pool))))
    return [q for q in questions if q.id in keep]


class ValidationReport(BaseModel):
    total: int
    by_category: dict[str, int]
    by_split: dict[str, int]
    errors: list[str]


def validate(questions: list[Question], notes: list[granola.Note]) -> ValidationReport:
    """Check every gold answer's evidence keys occur in its gold utterances, IDs exist, etc."""
    text_by_id: dict[str, str] = {}
    for n in notes:
        for i, item in enumerate(n.transcript or []):
            text_by_id[f"{n.id}:{i:04d}"] = item.text
    errors: list[str] = []
    seen: set[str] = set()
    for q in questions:
        if q.question in seen:
            errors.append(f"{q.id}: duplicate question text")
        seen.add(q.question)
        if not q.answer_norm:
            errors.append(f"{q.id}: empty answer_norm")
        if q.category == "unanswerable":
            if q.gold_utterance_ids:
                errors.append(f"{q.id}: unanswerable question has gold evidence")
            continue
        if not q.gold_utterance_ids:
            errors.append(f"{q.id}: no gold utterances")
        missing = [u for u in q.gold_utterance_ids if u not in text_by_id]
        if missing:
            errors.append(f"{q.id}: unknown utterance ids {missing[:3]}")
            continue
        evidence = " ".join(text_by_id[u] for u in q.gold_utterance_ids).lower()
        for key in q.evidence_keys:  # either spoken ("twenty dollars") or written ("$20")
            if key.lower() not in evidence and written(key).lower() not in evidence:
                errors.append(f"{q.id}: evidence key {key!r} not in gold utterances")
    return ValidationReport(
        total=len(questions),
        by_category=dict(Counter(q.category for q in questions)),
        by_split=dict(Counter(q.split for q in questions)),
        errors=errors,
    )

"""The fictional company "Northwind Labs": people, external contacts, projects, glossary.

All names are invented. Ambiguity is deliberate and is what the benchmark measures:

* two people called Sam (an engineer and a designer) and two called Alex (an engineering
  manager and an account executive);
* an internal project "Beacon" (onboarding redesign) and a customer "Beacon Freight";
* a project with three names: "Falcon" = the payments migration = ticket PAY-212;
* names that ASR misspells (Megan → Meghan, Tomás → Thomas, Inês → Inez).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

DOMAIN = "northwind.dev"


@dataclass(frozen=True)
class Person:
    id: str  # stable slug, used everywhere as the entity ID
    name: str
    email: str
    role: str
    team: str
    org: str = "Northwind Labs"
    asr_variants: tuple[str, ...] = ()  # how ASR mishears the first name

    @property
    def first(self) -> str:
        return self.name.split()[0]


def _p(pid: str, name: str, local: str, role: str, team: str, *asr: str) -> Person:
    return Person(pid, name, f"{local}@{DOMAIN}", role, team, asr_variants=tuple(asr))


OWNER_ID = "priya"

STAFF: tuple[Person, ...] = (
    _p("priya", "Priya Raman", "priya", "Head of Product", "product"),
    _p("marcus", "Marcus Chen", "marcus", "CEO", "leadership"),
    _p("olivia", "Olivia Grant", "olivia", "Head of Finance", "leadership"),
    _p("dev", "Dev Iyer", "dev", "CTO", "leadership"),
    _p("alex_k", "Alex Kim", "alex.k", "Engineering Manager", "engineering"),
    _p("sam_p", "Sam Patel", "sam.p", "Backend Engineer", "engineering"),
    _p("megan", "Megan Liu", "megan.l", "Frontend Engineer", "engineering", "Meghan", "Megan"),
    _p("tomas", "Tomás Herrera", "tomas.h", "Infrastructure Engineer", "engineering", "Thomas"),
    _p("ines", "Inês Duarte", "ines.d", "Mobile Engineer", "engineering", "Inez", "Ines"),
    _p("kofi", "Kofi Mensah", "kofi.m", "Data Engineer", "engineering", "Coffee"),
    _p("ruth", "Ruth Abebe", "ruth.a", "QA Engineer", "engineering"),
    _p("yusuf", "Yusuf Demir", "yusuf.d", "Security Engineer", "engineering", "Joseph"),
    _p("sam_o", "Sam Okafor", "sam.o", "Product Designer", "design"),
    _p("lena", "Lena Fischer", "lena.f", "Design Lead", "design", "Lina"),
    _p("elena", "Elena Petrova", "elena.p", "Product Manager", "product", "Alena"),
    _p("alex_r", "Alex Rivera", "alex.r", "Account Executive", "sales"),
    _p("jordan", "Jordan Blake", "jordan.b", "Head of Sales", "sales"),
    _p("felix", "Felix Moreau", "felix.m", "Sales Engineer", "sales"),
    _p("nadia", "Nadia Haddad", "nadia.h", "Customer Success Manager", "customer success"),
    _p("carla", "Carla Nuñez", "carla.n", "Support Lead", "customer success"),
    _p("hana", "Hana Sato", "hana.s", "Marketing Lead", "marketing", "Hannah"),
    _p("theo", "Theo Walsh", "theo.w", "Growth Lead", "marketing"),
    _p("grace", "Grace Lin", "grace.l", "Data Scientist", "data"),
    _p("ben", "Ben Okoro", "ben.o", "People & Ops Lead", "operations"),
    _p("felicia", "Felicia Norberg", "felicia.n", "Office Manager", "operations"),
)


@dataclass(frozen=True)
class Org:
    id: str
    name: str
    kind: str  # "customer" | "investor"
    domain: str
    contacts: tuple[Person, ...] = field(default_factory=tuple)


def _ext(pid: str, name: str, email: str, role: str, org: str) -> Person:
    return Person(pid, name, email, role, team="external", org=org)


ORGS: tuple[Org, ...] = (
    Org("acmeway", "Acmeway", "customer", "acmeway.com",
        (_ext("dana", "Dana Ortiz", "dana@acmeway.com", "VP Operations", "Acmeway"),)),
    Org("beacon_freight", "Beacon Freight", "customer", "beaconfreight.com",
        (_ext("owen", "Owen Price", "owen.price@beaconfreight.com", "Head of IT",
              "Beacon Freight"),)),
    Org("corvid", "Corvid Bank", "customer", "corvidbank.com",
        (_ext("mira", "Mira Shah", "mira.shah@corvidbank.com", "Compliance Director",
              "Corvid Bank"),)),
    Org("driftwood", "Driftwood Retail", "customer", "driftwoodretail.com",
        (_ext("luis", "Luis Romero", "luis@driftwoodretail.com", "COO", "Driftwood Retail"),)),
    Org("harbor", "Harbor Ventures", "investor", "harbor.vc",
        (_ext("simone", "Simone Laurent", "simone@harbor.vc", "Partner", "Harbor Ventures"),)),
)  # fmt: skip


@dataclass(frozen=True)
class Project:
    id: str
    name: str  # what people call it
    aliases: tuple[str, ...]  # other names: formal name, ticket code
    description: str


PROJECTS: tuple[Project, ...] = (
    Project("falcon", "Falcon", ("the payments migration", "PAY-212"),
            "Migration of billing from the legacy processor to the new payments platform"),
    Project("beacon", "Beacon", ("the onboarding redesign",),
            "Redesign of the new-customer onboarding flow (not the customer Beacon Freight)"),
    Project("otter", "Otter", ("mobile v2",), "Version 2 of the mobile app"),
    Project("lighthouse", "Lighthouse", ("the analytics pipeline",),
            "Rebuild of the product analytics pipeline"),
    Project("teams", "Northwind Teams", ("the team plan",),
            "New per-seat team plan; the launch the company is working towards"),
)  # fmt: skip

# -- Northwind-XL additions (scale="xl" only) -------------------------------------------------
# More customers whose asks and prices sit close to the plot's (near-miss distractors), a third
# Sam, and projects whose names collide with other entities (Harbor vs Harbor Ventures).

XL_ORGS: tuple[Org, ...] = (
    Org("lumen", "Lumen Health", "customer", "lumenhealth.com",
        (_ext("ava", "Ava Lindqvist", "ava@lumenhealth.com", "IT Director", "Lumen Health"),)),
    Org("orchard", "Orchard Labs", "customer", "orchardlabs.io",
        (_ext("sam_r", "Sam Rivera", "sam@orchardlabs.io", "Head of Ops", "Orchard Labs"),)),
    Org("pinecrest", "Pinecrest Logistics", "customer", "pinecrest.co",
        (_ext("noor", "Noor Haddad", "noor@pinecrest.co", "VP Engineering",
              "Pinecrest Logistics"),)),
    Org("quarry", "Quarry Bank", "customer", "quarrybank.com",
        (_ext("ivan", "Ivan Petrov", "ivan.petrov@quarrybank.com", "Security Lead",
              "Quarry Bank"),)),
    Org("redwood", "Redwood Media", "customer", "redwoodmedia.com",
        (_ext("tess", "Tess Morgan", "tess@redwoodmedia.com", "COO", "Redwood Media"),)),
    Org("summit", "Summit Schools", "customer", "summitschools.org",
        (_ext("joel", "Joel Brooks", "jbrooks@summitschools.org", "District CTO",
              "Summit Schools"),)),
    Org("tidewater", "Tidewater Foods", "customer", "tidewaterfoods.com",
        (_ext("maya", "Maya Okoye", "maya@tidewaterfoods.com", "Finance Director",
              "Tidewater Foods"),)),
    Org("vantage", "Vantage Legal", "customer", "vantagelegal.com",
        (_ext("hugo", "Hugo Lefevre", "hugo@vantagelegal.com", "Managing Partner",
              "Vantage Legal"),)),
)  # fmt: skip

XL_PROJECTS: tuple[Project, ...] = (
    Project("kite", "Kite", ("the permissions overhaul", "KITE-88"),
            "Overhaul of roles and permissions"),
    Project("atlas", "Atlas", ("the warehouse migration", "DATA-41"),
            "Migration of the data warehouse to the new cluster"),
    Project("juniper", "Juniper", ("the billing emails revamp",),
            "Rewrite of billing and invoice emails"),
    Project("harbor_hc", "Harbor", ("the help center relaunch",),
            "Relaunch of the help center (not the investor Harbor Ventures)"),
    Project("sparrow", "Sparrow", ("the mobile onboarding test",),
            "A/B test of a shorter mobile onboarding"),
    Project("meridian", "Meridian", ("the admin console",),
            "Enterprise admin console"),
)  # fmt: skip

ALL_ORGS = ORGS + XL_ORGS
ALL_PROJECTS = PROJECTS + XL_PROJECTS

# -- Benchmark v2.1 extras (``--extras``; docs/archive/northwind/V2_EXTRAS.md) ---------------
# Real-world identity mess. Never referenced unless extras are on, so v2 output is unchanged.
EXTRA_STAFF: tuple[Person, ...] = (
    # Changes surname (and email) on RENAMES["rosa"].on; the directory shows the current name.
    _p("rosa", "Rosa Whitfield", "rosa.w", "Solutions Architect", "solutions"),
)
# Two people with the same full name at one customer (both appear on calls as "Daniel Cho").
EXTRA_CONTACTS: tuple[Person, ...] = (
    _ext("daniel_cm", "Daniel Cho", "daniel.cho@corvidbank.com", "Compliance Analyst",
         "Corvid Bank"),
    _ext("daniel_cp", "Daniel Cho", "d.cho@corvidbank.com", "Procurement Manager", "Corvid Bank"),
)  # fmt: skip
# A contractor on a personal address: in no directory, and invites carry no display name.
CONTRACTORS: tuple[Person, ...] = (
    # Reserved .example TLD: a personal-looking address that can never be a real inbox.
    Person("jamie", "Jamie Okonkwo", "jamie.okonkwo.art@personal-mail.example",
           "Freelance illustrator", team="contractor", org="(contractor)"),
)  # fmt: skip
NO_DISPLAY_NAME = frozenset({"jamie"})


@dataclass(frozen=True)
class Rename:
    """A display-name change: before ``on`` the person appeared as ``old_name``/``old_email``."""

    on: date
    old_name: str
    old_email: str


RENAMES: dict[str, Rename] = {
    "rosa": Rename(date(2026, 9, 28), "Rosa Delgado", f"rosa.d@{DOMAIN}"),
}

PEOPLE: dict[str, Person] = (
    {p.id: p for p in STAFF}
    | {c.id: c for o in ALL_ORGS for c in o.contacts}
    | {p.id: p for p in (*EXTRA_STAFF, *EXTRA_CONTACTS, *CONTRACTORS)}
)
ORG_BY_ID: dict[str, Org] = {o.id: o for o in ALL_ORGS}
PROJECT_BY_ID: dict[str, Project] = {p.id: p for p in ALL_PROJECTS}


def person(pid: str) -> Person:
    return PEOPLE[pid]


def identity_on(pid: str, d: date) -> tuple[str, str]:
    """(display name, email) a calendar invite or speaker label showed for ``pid`` on ``d``."""
    p, r = PEOPLE[pid], RENAMES.get(pid)
    if r is not None and d < r.on:
        return r.old_name, r.old_email
    return p.name, p.email

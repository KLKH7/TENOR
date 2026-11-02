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

PEOPLE: dict[str, Person] = {p.id: p for p in STAFF} | {c.id: c for o in ORGS for c in o.contacts}
ORG_BY_ID: dict[str, Org] = {o.id: o for o in ORGS}
PROJECT_BY_ID: dict[str, Project] = {p.id: p for p in PROJECTS}


def person(pid: str) -> Person:
    return PEOPLE[pid]

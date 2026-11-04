# Live probe v2 of Granola's MCP (2026-11-02, evening)

This follows up [`PROBE_2026-11-02.md`](PROBE_2026-11-02.md) with a systematic, **read-only** probe of the official MCP (`https://mcp.granola.ai/mcp`). It ran on the same account: Plus plan, **4 meetings** (Oct 16–30, 2026), of which 3 were remote and 1 was in person.

**Privacy.** Findings are aggregated and paraphrased: no names, titles, quotes or answers. The raw responses are kept in `data/real/probe/`, which is gitignored.

**How to rerun.**
- Probe tool: `concentrate real-kit probe <probes.json>`, code in `concentrate.evals.real_slice.run_probes`.
- Every call goes through `ReadOnlyCaller`. It refuses any tool outside `query_granola_meetings`, `search_meetings`, `get_meetings`, `get_meeting_transcript`, `search_transcripts`, `list_meetings` and `get_account_info`, and a test checks that each write, folder, feedback and sign-out tool is refused.
- Python calls the MCP directly (`LiveMcpCaller`), so latencies are wall-clock times of single calls, with no agent overhead.

**Design.**
- 14 natural-language questions, in 4 themes, sent to `query_granola_meetings`. One was repeated 3× to test stability, giving 16 agent calls.
- 17 calls to the retrieval tools, for latency.
- Each answer was checked by hand against the stored meetings (`data/real.db`).

**Caveat.** n is tiny (4 meetings, 14 questions). Treat these as *existence proofs* of behaviours, not rates.

## 1. Results by theme

| Theme | Probes | Granola right | Pattern |
|---|---|---|---|
| Cross-meeting identity (who is X, which meetings) | 5 | 3 | Right when the person has an **email** in attendee metadata, or a distinctive full name used in several summaries. It **missed** a person listed only by display name, when searched by full name. It **missed** the second of two people who share a first name. |
| In-person attribution (one microphone) | 2 | 1 of 2 consistent | Asked for attribution across the whole meeting, it **correctly refused** ("all voices are on the microphone"). Asked about one commitment, it **confidently named the note owner**. In a third answer (the commitments list), the same item came back as "speaker not reliably identified". |
| Commitments and follow-up | 3 (+2 repeats) | 3 | Strong. It gives owner, status and a "no later confirmation" vs "explicitly not done" distinction, cited to the later meeting, and the answer was stable across 3 repeats. |
| Temporal / "as of" | 4 | 2 | Explicit dates work ("as of Oct 20", "in October"). "**When is the next meeting?**" was refused as a calendar question, although a past meeting's transcript states the date. An unscoped "what meetings do I have" silently applied a **7-day window** and returned 2 of the 4 meetings. |

### 1.1 Identity details
- **Email handle → name works.** For a person known by first name in one meeting and by full name plus email in another, Granola linked both. It also resolved a bare short first name to that person's email.
  - In this probe a teammate who appears in two meetings was correctly linked to both. The first probe saw a one-of-two miss; we didn't re-run that exact question, so we can't tell whether Granola changed or this case is simply easier.
- **Miss 1: full name of a display-name-only attendee.** A collaborator who is a listed attendee of one meeting, and appears by first name in its summary, came back as "couldn't find any meetings or transcript mentions". The full name appears only in attendee metadata, not in the text, so keyword search over notes and transcripts can't reach it.
- **Miss 2: shared first names.** Two people share a first name: one is an attendee of one meeting, the other is mentioned in another meeting. Granola found **one meeting**, and said it couldn't tell whether they were the same person "because no other meetings matched". The attendee-metadata match was never made.
  - This is the "wrong Sam" failure our synthetic `disambiguation` questions target.

### 1.2 Attribution consistency
Granola's own tool descriptions tell it not to attribute single-microphone speech. Its behaviour depends on the question:
- broad questions get the honest refusal;
- a narrow question gets the most plausible owner, here the note owner (which is also what a summary written from the owner's perspective tends to imply).

The same commitment was attributed differently in two answers. A precomputed store resolves attribution **once per utterance** and answers consistently, but it can be consistently wrong. Only the real slice's gold answers can say which is better.

## 2. Latency per tool (wall clock, one call at a time)

| Tool | n | Median | Range |
|---|---|---|---|
| `query_granola_meetings` (agent) | 16 | **8.5 s** | 1.6–17.7 s |
| `search_meetings` | 3 | 0.4 s | 0.4 s |
| `list_meetings` | 3 | 0.3 s | 0.3–0.4 s |
| `get_meetings` (4 notes) | 3 | 0.4 s | 0.4 s |
| `search_transcripts` | 3 | 0.3 s | 0.3 s |
| `get_meeting_transcript` (1 chunk or full) | 5 | 0.4 s | 0.3–0.6 s |

- The agent's time is dominated by its own reasoning, not retrieval: every retrieval tool answers in under a second.
- Refusals are fast (1.6 s for the calendar refusal). Cross-meeting lists are slow (17.7 s for the all-commitments question).
- With only 4 meetings these are a **lower bound**. Retrieval cost and agent turns should grow with corpus size; the synthetic scale curve (workstream F) is the controlled test.

## 3. Where Granola does well (honestly)
- **Commitment tracking is good:** owners, honest "no later evidence", and explicit "not done, reassigned" when a later meeting says so, all cited. It distinguishes *not confirmed* from *not done* better than the first probe suggested.
- **Calibrated abstention on in-person speech** when asked broadly, and it explains why.
- **Explicit date scoping works**, including "as of" a past date and month-wide listings.
- **Stable answers:** 3 repeats of the same question gave the same facts in different words.
- **Retrieval tools are fast** (≈0.3–0.4 s) and return citable chunks.
- **Citations** use meeting UUIDs that match the REST/MCP IDs, so they're machine-checkable.

## 4. Implications for Concentrate
1. **Identity is the clearest gap.** Concentrate's directory and attendee resolution link a display name, an email handle and first-name mentions to one entity at write time. The two misses above are exactly the queries that design answers from a lookup. The real slice's `disambiguation` category measures this directly.
2. **Relative-time questions** ("next meeting", "what's due next week") need the meeting date to resolve dates mentioned in past meetings. Granola's agent routed one to the calendar instead. Concentrate stores resolved dates on facts.
3. **Latency:** an 8.5 s median per question vs one store lookup plus one LLM call. Workstream F prices this.
4. **Don't overclaim on commitments.** Granola is already good here on a small corpus. Any Concentrate advantage must show up in the slice's scores, not be assumed.

## 5. Follow-up: scored results on the same account

The probe above is qualitative. The scored comparison is in [`REAL_SLICE.md`](../results/REAL_SLICE.md):
- 39 LLM-drafted questions, approved in bulk by the meeting owner with 0 edits;
- Granola's agent **92%** [82, 100] vs Concentrate **59%** [44, 74], with b0 top-k at 77% on the same 39;
- a second judge agrees 97% of the time;
- unanswerable questions were not run for Granola or Concentrate.

Concentrate's losses were mostly long-tail details its write-time memory never extracted. That fits §1.1 and §4: Granola's agent reads transcripts at question time.

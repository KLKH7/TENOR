# Concentrate: scope, map and where it could go

This is the author's map of the project. Each workstream below has an owner decision; nothing starts without one.

## 1. What we're doing

### In plain terms
AI agents increasingly ask Granola about your meetings: "what did we decide on pricing?", "what did Sam promise?". There are two ways to answer:
- **Look it up when asked:** search the transcripts at question time and read what comes back. Granola's agent does this today.
- **Study ahead of time:** after each meeting, write down the facts, who promised what, and what changed, in a tidy, cited notebook. Then answer from the notebook.

We built a fake company with 184 meetings, where we know every true answer, and an exam of 296 questions about it. Seven ways of answering, including two well-known memory products, take the exam. We grade accuracy, cost and speed, and report what breaks.

### In technical terms
- **A benchmark:** Northwind, a synthetic corpus in Granola's exact API/MCP format, calibrated on real transcripts. The ground truth is generated first, so every question has a verified answer and gold transcript lines.
- **A harness:** the same answer model and judge for every system. It reports accuracy with bootstrap CIs, exact match, citation precision/recall, $/question, latency, and ingest cost.
- **Systems:**
  - *query-time:* top-k hybrid retrieval, summaries only, a full-context ceiling, and an agent modelled on Granola's MCP;
  - *write-time:* Mem0, Graphiti, and **Concentrate**. Concentrate's pipeline is speaker/entity resolution → grounded fact extraction → verification gate → bitemporal store.
- **Live evidence:** a read-only probe of Granola's real MCP on real meetings.

## 2. Where we are (2026-11-02, evening)

| Piece | State |
|---|---|
| Benchmark v1 (184 meetings, 296 Qs) | ✅ built; fidelity audited (`FIDELITY.md`): good on format and speaker labels, weak on wording (96% templated filler) |
| 7 systems + harness + judge check | ✅ built and tested (~200 tests) |
| Memory builds (Mem0, Graphiti, Concentrate) | 🛠 running (resumed after a network stall) |
| Final comparison on test | ⏳ after the builds; costs ~$40–60 of API credit |
| Write-up for Granola | ❌ not started |

**API credit:** about $65 of $200 spent so far. That's separate from Claude Code usage, which pays for agents writing code; API credit pays for running models inside the experiments.

## 3. What would genuinely impress a startup like Granola

A team that has thought about this more than we have won't be impressed by technique. What impresses them is **evidence about their product, their decisions and their users, made concrete enough to act on**:

1. **"I measured your real agent, on real meetings."** Every number we have is synthetic, or a re-implementation of their agent. A small real slice changes that: you hand-write questions about your own meetings, Granola's own `query_granola_meetings` answers them, and we score it against Concentrate.
2. **"Here's the answer to a decision you said you're weighing."** Granola said it's considering deleting old transcripts. A curve of *accuracy vs how much transcript we keep*, by question type, answers that directly.
3. **"Here's what it would cost you."** Unit economics at their scale: ingest $/meeting, $/user/month, and query cost vs accuracy for every system. Startups think in margins.
4. **"Here's something you can touch."** A working demo beats a PDF:
   - Concentrate as an **MCP server** you can plug into Claude next to Granola's, and ask both the same question;
   - a **results explorer** where you click any question and see every system's answer, citations and cost.
5. **"Here's exactly where everyone breaks."** A failure taxonomy with real examples: speaker confusion, the wrong Sam, stale values, missed follow-ups. Specific, reproducible findings are what engineers forward to each other.
6. **"This holds up."** Robustness:
   - a scale curve (does query-time retrieval degrade as meetings pile up?);
   - a v2 corpus with realistic wording;
   - hybrid-room meetings;
   - the second judge and the hand check.

## 4. Workstreams (each can run as its own session)

| # | Workstream | Why it impresses | Agent-hours | API $ | Depends on |
|---|---|---|---|---|---|
| A | **Final comparison + failure taxonomy** | the headline numbers and where each system breaks | 1 | ~$40–60 | builds finishing |
| B | **Real-data slice vs Granola's real agent** | the only comparison against the real product | 1 (+ you writing ~30 Qs) | ~$3 | your real meetings |
| C | **Transcript-retirement curve** | answers Granola's stated open question | 1 | ~$10 | Concentrate build |
| D | **Concentrate MCP server + side-by-side demo** | something they can plug in and try | 1–2 | ~$1 | none |
| E | **Results explorer (web)** | makes every number inspectable | 1–2 | $0 | A's report format (exists) |
| F | **Unit economics + scale curve** | margins, and when query-time breaks | 1 | ~$5–15 | builds |
| G | **Benchmark v2: realistic wording** (noise calibration, filler pool, digits, length, polish) | removes the biggest validity caveat | 2 | ~$15–25 later | none to build |
| H | **Pre-meeting brief** (a proactive memory product feature, evaluated) | shows product thinking, not just research | 1–2 | ~$3 | Concentrate build |
| I | **Engineering hardening** (request timeouts, which caused today's stall; resumable runs; CI) | credibility of the codebase | 0.5 | $0 | none |
| J | **Write-up for Granola** (README results, a 2-page memo, a short deck) | what they'll actually read | 1 | $0 | A, ideally B and C |

## 5. How you stay in control (and keep learning)

- **Each workstream runs as a visible session you can open, read and talk to,** not a hidden subagent.
- **Every brief ends with a teach-back:** a "what I built, the CS concept behind it, the decision I'd like from you" summary written for you.
- **Decisions live in `docs/DESIGN.md`,** and anything over $2 still needs your yes.
- **Checkpoints:** I'll pause to show you results and ask for direction at each one, rather than chaining decisions on your behalf.

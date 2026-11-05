# Measuring meeting AI honestly (plan)

*Decided 2026-11-05 after the first AMI results. Method: calibrated evaluation. Product angle: completeness.*

## Why

Automated checks on meeting AI over-count errors. On 7 AMI meetings recorded into Granola and Wispr Flow:

| Check | Automated | After human verification |
|---|---|---|
| Note claims (grounding checker, 20 sampled) | most flagged as unsupported, overreach or contradicted | the human could judge 12; agreed with the checker on **4**; found **0 wrong, 1 overreach** |
| Granola Chat answers (LLM judge, 68) | **16 wrong**, 24 partial | of the 3 strongest "confident contradictions", the human found **0 clearly wrong**: all partly right |

The over-count has identifiable causes, each fixable:

| Cause | Example | Fix |
|---|---|---|
| Names vs roles | gold says "the Project Manager", the product says "Shreedhar" | a speaker roster from the meeting's own introductions |
| Product boilerplate | Granola's `<transcript_note>` on every answer | strip it before judging |
| Weak judge | Haiku's reason says "arguably correct", its verdict says "incorrect" | a stronger judge, checked against humans |
| Line-number bug | cited "evidence" pointed at the wrong lines | IDs instead of positions |
| Summary-as-answer-key | AMI's one-line summary drops nuance the product kept (the Gisella case) | allow "both partly right"; score against the transcript, not only the summary |
| Granularity | a valid but different list of action items scores "partial" | per-item recall, not one verdict |

Granola evaluates with LLM judges too (Braintrust is mentioned on its blog). If their judges share these failure modes, their error numbers are inflated and their regressions are hard to see.

## What we build

1. **A calibration set:** human labels on note claims and Chat answers from AMI meetings (public, so they can be released). Target ~100 items; 23 done (2026-11-05).
2. **A calibrated judge:** roster, boilerplate stripping, transcript windows, a strong model, per-item scoring; reported with its **agreement with humans** (Cohen's κ and raw agreement) and an **over-count factor** (automated errors ÷ human-confirmed errors).
3. **The product angle, completeness:** for Chat answers and notes, the share of gold items missing ("useful but incomplete", Granola's own category), and whether reading the transcript (depth) recovers them. This links back to [breadth and fidelity](EVIDENCE_COMPRESSION.md).

## Threats to validity

- **One human verifier so far** (the author), who also designed the study. A second verifier is needed for agreement between humans.
- **7 meetings**, 6 of them role-play; 1 real meeting. More AMI recordings are planned.
- **Small verified sample** (23 items). Every rate above has a wide interval; none is a headline yet.
- **The verification sheet shaped answers:** the first two versions lacked context and were rebuilt; only v3 labels are used.

## Product angle: quiet features on one engine

Design rule: **notepad first, user in control, calm.** A feature must feel natural in a paper notebook: no overlays, no interruptions by default.

One engine: claims, decisions and promises extracted when notes are written, each with its source line and date. Four surfaces:

| Feature | What the user sees | Evidence it addresses |
|---|---|---|
| **Said, not noted** | A collapsed line under enhanced notes: "3 things said that aren't in your notes"; tap to add | Completeness: ~35% of Granola Chat answers on AMI were partial |
| **Inferred ink** | AI bullets that are inferences, not statements, in a slightly lighter shade; hover for why | Real meetings: 14% of Granola's note claims were inferred ([NOTES_FAITHFULNESS](NOTES_FAITHFULNESS.md)) |
| **Margin notes** | An old note gains a small mark when a later meeting changes it: "updated 5 Nov → €30" | Cross-meeting answers were often partial; one merged two statements |
| **Since last time** | A quiet line in the pre-meeting brief and in the notes: open promises, changed decisions | Cross-meeting follow-through |
| *Live recall* | Opt-in setting, off by default: one card, only for a direct conflict with a past decision | Kept as an experiment; risks feeling intrusive |

Each surface shows a line **only when confident**; the threshold is set and validated with the calibrated judge above (precision first). Each line carries its source quote.

Why Granola might not want these: more surface area to design and support; false alarms erode the calm feel; some overlap with existing todos, briefs and citations. Each needs a measured precision before it's worth shipping.

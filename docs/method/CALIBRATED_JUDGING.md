# Calibrated judging

This page explains why automated judges over-count errors on meeting AI outputs and how TENOR's calibrated judge corrects for it. Decided 2026-11-05 after the first AMI results; status: plan, partly built.

![How does the outside-in evaluation harness work? Public AMI audio is played into Granola and Wispr Flow, read back through read-only MCP tools into a private store, and scored by free scorers and a calibrated judge against the AMI answer key and human labels](../figures/harness_flow.svg)

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

LLM judges are common in industry evaluation of meeting products as well as in research. Any judge with these failure modes inflates error counts and makes real regressions harder to see, which is why every TENOR rate is reported beside the judge's measured agreement with human labels.

## What we build

1. **A calibration set:** human labels on note claims and Chat answers from AMI meetings (public, so they can be released). Target ~100 items; 23 done (2026-11-05). Protocol: [`HUMAN_VERIFICATION.md`](HUMAN_VERIFICATION.md).
2. **A calibrated judge:** roster, boilerplate stripping, transcript windows, a strong model, per-item scoring; reported with its **agreement with humans** (Cohen's κ and raw agreement) and an **over-count factor** (automated errors ÷ human-confirmed errors).
3. **Completeness as a measure:** for Chat answers and notes, the share of gold items missing ("useful but incomplete" in Granola's published answer taxonomy), and whether reading the transcript recovers them. This links back to [breadth and fidelity](EVIDENCE_COMPRESSION.md).

## Threats to validity

- **One human verifier so far** (the author), who also designed the study. A second verifier is needed for agreement between humans.
- **7 meetings**, 6 of them role-play; 1 real meeting. More AMI recordings are planned.
- **Small verified sample** (23 items). Every rate above has a wide interval; none is a headline yet.
- **The verification sheet shaped answers:** the first two versions lacked context and were rebuilt; only v3 labels are used.

What these findings could mean for product design is discussed separately, product-neutrally, in [`product/IMPLICATIONS.md`](../product/IMPLICATIONS.md).

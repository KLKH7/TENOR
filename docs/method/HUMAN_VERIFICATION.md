# Human verification

This page describes how TENOR collects human labels on product outputs and uses them to calibrate its automated judges.

## Why

Automated judges decide most TENOR verdicts, and on meeting outputs they over-count errors ([`CALIBRATED_JUDGING.md`](CALIBRATED_JUDGING.md)). A verdict rate is only as trustworthy as the judge's measured agreement with humans, so every automated rate is reported beside that agreement.

## What is labelled

| Item | What the verifier sees | Label |
|---|---|---|
| **Note claim** | The claim and the AMI transcript context it should be grounded in | supported · inferred (overreach) · unsupported · contradicted, or "cannot judge" |
| **Chat answer** | The question, the product's answer and the AMI gold answer | correct · partly right · wrong |

## Protocol so far

- **Selection.** The 20 note claims were sampled from the automated grounding checker's output; the 3 Chat answers were the ones the automated judge called the strongest confident contradictions. Both selections lean towards items the automation called errors, which is where over-counting shows, and which inflates the apparent over-count compared with a uniform sample.
- **Context first.** The first two versions of the verification sheet lacked enough transcript context and produced unusable labels; they were rebuilt, and only labels from the third version are used.
- **Public items only.** Labels are collected on AMI meetings (CC BY 4.0), so items and labels can be released with the benchmark. Product outputs are released only as short quoted examples beside the AMI source.

## Planned for the calibration set

- **About 100 labels,** sampled with a fixed seed, reported both for a uniform sample and for a sample weighted towards flagged items.
- **Product names hidden** on the sheet where the output format allows it.
- **A second, independent verifier** on a shared subset, to measure agreement between humans.
- **Reported as agreement:** Cohen's κ and raw agreement between the calibrated judge and the human labels, and an over-count factor (automated errors ÷ human-confirmed errors), each with its n.

## Status (2026-11-05)

| | Done | Target |
|---|---|---|
| Labels collected | 23 (20 note claims, 3 Chat answers) | about 100 |
| Verifiers | 1 (the author, who also designed the study) | at least 2, for agreement between humans |

**What the 23 labels show so far:** of 20 sampled note claims, the verifier could judge 12, agreed with the automated checker on 4, and found 0 wrong and 1 overreach. Of the 3 Chat answers the automated judge called the strongest confident contradictions, 0 were clearly wrong; all were partly right. These are counts, not rates: the sample is too small for an interval to be informative.

## Threats to validity

- **One verifier, who is also the designer.** A second, independent verifier is needed before any agreement figure is called confirmed.
- **Selection leans towards flagged items,** so the current counts overstate the over-count a uniform sample would show.
- **The sheet design shapes the labels,** as the first two versions showed.

# Phase 4 design: the Concentrate memory engine

Status: **design, not built.** It's written while the baselines (B0–B5) are being finished, so the plan can be reviewed before any code exists.

## What Concentrate has to beat, and where it can win

The research survey (2026-11-02) changed the bar. *Time-stamped facts with supersession* are not new:
- Graphiti stores `valid_at`/`invalid_at` on every fact.
- Engram (arXiv 2606.09900) is a bi-temporal store with provenance.
- Mem0 extracts facts at write time and updates them.

So Concentrate competes on what's **specific to Granola meetings**, which none of those systems handle, and which our live probe showed Granola's own agent getting wrong:

| Gap (observed) | Why generic memory misses it | Concentrate's answer |
|---|---|---|
| "Them" lines have no or partial names; in-person meetings are all "Me" | Generic systems treat the speaker label as given | **Speaker attribution** from conversational cues |
| The same person appears as a name in one meeting and an email handle in another (the live miss) | They link entities by string similarity | **Cross-meeting entity resolution** using attendees, the directory, and who-attends-what |
| ASR mishears names ("Coffee" for Kofi, "Meghan") | String matching fails | **Phonetic matching** |
| Answers must cite the exact line | Mem0 and Graphiti lose line IDs during extraction | **Line-level provenance** on every fact |
| Granola is considering deleting transcripts | Nobody measures what's lost | **The transcript-retirement curve** |

## Pipeline

```
 meeting note ──► 1. ENHANCE ──► 2. EXTRACT ──► 3. VERIFY ──► 4. CONSOLIDATE ──► memory store
 (transcript,      who is          atomic          is each        merge, supersede,     facts (bitemporal)
  summary,         speaking,       facts with      fact really    track commitments     entities
  attendees)       who is          line IDs        in the lines                         digests
                   mentioned                       it cites?
                                                                     question ──► 5. ANSWER
```

### 1. Enhance: speaker attribution and entity linking (mostly not LLM)
- **Candidates** for any person mention or speaker come from:
  - the meeting's attendees (calendar);
  - the org directory;
  - people seen in earlier meetings of the same series;
  - the glossary, for projects and codenames.
- **Features scored for each candidate:**
  - *Jaro-Winkler string similarity*, which weights prefix agreement, so "Meghan" scores close to "Megan";
  - *Double Metaphone* phonetic codes: words that *sound* alike get the same code, so "Coffee" and "Kofi" both start with "KF";
  - *co-occurrence priors*: how often this person attends this meeting series;
  - *channel cues*: microphone means the note owner in remote meetings;
  - *addressing cues*: "Megan, what's on your plate?" followed by a Them turn makes Megan the likely speaker.
- **Decision:** pick the highest-scoring candidate if it clears a confidence threshold, otherwise stay explicit and unresolved (`Sam[?: designer|engineer]`).
- **Output:** an *enhanced transcript* where every line has a resolved speaker (or `?`) and resolved mentions, with confidences.

**Concept: why not just ask an LLM "who said this?"** You can, and we'll use one as a fallback for low-confidence cases. But deterministic scoring is cheap, explainable (it shows *why* it chose Megan), and testable against our ground truth, which knows the true speaker of every synthetic line. That gives a precise *entity-linking accuracy* number, which the brief asks for.

### 2. Extract: atomic facts (LLM, structured output)
For each meeting chunk of the enhanced transcript, the ingestion model (Sonnet 5.5) returns facts as JSON:

```json
{"subject": "teams", "predicate": "price_per_seat", "value": "$25 per seat per month",
 "value_norm": "25", "valid_from": "2026-09-29", "source_lines": ["not_…:0037","not_…:0038"],
 "reason": "SSO and audit logs now included", "confidence": 0.92, "privacy": null}
```

- **Typed predicates** (`decided`, `committed_to`, `owns`, `status_of`, `metric_value`, `date_of`, `request`, …) make questions answerable by lookup, not just by search.
- Dates are resolved against the meeting date ("by Thursday" → 2026-09-24).

### 3. Verify: the grounding gate
No fact enters memory unless its cited lines support it:
1. **Deterministic checks** for numbers, dates and codes. Spoken-number normalization (`spoken_numbers`) turns "twenty five dollars" into 25, which is compared against `value_norm`.
2. **A quote check**: the verifier must quote the supporting text, and the quote must be a substring of the cited lines.
3. **An LLM entailment check**, only for facts that pass 1–2.

Rejected facts are logged with a reason. To measure the gate, we inject known-bad facts and count how many it catches (precision and recall).

**Concept: hallucination control by construction.** An extractor LLM can invent plausible facts. Checking every fact against its cited source *before* storing it means the memory can only contain things the transcript actually says. That's the property Granola needs before trusting a memory enough to delete transcripts.

### 4. Consolidate: bitemporal store
- The store is keyed by `(subject entity, predicate)`.
- **Supersession:** if a new fact has a different value, the old one gets `valid_to = new.valid_from` and is kept, not deleted. History stays queryable ("price as of week 3").
- **Duplicates:** the same value restated adds evidence and raises confidence.
- **Commitment lifecycle:** later evidence ("the script is done", "slipped to Friday", "Ruth will take it") moves a commitment through open → done / slipped / reassigned. No mention by the end means "no follow-up", which isn't the same as done.
- **Contradictions:** two different current values from the same date are flagged, not silently resolved.

**Concept: bitemporal data.** Databases can track two times per fact: *valid time* (when it was true in the world) and *transaction time* (when we learned it). Finance and audit systems use this so they can answer both "what was true on date X?" and "what did we *believe* on date X?". Meetings need valid time for the questions; transaction time matters when a later meeting corrects an earlier one.

### 5. Answer: structured lookup first, search second
1. **Resolve the question's entities**, using the same linker as step 1, to settle "Sam the engineer".
2. **Classify the intent**: latest, as-of a date, history, status, list, or open-ended.
3. **Look up** the matching `(subject, predicate)` facts, applying the as-of date.
4. **Add hybrid search** over facts and per-meeting digests for open-ended parts.
5. **Answer** with Opus 5.5 using the shared answer prompt, citing **source line IDs**. The answer model and prompt are the same as every baseline's, so differences come from memory quality, not prompting.

## Compression tiers and the retirement experiment

| Tier | Content | Kept after retirement? |
|---|---|---|
| L0 | raw transcript | no |
| L1 | enhanced transcript | no |
| L2 | verified facts (with line IDs, now dangling) | yes |
| L3 | meeting digest | yes |

The **retirement curve** comes from deleting L0 and L1 for meetings older than N days, re-running the benchmark, and plotting tokens kept against accuracy. That directly answers Granola's "can we delete transcripts and keep a working memory?"

## How it will be measured (no special treatment)
- **Same questions, judge, answer model and budget rules as B0–B5.** Ingestion cost is reported separately, the same way as for Mem0 and Graphiti.
- **Ablations**, one at a time: without entity linking, without the grounding gate, without supersession, without digests. These show which parts actually matter.
- **Splits:** tuning on **dev only**; one final run on **test**.

## Open questions for review
1. **Ingestion model:** Sonnet 5.5 (cheaper, consistent with Mem0/Graphiti here), or Opus 5.5 for extraction quality? Budget favours Sonnet.
2. **Chunking:** whole meetings (more context, fewer calls) or ~40-line windows (cheaper retries, lower recall of cross-chunk facts)?
3. ~~Speaker attribution scope~~ **Decided (2026-11-02, with the author):** remote platforms (Meet/Zoom) already give Granola names: 82% of remote "Them" lines were named in the author's real meetings. So:
   - **Remote:** attribution only for the unnamed remainder. The effort goes into **cross-meeting identity**: platform display name ↔ calendar email handle ↔ directory, which is the miss observed live.
   - **In person (and hybrid rooms):** full inference, because one microphone (or one "Room 4B" participant) carries several voices. The signals are:
     - addressing cues ("Megan, …" → next speaker);
     - self-references resolved by later meetings ("I'll take X" … "Sam's X is done");
     - role priors (the CFO reports runway);
     - the attendee list as the candidate set;
     - **label propagation over iOS diarization clusters**: one confident line names the whole "Speaker A" cluster.
   - **Benchmark follow-up:** add hybrid meetings where a conference room joins as one participant.

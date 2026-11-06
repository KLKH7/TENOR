---
pretty_name: Northwind meeting memory
license: cc-by-4.0
language:
  - en
task_categories:
  - question-answering
task_ids:
  - extractive-qa
  - open-domain-qa
tags:
  - agent-memory
  - meetings
  - transcripts
  - long-context
  - temporal-reasoning
  - synthetic
size_categories:
  - n<1K
source_datasets:
  - original
annotations_creators:
  - machine-generated
language_creators:
  - machine-generated
multilinguality:
  - monolingual
configs:
  - config_name: base
    data_files:
      - split: questions
        path: fixtures/northwind/questions.jsonl
---

# Dataset card: Northwind meeting memory

Northwind is a synthetic benchmark for **agent memory over meeting notes**. A fictional startup, Northwind Labs, holds standups, 1:1s, customer calls, design reviews, all-hands and an investor update. Each meeting is a note in the JSON shape of Granola's public API (title, attendees, calendar event, AI summary, transcript). Questions ask what an agent would need to know about months of those meetings, and every answer is known by construction and cites the transcript lines that state it.

Part of [TENOR](../README.md) (archived synthetic diagnostics). Benchmark card: [`docs/archive/northwind/DESIGN.md`](../docs/archive/northwind/DESIGN.md). Not affiliated with Granola: it uses Granola's public data formats only.

## Dataset structure

| Config | Meetings | Questions (dev / test) | Shipped |
|---|---|---|---|
| `base` | 40 | 230 (71 / 159) | committed at `data/fixtures/northwind/` |
| `xl` | 184 | 296 (90 / 206) | regenerated with `uv run tenor synth --seed 42 --scale xl` into `data/fixtures/large/` |

Files in a fixture directory:

- `notes/<note_id>.json`: one meeting in Granola's API note shape. Transcript entries carry `speaker.source` (`microphone` = the note owner, `speaker` = system audio), optional speaker names, and timestamps; utterance IDs are `<note_id>:<NNNN>`.
- `questions.jsonl`: one question per line with `id`, `category`, `question`, `answer`, `answer_norm`, `evidence_keys`, `gold_utterance_ids`, `as_of` (the date the question is asked), `sources` (world facts), and `split` (`dev` | `test`).
- `world.json`: the ground-truth world the meetings were rendered from (people, orgs, projects, facts with validity dates, commitments and their outcomes).
- `directory.json`: the company directory (people, orgs, projects, aliases).
- `folders.json`: Granola-style folders.
- `manifest.json`: generator version, seed, noise rates, counts and validation results.

The Hugging Face `configs` block above exposes the questions only, as one split; filter on the `split` field for dev/test. The meetings are the retrieval corpus and are loaded from `notes/`.

### Question categories

`temporal`, `single_hop`, `latest_state`, `commitment_status`, `entity_disambiguation`, `decision_change`, `unanswerable`, `multi_hop`, `aggregation`. Categories are stratified across the dev/test split. Counts per category are in each `manifest.json`.

## Creation

1. A seeded generator plans the world: people, customers, projects, prices, launch dates, owners and commitments with outcomes (kept, slipped, reassigned, dropped, never followed up).
2. Meetings are rendered from the world with templated language and calibrated noise (fillers, false starts, misheard names, unnamed speakers, in-person meetings on one microphone).
3. Questions are derived from the world, and the generator validates that each gold answer is stated in its gold utterances.

No LLM is involved: generation is deterministic and free, and the same seed gives byte-identical output. Noise rates were calibrated on aggregate statistics of the author's own real meetings; **no real transcript text, names or other personal data is in the dataset**. Email addresses use fictional organisations.

## Intended use

Comparing memory and retrieval systems for agents: accuracy, abstention, citation precision/recall, cost and latency. Tune on `dev` only; run `test` once.

## Limitations and biases

- **Synthetic wording.** Real meetings are messier. Generator `northwind-1` produced heavy filler repetition; `northwind-2` reduces it, but sentence frames still repeat far more than in real meetings ([`docs/FIDELITY_V2.md`](../docs/FIDELITY_V2.md)).
- **Calibration is small.** Noise rates come from a handful of real meetings, not thousands.
- **One company, English only, one meeting culture.** Results may not transfer to other domains.
- **Generator version matters.** Utterance IDs are positional, so reports must be read against fixtures from the same generator version and git SHA (recorded in every report).

## License

The synthetic data (everything under `data/fixtures/`) is released under [Creative Commons Attribution 4.0 (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). The code that generates it is MIT ([`LICENSE`](../LICENSE)).

## Citation

See [`CITATION.cff`](../CITATION.cff) or the BibTeX in the [README](../README.md#citation).

# Contributing

Thanks for looking. Hindsight is a research benchmark, so the bar is **honesty and reproducibility over polish**: a new system that loses, reported carefully, is a welcome contribution.

## Setup

```bash
uv sync
uv run pytest            # no network, no API key
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

CI runs the same four checks ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). Conventions for code, prompts and data are in [`CLAUDE.md`](CLAUDE.md); the short version:

- Python 3.11, small typed modules, Pydantic v2 for anything crossing a boundary.
- **Determinism:** generators and evals take `--seed`; same seed, same bytes. Inject clocks and RNGs.
- **LLM calls go only through `concentrate.llm`** (structured output, retries, a disk cache keyed by model and prompt, cost accounting). Prompts live in `prompts/<name>.v<N>.md`; bump the version instead of editing a prompt in place.
- **Tests never touch the network.** Mock HTTP with `httpx.MockTransport`; fake the LLM (see `FakeLLM` in `tests/test_baselines.py`).
- **Secrets only from the environment.** Never log or persist them. Run `uv run python scripts/prepublish_check.py --all` before opening a PR that touches data or config.

## Run the benchmark

```bash
uv run concentrate synth --seed 42 --scale xl                       # free, deterministic
uv run concentrate eval --xl --split dev --systems b1 --estimate    # free: price it first
uv run concentrate eval --xl --split dev --systems b1               # costs API credit; asks above $2
uv run concentrate analyze evals/reports/<stamp>.json               # free: significance, Pareto, failures
```

- **Tune on `dev` only.** Run `test` once, at the end, and report what it says.
- Write-time systems build their memory once (`concentrate ingest`, or automatically on the first `eval`) under `data/cache/`; reruns reuse it, and cached LLM calls are free.
- Every report records the git SHA, models, prompt versions, seed and date. Keep it with any number you quote.

## Add a system

A system answers one question at a time and says what it cost. There are two protocols, both in [`packages/concentrate/agents/baselines.py`](packages/concentrate/agents/baselines.py):

- **`System`** (query-time: does its work when asked):
  - `name: str` and `prompt_ids: tuple[str, ...]` (the prompt versions it uses, logged with every result);
  - `run(q) -> SystemResult`: the answer (`answer`, `abstain`, `citations` as transcript line IDs) plus tokens, `cost_usd`, `latency_s`, tool calls and the note IDs it read;
  - `estimate(q) -> (input_tokens, output_tokens)` without calling a model, so `--estimate` stays free.
- **`WriteTimeSystem`** (builds a memory ahead of time) adds:
  - `ingest(notes) -> IngestStats`: idempotent, persisted under `data/cache/<system>/<corpus hash>/`, resumable;
  - `estimate_ingest(notes) -> (input_tokens, output_tokens)`.

Steps:

1. Put the system in `packages/concentrate/agents/<your_system>.py`. Take an `LLMClient` in the constructor and use the shared answer prompt (`prompts/answer.v1.md`) unless the point of your system is a different prompt; the comparison is fairest when only the *context* differs.
2. Register a short key in `SYSTEMS` and `build_systems()` in [`packages/concentrate/evals/runner.py`](packages/concentrate/evals/runner.py). Heavy optional dependencies are imported lazily there (see `b4`/`b5`).
3. Add `tests/test_<your_system>.py` with a fake LLM: at minimum, that `run` returns cited answers, that `estimate` makes no calls, and (for write-time systems) that a second `ingest` is a no-op.
4. Document it in `docs/baselines/<your_system>.md`: what it puts in front of the model, how it's configured, and its known weaknesses.
5. Open a PR with a dev-split report (or just the `--estimate` output if you'd rather the maintainer runs it). Use the **New system** issue template if you want to discuss it first.

## Add questions or change the benchmark

Never hand-edit `data/fixtures/`. Change the generator in `packages/concentrate/synth/`, regenerate with `concentrate synth --seed 42`, and commit the result; a test enforces that the committed fixtures match the generator exactly. A generator change that alters transcripts needs a new generator version, because utterance IDs are positional and old reports cite them.

## Licence

By contributing you agree that your code is released under the [MIT licence](LICENSE) and any benchmark data under [CC BY 4.0](data/README.md#license).

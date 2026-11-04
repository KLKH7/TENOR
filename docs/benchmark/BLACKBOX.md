# The black-box track: bring your own meetings

Hindsight has two tracks (see [PLAN.md](../PLAN.md), section 0.5):

1. **The public track** runs on synthetic meetings (Northwind) whose answers are known by construction. Any system that can load a corpus can take it.
2. **The black-box track** (this page) is for products that *can't* load our corpus, such as Granola itself or any other meeting tool with an MCP server. It runs on **your own meetings**, with **your own questions**, and talks to the product **only through its public MCP**.

## Why it exists

External agents (Claude, ChatGPT, Cursor) reach a meeting product only through its MCP. They see an answer, maybe some citations, and a wait. So asking a product questions through that same MCP measures exactly what those agents experience: answer quality, citations, latency and failure modes. It makes no claims about how the product works inside.

The kit is the real-data slice ([REAL_SLICE.md](../results/REAL_SLICE.md)), generalised. It started as "Granola's agent vs Concentrate on Kian's meetings". Now it is "any meeting MCP, on anyone's meetings".

## Which public interfaces are used, and nothing else

| Product | Interface | Tool calls made | Arguments |
|---|---|---|---|
| **Granola** (`--product granola`) | Granola's public MCP, `https://mcp.granola.ai/mcp`, Streamable HTTP, browser OAuth sign-in | `query_granola_meetings` only | `{"query": <question>}` |
| **Concentrate** (`--product concentrate`) | Concentrate's own MCP server (`concentrate real-kit serve`), stdio | `ask_memory` only | `{"question": <question>, "answer": true}` |
| **Any other product** (`--product <name>`) | Its MCP server, stdio or Streamable HTTP, described in a JSON spec | the one tool named in the spec | `{<question_arg>: <question>, ...extra_args}` |

Nothing else touches the product while it is being scored:
- no private or undocumented endpoints;
- no scraping of the app;
- no internal data;
- no write tools.

Each adapter allowlists exactly one tool. A wrapper (`AllowlistCaller`) refuses every other tool name **before anything is sent**. `tests/test_blackbox.py` checks this against every write tool Granola's MCP offers (`update_private_notes`, `create_folder`, `add_note_to_folder`, `give_feedback`, `sign_out`, ...).

**Other interfaces the kit uses, outside scoring**, all optional and read-only:
- **To draft questions**, the kit reads your meetings into `data/real.db` with `concentrate sync --source granola-mcp`. This calls `list_meetings`, `get_meetings` and `get_meeting_transcript` (see [SCHEMA_NOTES.md](../engineering/SCHEMA_NOTES.md), "MCP source mapping"). Hand-written questions don't need it.
- **`real-kit probe`** times Granola's read tools through a 7-tool read-only allowlist (`evals/real_slice.py: READ_ONLY_TOOLS`).
- **The Anthropic API** is used for the judge (`judge.v1`) and, if you use it, for drafting questions. Its LLM cache lives under `data/real/cache/`.

## Run it on your own account

All commands run from the repo root. Every file they write about your meetings lands under `data/real/`, which is gitignored.

1. **Set up.** Run `uv sync`, and put `ANTHROPIC_API_KEY` in `.env` (it is needed for the judge).
2. **Write questions.** The questions are about your meetings, not about any one product, so every product is asked the same ones.
   - **Test split (hand-written, held out).** Copy the template below into `data/real/test_questions.md` and fill in one block per question, from your own memory. Don't look anything up in the products first. Then import it:
     ```
     Q: Who proposed moving the pilot to October?
     A: Priya
     KEY: Priya
     KIND: who_said | decision | change | commitment_status | disambiguation | temporal | unanswerable
     ```
     ```bash
     uv run concentrate real-kit import-test data/real/test_questions.md
     ```
     Re-importing is safe: duplicate questions are skipped. Use `A: UNANSWERABLE` for questions the meetings never answer.
   - **Dev split (optional, LLM-drafted).** Sync your meetings (`uv run concentrate sync --source granola-mcp --db data/real.db`), then run `real-kit draft --estimate` and `real-kit draft`. Next, `real-kit review` walks you through every draft: accept, edit or reject. Use dev questions to explore and tune. Never tune on test.
   - `real-kit add --split dev|test` adds a single question by hand.
3. **Check coverage.** `uv run concentrate real-kit status --split test --product granola` shows questions per kind, and how many answers are already recorded for that product.
4. **Price it, then run it.**
   ```bash
   uv run concentrate real-kit run --product granola --split test --estimate
   uv run concentrate real-kit run --product granola --split test
   ```
   - The first run asks each question once. The raw response, the parsed answer, the end-to-end latency and a timestamp are recorded in `data/real/answers/<product>/`.
   - Every later run replays the recording, so scores are reproducible and the product isn't asked again.
   - `--offline` scores only questions that already have a recorded answer, and says how many it skipped.
   - Repeat `--product` to ask several products in one run.
   - For `--product concentrate`, first build the memory (`uv run concentrate real-slice --systems c --estimate`, then without `--estimate`). The adapter then starts `real-kit serve` itself.
5. **Look at disagreements.** `uv run concentrate real-kit spotcheck data/real/reports/<stamp>.jsonl --split test --product granola` writes `data/real/reports/spotcheck.test.md`. It lists each question where a product disagrees with your gold answer. Sometimes the gold is what's wrong.
6. **Share only the aggregate.** Each run writes `data/real/reports/<stamp>.aggregate.json`. Add `--commit-to docs/results/<name>.json` to also write it somewhere you can commit. Either way, the writer (`blackbox.kit.write_committable`) first checks the file and **refuses to write it** if it holds:
   - a per-question field (`qid`, `question`, `gold`, `answer`, `raw`, `citations`, ...);
   - a meeting ID;
   - a string over 120 characters;
   - any question, gold answer or product answer text from the run.

### How answers are scored

The scoring is the same as on the real slice:
- **The judge.** `judge.v1` grades each answer against your gold answer. The `.jsonl` report has the runner's shape, so `concentrate judge-check` can re-judge it with a second model.
- **Key-term match**: the fraction of your `KEY` terms found in the answer.
- **Citations** are scored at the **meeting level**: the meetings a product cites vs the gold evidence meetings. Hand-written test questions usually have no evidence IDs, so citation scores are then n/a.
- **Latency** is end to end, as an agent waits for it: from the call until the parsed answer, transport included.
- **Abstention.** Granola returns prose with no "I don't know" flag, so the judge decides from the text whether it declined. Abstain precision and recall are only meaningful for products that send a flag (Concentrate does).
- **Cost.** A hosted product (Granola) costs us $0, because it runs on its side. Concentrate's server writes its answer with an LLM billed to your own Anthropic key. It reports that cost (`answer_cost_usd`), which is recorded as the product's `cost_per_q`. `--estimate` and `--budget` cover only the judge, so a first live Concentrate run costs more than the estimate (about one answer call per question).

## Adding a product

**Most products need no code: write a JSON spec.** Save it as `data/real/products/<name>.json`, and run with `--product <name>`, or pass the path. [`product.example.json`](product.example.json) is a complete template. Its fields:

| Field | Meaning |
|---|---|
| `name` | Lowercase id. Answers are recorded under `data/real/answers/<name>/` |
| `transport` | `stdio` (start `command args` as a subprocess) or `http` (Streamable HTTP `url`, no auth) |
| `env` | Names of environment variables to pass to a stdio server, e.g. its API key. Values are read from your environment and never stored |
| `tool`, `question_arg`, `extra_args` | The one question-answering tool, the argument the question goes in, and any fixed extra arguments |
| `answer_path`, `citation_path` | For JSON responses: dotted paths to the answer text and to a list of cited IDs |
| `citation_regex` | For text responses: a regex whose group 1 is a cited meeting ID |
| `read_only_tools` | The allowlist. It must contain `tool`, and should contain nothing else |

**Products that need code** (OAuth over HTTP, or an answer that takes several calls): subclass `McpProduct` in `packages/concentrate/blackbox/products.py`. Set `name`, `tool` and `read_only_tools`, and implement `arguments()` and `parse()`. `GranolaProduct` is the model to copy. Its caller is `sources.mcp_transport.LiveMcpCaller`, which handles OAuth. Then register the name in `open_product()`.

### Read-only checklist (do this before the first run)

1. **Read the server's tool list** (`tools/list`, or its docs). Name the single tool that answers a question about meetings.
2. **Confirm that tool can't change anything.** It must not:
   - create, edit or delete notes, folders, summaries or templates;
   - send feedback, email or messages;
   - sign out, or switch workspace or account;
   - start a paid job on your behalf.

   If its description or annotations say it writes (`readOnlyHint: false` or `destructiveHint: true`), stop: it can't be used.
3. **Allowlist only that tool.** If the product needs a second call (for example, fetching the answer by job ID), repeat steps 1–2 for that tool too.
4. **Let the preflight check it.** For stdio and HTTP products, the kit lists the server's tools on connect. It refuses to run if an allowlisted tool is missing, or is annotated as not read-only. Annotations are optional in MCP, so an unannotated tool passes the preflight, and your check in step 2 is the real guard.
5. **Keep the spec private if it's account-specific.** `data/real/products/` is gitignored. Only commit a spec that holds no account URL or identifier.

## Where everything lives

| What | Where | Committed? |
|---|---|---|
| Dev questions (LLM-drafted, reviewed) | `data/real/questions.jsonl` | No |
| Test questions (hand-written, held out) | `data/real/questions.test.jsonl` | No |
| Recorded product answers | `data/real/answers/<product>/` (Granola's pre-black-box recordings in `data/real/granola/` still replay) | No |
| Per-question reports, spot-checks | `data/real/reports/` | No |
| Aggregate metrics | `data/real/reports/<stamp>.aggregate.json`, or `--commit-to` | Yes, after the check |
| Product specs | `data/real/products/` | No (except examples like `product.example.json`) |

## Limits

- **One person's meetings.** A result describes one account: its meeting count, languages, meeting types and the kinds of questions its owner asks. It isn't a population estimate, and on a few dozen questions the confidence intervals are wide (they are reported).
- **LLM-drafted vs hand-written questions.**
  - Drafted questions are cheap, but they lean toward what an LLM reading transcripts finds askable.
  - Bulk-approved gold answers can be wrong (REAL_SLICE found at least one).
  - Hand-written test questions avoid both problems. They are fewer, though, and their gold rests on the writer's memory.
  - Report the splits separately, and treat dev as exploratory.
- **Same questions, not necessarily the same data.** Two products are only compared on identical data if both hold the same meetings.
  - Granola and Concentrate can be: Concentrate's memory is built from the meetings synced out of Granola.
  - A different meeting tool usually recorded different meetings (or none of these). Its score on these questions then measures coverage as much as quality. Ask each product only about meetings it actually holds, and say so in the result.
- **A black box shows only what it returns.** If a product gives no citations or no abstain flag, those metrics are n/a for it. We don't infer them.
- **Answers are recorded once.** A product's answers are recorded the first time each question is asked. Products change over time, so every recording carries its `asked_at` time, and a result holds only for that date.

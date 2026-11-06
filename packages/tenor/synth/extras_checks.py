"""Free, deterministic probes of the v2.1 phenomena on a fixture set (no LLM, no network).

Each probe is a cheap stand-in for a failure mode a real system could have, so we can check that
the extras are hard for the *intended* reason before paying for a model run:

* ``retrieval``: BM25 meeting recall@5 per category (all gold meetings in the top 5), the
  lexical half of the top-k baseline. Phenomena that are retrievable but still hard isolate
  reasoning from search.
* ``label_attribution``: answer "who said X?" by reading the speaker label on the gold
  passages. Works for named remote speakers; must fail for a hybrid room.
* ``exact_quote``: is the clean quote verbatim in the noisy transcript, and are its key
  phrases in the AI summary (what survives if transcripts are retired)?
* ``retracted_traps``: how often each misstated value is still in transcripts and in AI
  summaries, i.e. what a mention-counting or summary-only system would see.
* ``directory``: do the names that appear on calls resolve to exactly one directory entry?

Usage: ``uv run python -m tenor.synth.extras_checks <fixture dir>``. Aggregates only.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from tenor.agents.baselines import MeetingRetriever
from tenor.core.spoken import written
from tenor.evals.runner import load_corpus
from tenor.synth.questions import Question
from tenor.synth.world import World

K = 5
WHO_CATEGORIES = (
    "room_attribution",
    "speaker_attribution",
    "entity_disambiguation",
    "identity_resolution",
)


def _questions(fixtures: Path) -> list[Question]:
    lines = (fixtures / "questions.jsonl").read_text().splitlines()
    return [Question.model_validate_json(line) for line in lines]


def probe(fixtures: Path) -> dict[str, Any]:
    """All probes for one fixture set (aggregate numbers only)."""
    notes = load_corpus(fixtures)
    world = World.model_validate_json((fixtures / "world.json").read_text())
    directory = json.loads((fixtures / "directory.json").read_text())
    people = {p["id"]: p for p in directory["people"]}
    qs = [q for q in _questions(fixtures) if q.category != "unanswerable"]
    by_utt = {u.utterance_id: (m, u) for m in notes for u in m.utterances}

    # -- retrieval: BM25 meeting recall@K -----------------------------------------------------
    retriever = MeetingRetriever(notes, embedder=None)
    hits: dict[str, list[float]] = defaultdict(list)
    for q in qs:
        gold = {by_utt[u][0].note_id for u in q.gold_utterance_ids if u in by_utt}
        top = {m.note_id for m in retriever.top_meetings(q.question, K)}
        hits[q.category].append(float(bool(gold) and gold <= top))
    retrieval = {c: round(sum(v) / len(v), 2) for c, v in sorted(hits.items())}

    # -- label attribution: read the speaker label of the gold passages -----------------------
    label: dict[str, list[float]] = defaultdict(list)
    for q in qs:
        pid = q.answer_norm[0]
        if q.category not in WHO_CATEGORIES or pid not in people:
            continue
        names = {by_utt[u][1].speaker_name for u in q.gold_utterance_ids if u in by_utt}
        target = people[pid]["name"]
        ambiguous = sum(p["name"] == target for p in people.values()) > 1
        label[q.category].append(float(target in names and not ambiguous))
    label_attribution = {
        c: {"n": len(v), "correct": round(sum(v) / len(v), 2)} for c, v in sorted(label.items())
    }

    # -- exact quotes: is the wording recoverable without the raw transcript? ----------------
    quotes = [q for q in qs if q.category == "exact_quote"]
    verbatim = in_summary = 0
    for q in quotes:
        note = by_utt[q.gold_utterance_ids[0]][0]
        said = " ".join(by_utt[u][1].text for u in q.gold_utterance_ids).lower()
        verbatim += q.answer.strip('"').lower() in said
        summary = (note.summary_markdown or "").replace("*", "").lower()
        in_summary += all(k in summary or written(k).lower() in summary for k in q.answer_norm)
    exact_quote = {
        "n": len(quotes),
        "clean_quote_verbatim_in_transcript": round(verbatim / max(1, len(quotes)), 2),
        "key_phrases_in_summary": round(in_summary / max(1, len(quotes)), 2),
    }

    # -- retracted values: still visible in transcripts and summaries? ------------------------
    traps: dict[str, dict[str, int]] = {}
    retracted = world.extras.retracted if world.extras else []
    for f in (f for f in world.facts if f.id in retracted):
        forms = {s.lower() for s in f.spoken} | {written(s).lower() for s in f.spoken}
        traps[f"{f.subject}.{f.predicate}={f.value_norm}"] = {
            "passages": sum(
                any(x in u.text.lower() for x in forms) for m in notes for u in m.utterances
            ),
            "summaries": sum(f.bullet in (m.summary_markdown or "") for m in notes),
        }

    # -- directory resolution of names heard on calls ------------------------------------------
    heard = {u.speaker_name for m in notes for u in m.utterances if u.speaker_name}
    heard |= {p.name for m in notes for p in m.attendees if p.name}
    resolves: dict[str, int] = defaultdict(int)
    for name in heard:
        n = sum(p["name"] == name for p in people.values())
        resolves["unique" if n == 1 else "none" if n == 0 else "ambiguous"] += 1
    unresolved = sorted(n for n in heard if sum(p["name"] == n for p in people.values()) != 1)
    emails = {p.email for m in notes for p in m.attendees}
    known = {p["email"] for p in people.values()}
    return {
        "fixtures": str(fixtures),
        "questions": len(qs),
        f"bm25_meeting_recall@{K}": retrieval,
        "label_attribution": label_attribution,
        "exact_quote": exact_quote,
        "retracted_traps": traps,
        "directory": {
            "names_heard": dict(resolves),
            "unresolved_names": unresolved,
            "attendee_emails_not_in_directory": len(emails - known),
        },
    }


def main() -> None:
    print(json.dumps(probe(Path(sys.argv[1])), indent=1))


if __name__ == "__main__":
    main()

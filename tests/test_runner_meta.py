"""Report provenance: judge reasons in the JSONL, pinned data version, per-system k/config."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tenor.agents.baselines import MeetingRetriever, TopKSystem
from tenor.evals.runner import (
    fixtures_version,
    load_corpus,
    load_questions,
    run_eval,
    system_settings,
)
from tenor.llm.client import LLMClient
from tests.test_baselines import FIXTURES, FakeLLM, HashEmbedder


class _WriteTime:
    """Minimal stand-in for a write-time system: only what system_settings reads."""

    name = "b9_fake"

    def __init__(self, cfg: dict[str, Any] | None) -> None:
        self.cfg = cfg

    def config(self) -> dict[str, Any]:
        if self.cfg is None:
            raise RuntimeError("store not open")
        return self.cfg


def _run(tmp_path: Path, k: int) -> dict[str, Any]:
    llm = FakeLLM()
    client = LLMClient(llm, model="claude-opus-5-5", cache_dir=tmp_path / "cache")
    notes = load_corpus(FIXTURES)
    topk = TopKSystem(client, MeetingRetriever(notes, HashEmbedder()), k, backend=llm)
    return run_eval(
        [topk],
        load_questions(FIXTURES, "dev", limit=3),
        client,
        seed=42,
        split="dev",
        budget_usd=100,
        out_dir=tmp_path / "reports",
        fixtures=FIXTURES,
    )


def test_jsonl_keeps_judge_reason_and_verdict_string(tmp_path: Path) -> None:
    report = _run(tmp_path, k=10)
    rows = [json.loads(x) for x in Path(report["paths"]["jsonl"]).read_text().splitlines()]
    assert len(rows) == 3
    for r in rows:
        assert r["verdict"] in ("correct", "partially_correct", "incorrect")  # still a string
        assert r["judge"] == {"verdict": r["verdict"], "reason": "fake"}
        assert r["judge_reason"] == "fake"
        assert isinstance(r["correct"], bool) and r["qid"] and r["question"]


def test_report_records_k_and_data_version(tmp_path: Path) -> None:
    report = _run(tmp_path, k=10)
    meta = report["meta"]
    assert meta["systems"]["b0_topk"]["k"] == 10
    assert meta["data"] == fixtures_version(FIXTURES)
    assert meta["data"]["generator"] == "northwind-2"
    md = Path(report["paths"]["md"]).read_text()
    assert "**Retrieval:** b0_topk k=10" in md
    assert meta["data"]["content_sha256"][:16] in md


def test_fixtures_version_is_content_addressed(tmp_path: Path) -> None:
    copy = tmp_path / "nw"
    shutil.copytree(FIXTURES, copy)
    v = fixtures_version(copy)
    assert v["content_sha256"] == fixtures_version(FIXTURES)["content_sha256"]  # path-free
    assert v["files"] == len(list(FIXTURES.glob("notes/*.json"))) + 2
    (copy / "manifest.json").write_text('{"generator": "other"}')  # manifest isn't hashed
    assert fixtures_version(copy)["content_sha256"] == v["content_sha256"]
    note = sorted(copy.glob("notes/*.json"))[0]
    note.write_text(note.read_text() + " ")
    assert fixtures_version(copy)["content_sha256"] != v["content_sha256"]


def test_system_settings_hashes_write_time_config() -> None:
    a = system_settings([_WriteTime({"model": "m", "format": 1})])  # type: ignore[list-item]
    b = system_settings([_WriteTime({"format": 1, "model": "m"})])  # type: ignore[list-item]
    c = system_settings([_WriteTime({"format": 2, "model": "m"})])  # type: ignore[list-item]
    assert a["b9_fake"]["k"] is None
    assert a["b9_fake"]["config_sha256"] == b["b9_fake"]["config_sha256"]  # key order free
    assert a["b9_fake"]["config_sha256"] != c["b9_fake"]["config_sha256"]
    broken = system_settings([_WriteTime(None)])  # type: ignore[list-item]
    assert "store not open" in broken["b9_fake"]["config_error"]

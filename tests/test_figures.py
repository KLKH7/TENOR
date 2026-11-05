"""Figures: smoke, determinism and the committed-figures check, on tiny fixtures."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from concentrate.evals import figstyle as fs
from concentrate.evals import figures as F
from concentrate.evals import figures_diagrams as FD
from concentrate.evals import figures_real as FR

STAMP = "20990101T000000Z"
SYSTEMS = ("b0_topk", "concentrate", "b3_agentic")
CATS = ("single_hop", "temporal")


def _metrics(acc: float) -> dict[str, object]:
    return {
        "n": 4,
        "accuracy": acc,
        "accuracy_ci95": [acc - 0.1, min(1.0, acc + 0.1)],
        "by_category": {c: {"n": 2, "accuracy": acc} for c in CATS},
    }


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)


def make_root(root: Path) -> Path:
    """A tiny but complete set of figure inputs under ``root``."""
    reports = root / "evals" / "reports"
    accs = {"b0_topk": 0.75, "concentrate": 0.75, "b3_agentic": 0.5}
    built = {"input_tokens": 1000, "output_tokens": 100}
    _write(
        reports / f"{STAMP}.json",
        {
            "meta": {
                "split": "test",
                "questions": 4,
                "model": "claude-opus-5-5",
                "ingest": {"concentrate": {"detail": {"built": built}}},
            },
            "metrics": {s: _metrics(a) for s, a in accs.items()},
        },
    )
    rows = [
        {"system": s, "qid": f"q{i}", "correct": i < 3, "input_tokens": 500, "output_tokens": 50}
        for s in SYSTEMS
        for i in range(4)
    ]
    _write(reports / f"{STAMP}.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
    pairs = [
        {"a": "concentrate", "b": "b0_topk", "diff": 0.0, "significant": False},
        {"a": "concentrate", "b": "b3_agentic", "diff": 0.25, "significant": True},
        {"a": "b0_topk", "b": "b3_agentic", "diff": 0.25, "significant": False},
    ]
    failures = {s: {"partial_list": {"count": 1}, "stale_value": {"count": 1}} for s in SYSTEMS}
    _write(
        reports / f"{STAMP}.analysis.json",
        {"pairs": {"overall": pairs}, "failures": {"table": failures}},
    )
    _write(
        reports / f"{STAMP}.judgecheck.claude-sonnet-5-5.md",
        "Primary judge `claude-opus-5-5` vs second judge `claude-sonnet-5-5`, same prompt.\n\n"
        "- **Agreement on correct / not correct:** 91.7% (Cohen's kappa 0.80)\n\n"
        "| System | n | Accuracy (primary) | Accuracy (second) | Δ |\n|---|---|---|---|---|\n"
        + "".join(f"| {s} | 4 | 75.0% | 50.0% | -25.0% |\n" for s in SYSTEMS)
        + "\n| | correct | partially_correct | incorrect |\n|---|---|---|---|\n"
        "| correct | 8 | 1 | 0 |\n| partially_correct | 0 | 1 | 0 |\n"
        "| incorrect | 0 | 0 | 2 |\n",
    )
    curve = {
        f"concentrate@{k}": {"n": 4, "accuracy": 0.75, "accuracy_ci95": [0.5, 1.0]}
        for k in ("inf", "7", "0")
    }
    curve["b1_summaries"] = {"n": 4, "accuracy": 0.5, "accuracy_ci95": [0.25, 0.75]}
    _write(
        reports / "retirement" / "20990101T000000Z.curve.json",
        {
            "meta": {"split": "dev", "questions": 4},
            "order": ["inf", "7", "0"],
            "retired_share": {"inf": 0.0, "7": 0.8, "0": 0.99},
            "curve": curve,
        },
    )
    point = {"accuracy_common": 0.5, "n_common": 2}
    _write(
        root / "evals" / "scale" / "x" / "scale.json",
        {
            "meta": {"model": "claude-haiku-5-5", "reports": {}},
            "common_questions": ["q1", "q2"],
            "systems": {
                s: {"points": [{"meetings": m, **point} for m in (10, 20)]} for s in SYSTEMS
            },
        },
    )
    real = {
        "systems": {s: _metrics(0.5) for s in ("granola_agent", "concentrate", "b0_topk")},
        "paired": {"granola_agent|concentrate": {"a_only": 3, "b_only": 1, "p": 0.625}},
        "fallback_dev": {
            "n": 4,
            "off_correct": 1,
            "on_correct": 3,
            "won": 2,
            "lost": 0,
            "p": 0.5,
            "by_category": {"who_said": {"n": 4, "off": 1, "on": 3}},
        },
    }
    _write(root / F.REAL_SNAPSHOT, real)
    cells = [
        {"system": s, "tag": t, "n": 4, "accuracy": 0.5, "acc_ci": [0.25, 0.75],
         "confident_error": 0.25, "ce_ci": [0.0, 0.5]}
        for s in SYSTEMS
        for t in ("all", "notes_sufficient", "transcript_needed", "unanswerable")
    ]  # fmt: skip
    _write(
        reports / "notes_vs_transcript.json",
        {"test_report": {"report": STAMP, "primary": {"cells": cells, "gaps": []}}},
    )
    tag_cell = {"n": 4, "accuracy": 0.5, "accuracy_ci": [0.25, 0.75]}
    cat_cell = {"n": 6, "accuracy": 0.5}
    conditions = {
        c: {"n": 8, "accuracy": 0.5, "accuracy_ci": [0.25, 0.75], "confident_error": 0.125,
            "confident_error_ci": [0.0, 0.25], "partial": 0.125, "hedged_miss": 0.375,
            "by_tag": {t: tag_cell for t in ("notes_sufficient", "transcript_needed")},
            "by_category": {"who_said": cat_cell, "temporal": tag_cell}}
        for c in F.DELETION_CONDITIONS
    }  # fmt: skip
    pair = {"wins": 1, "losses": 3, "sign_p": 0.625}
    _write(
        reports / "real-deletion-sim-dev.aggregate.json",
        {
            "meta": {"questions": 8, "date": "20260101T000000Z"},
            "metrics": {"deletion": {"conditions": conditions, "paired": {
                k: pair for k in ("notes_only vs granola", "notes_facts vs notes_only",
                                  "notes_facts_fallback vs notes_facts")}}},
        },
    )  # fmt: skip
    cov = {"windows": [[0.0, 0.9], [15.0, 0.3], [30.0, 0.8]], "coverage": 0.667, "gaps": []}
    ctrl = {"windows": [[0.0, 0.1], [15.0, 0.0], [30.0, 0.0]], "coverage": 0.0, "gaps": []}
    _write(
        reports / "ami" / "coverage.json",
        {
            "meta": {"threshold": 0.4, "window_s": 15.0},
            "summary": {p: {"coverage": 0.667, "meetings": 2} for p in ("granola", "wispr")},
            "meetings": [
                {"meeting_id": mid, "duration_s": 45, "control_meeting": "IB4003",
                 "control": ctrl, "products": {"granola": cov, "wispr": cov}}
                for mid in ("EN2002b", "IS1008a")
            ],
        },
    )  # fmt: skip
    return root


def test_renders_every_figure_as_valid_svg(tmp_path: Path) -> None:
    rendered, skipped = F.render(F.load_inputs(STAMP, make_root(tmp_path)))
    assert skipped == []
    names = {s.name for s in F.FIGURES}
    expected = {f"{n}.svg" for n in names} | {f"data/{n}.values.json" for n in names}
    assert set(rendered) == expected | {"README.md"}
    for name, data in rendered.items():
        if name.endswith(".svg"):
            ET.fromstring(data)  # well-formed XML
            assert b"<dc:date>" not in data  # no timestamp
    assert b"Why did a tie on synthetic data" in rendered["hero_synthetic_vs_real.svg"]
    assert b"DEV" in rendered["fallback_dev.svg"]
    assert b"DEV" in rendered["deletion_sim.svg"]
    for name in ("retention_steps", "answer_types", "question_heatmap"):
        assert b"DEV" in rendered[f"{name}.svg"]
    assert b"How does Hindsight work?" in rendered["overview.svg"]
    assert b"2 meetings" in rendered["harness_flow.svg"]
    assert b"EN2002b" in rendered["capture_coverage.svg"]


def test_answer_types_partition_every_answer() -> None:
    """correct + confident_error + hedged_miss = 1, and partials sit inside hedged_miss."""
    c = {"accuracy": 0.5, "partial": 0.125, "hedged_miss": 0.375, "confident_error": 0.125}
    shares = FR.answer_types(c)
    assert shares == {"complete": 0.5, "incomplete": 0.125, "says_so": 0.25,
                      "confident_wrong": 0.125}  # fmt: skip
    assert sum(shares.values()) == pytest.approx(1.0)
    assert [k for k, _n, _c in fs.ANSWER_TYPES] == list(shares)


def test_heatmap_greys_out_small_cells(tmp_path: Path) -> None:
    inp = F.load_inputs(STAMP, make_root(tmp_path))
    fig = FR.fig_question_heatmap(inp.deletion)
    tags, cats = F.plotted_values(fig)["axes"]
    fs.close(fig)
    assert all(v is None for row in tags["images"][0] for v in row)  # every tag cell has n=4
    assert all(row == [None, 50.0] for row in cats["images"][0])  # temporal n=4, who_said n=6
    assert ["n=4", 0.0, 0.0] in tags["texts"]  # n still printed in a greyed cell


def test_track_cards_read_their_numbers_from_the_aggregates(tmp_path: Path) -> None:
    inp = F.load_inputs(STAMP, make_root(tmp_path))
    heads = FD._headlines(inp.real, inp.deletion)
    assert "50% with notes only" in heads["retention"]
    assert "n=4" in heads["qa"]
    assert set(heads) <= {t[0] for t in FD.TRACKS}
    assert FD._headlines(None, None) == {}


def test_same_inputs_give_byte_identical_output(tmp_path: Path) -> None:
    inp = F.load_inputs(STAMP, make_root(tmp_path))
    first, _ = F.render(inp)
    second, _ = F.render(F.load_inputs(STAMP, tmp_path))
    assert first == second


def test_missing_optional_inputs_are_skipped_not_fatal(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / F.REAL_SNAPSHOT).unlink()
    rendered, skipped = F.render(F.load_inputs(STAMP, root))
    assert "hero_synthetic_vs_real.svg" not in rendered
    assert any(s.startswith("hero_synthetic_vs_real") for s in skipped)


def test_check_flags_changed_figures(tmp_path: Path) -> None:
    rendered, _ = F.render(F.load_inputs(STAMP, make_root(tmp_path)))
    out = tmp_path / "out"
    F.write(rendered, out)
    assert F.check(rendered, out) == []
    (out / "leaderboard.svg").write_bytes(b"<svg/>")
    assert F.check(rendered, out) == ["leaderboard.svg"]


def test_check_fails_when_a_plotted_number_changes(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    rendered, _ = F.render(F.load_inputs(STAMP, root))
    out = tmp_path / "out"
    F.write(rendered, out)
    # One accuracy moves by one question (0.75 -> 0.5); nothing else changes.
    report = root / "evals" / "reports" / f"{STAMP}.json"
    data = json.loads(report.read_text())
    data["metrics"]["concentrate"]["accuracy"] = 0.5
    data["metrics"]["concentrate"]["accuracy_ci95"] = [0.4, 0.6]
    report.write_text(json.dumps(data))
    changed, _ = F.render(F.load_inputs(STAMP, root))
    bad = F.check(changed, out)
    assert "data/leaderboard.values.json" in bad
    assert "data/cost_frontier.values.json" in bad
    assert "data/by_category.values.json" not in bad  # per-category numbers didn't move


def test_check_ignores_font_and_dpi_differences(tmp_path: Path, monkeypatch: Any) -> None:
    """Another platform's text metrics change SVG bytes, not the check's verdict."""
    inp = F.load_inputs(STAMP, make_root(tmp_path))
    rendered, _ = F.render(inp)
    out = tmp_path / "out"
    F.write(rendered, out)
    rc = {**fs.RC, "font.size": 13, "figure.dpi": 72, "font.sans-serif": ["Helvetica"]}
    monkeypatch.setattr(fs, "RC", rc)
    other, _ = F.render(inp)
    assert any(other[n] != rendered[n] for n in rendered if n.endswith(".svg"))
    assert F.check(other, out) == []


def test_svg_signature_sees_text_and_elements_not_coordinates() -> None:
    def svg(body: str) -> bytes:
        return f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>'.encode()

    base = svg('<path d="M 1.2 2"/><text x="3">92% of</text><text x="9">questions</text>')
    # Coordinates and line wrapping (font-dependent) don't count; words and elements do.
    rewrapped = svg('<path d="M 1.7 2"/><text x="4">92%</text><text x="5">of questions</text>')
    assert F.svg_signature(base) == F.svg_signature(rewrapped)
    assert F.svg_signature(base) != F.svg_signature(
        svg('<path d="M 1"/><text>91% of</text><text>questions</text>')
    )
    assert F.svg_signature(base) != F.svg_signature(
        svg('<path d="M 1"/><path d="M 2"/><text>92% of</text><text>questions</text>')
    )


def test_equal_price_and_amortised_ingest(tmp_path: Path) -> None:
    inp = F.load_inputs(STAMP, make_root(tmp_path))
    assert F.equal_price(1_000_000, 0) == F.PRICE_IN
    assert F.equal_price(0, 1_000_000) == F.PRICE_OUT
    answer = F.equal_price(500, 50)
    ingest = F.equal_price(1000, 100)
    assert F.per_question_cost(inp, "concentrate", 10) == pytest.approx(answer + ingest / 10)
    assert F.per_question_cost(inp, "b0_topk", 10) == pytest.approx(answer)


def test_frontier_drops_dominated_points() -> None:
    pts = [("cheap", 0.01, 70.0), ("good", 0.1, 90.0), ("worse", 0.2, 85.0), ("tie", 0.1, 90.0)]
    assert F.frontier(pts) == ["cheap", "good", "tie"]


def test_wilson_interval_contains_estimate() -> None:
    lo, hi = F.wilson(15, 20)
    assert lo < 0.75 < hi
    assert lo >= 0 and hi <= 1


def test_snapshot_real_keeps_only_aggregates(tmp_path: Path) -> None:
    secret = "PRIVATE MEETING TEXT"

    def row(system: str, qid: str, ok: bool, cat: str = "who_said") -> str:
        return json.dumps(
            {
                "system": system,
                "qid": qid,
                "category": cat,
                "correct": ok,
                "verdict": "correct" if ok else "incorrect",
                "question": secret,
                "answer": {"answer": secret},
                "gold": secret,
            }
        )

    main = tmp_path / "main.jsonl"
    main.write_text(
        "\n".join(
            [row("granola_agent", q, True) for q in ("a", "b")]
            + [row("concentrate", "a", False), row("concentrate", "b", True)]
        )
    )
    topk = tmp_path / "topk.jsonl"
    topk.write_text("\n".join([row("b0_topk", q, True) for q in ("a", "b", "c")]))
    fb = tmp_path / "fb.jsonl"
    fb_rows = [
        row("concentrate", "a", False),
        row("concentrate_on_abstain", "a", True),
        row("concentrate", "b", True),
        row("concentrate_on_abstain", "b", True),
    ]
    fb.write_text("\n".join(fb_rows))
    snap = F.snapshot_real(main, topk, fb)
    assert secret not in json.dumps(snap)
    assert snap["systems"]["b0_topk"]["n"] == 2  # restricted to the main run's questions
    assert snap["paired"]["granola_agent|concentrate"]["a_only"] == 1
    assert snap["fallback_dev"]["won"] == 1 and snap["fallback_dev"]["lost"] == 0


COMMITTED = "20261103T002456Z"


@pytest.mark.skipif(
    not Path(f"evals/reports/{COMMITTED}.json").exists(), reason="committed report missing"
)
def test_committed_figures_are_up_to_date() -> None:
    rendered, _ = F.render(F.load_inputs(COMMITTED))
    assert F.check(rendered, F.OUT_DIR) == [], f"run `uv run concentrate figures {COMMITTED}`"

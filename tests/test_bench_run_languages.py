"""Tests for benchmarks.run_languages: tier dispatch, summary file and resume (no subprocess)."""

import json

import pytest

from benchmarks import corpora, runner
from benchmarks import run_languages as rl


@pytest.fixture
def pinned(tmp_path):
    return runner.PinnedNexus(venv=tmp_path / ".venv-x", version="2.2.2", ref="0430f01")


def _corpus(**over):
    c = {
        "name": "demo", "language": "Rust", "repo": "o/demo", "url": "u", "pin": "p",
        "tiers": ["quick", "retrieval", "graph", "live"], "suite": "demo", "sparse": [],
        "tasks": "benchmarks/tasks/demo.yaml",
    }
    c.update(over)
    return c


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(rl, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(corpora, "checkout", lambda c: calls.append(("checkout", c["name"])))
    monkeypatch.setattr(runner, "ensure_pinned_index", lambda p, d: calls.append(("index", d.name)))
    monkeypatch.setattr(rl, "index_meta", lambda p, c: {"index_seconds": 1.0})
    def make(tier):
        def stub(*a, **k):
            calls.append((tier,))
            return {"t": tier}

        return stub

    for tier in ("quick", "retrieval", "graph", "live"):
        monkeypatch.setattr(rl, f"tier_{tier}", make(tier))
    return calls


def test_child_env_drops_pythonpath_and_puts_the_venv_first(monkeypatch, pinned):
    monkeypatch.setenv("PYTHONPATH", "/some/src")
    env = rl._child_env(pinned)
    assert "PYTHONPATH" not in env
    assert env["PATH"].startswith(str(pinned.venv / "bin"))
    assert env["NEXUS_EMBEDDING_DEVICE"] == "cpu"


def test_run_corpus_runs_every_tier_the_corpus_has(stubbed, pinned):
    row = rl.run_corpus(_corpus(), pinned, list(rl.ALL_TIERS), "before", None)
    assert [k for k in ("quick", "retrieval", "graph", "live") if k in row] == list(rl.ALL_TIERS)
    assert row["nexus_version"] == "2.2.2" and row["label"] == "before"
    assert ("index", "demo") in stubbed


def test_run_corpus_skips_a_tier_the_corpus_does_not_list(stubbed, pinned):
    row = rl.run_corpus(_corpus(tiers=["quick"]), pinned, list(rl.ALL_TIERS), "before", None)
    assert "quick" in row and "live" not in row and "graph" not in row
    assert not any(c[0] == "index" for c in stubbed)  # quick needs no pinned index


def test_run_corpus_free_tiers_never_start_the_paid_tier(stubbed, pinned):
    rl.run_corpus(_corpus(), pinned, ["quick", "retrieval", "graph"], "before", None)
    assert ("live",) not in stubbed


def test_main_writes_the_summary_and_keeps_earlier_rows(stubbed, pinned, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "load_pinned", lambda venv: pinned)
    monkeypatch.setattr(corpora, "load", lambda: [_corpus(name="a"), _corpus(name="b")])
    assert rl.main(["--nexus-venv", "v", "--label", "t", "--languages", "rust",
                    "--tiers", "quick"]) == 0
    summary = json.loads((rl.RESULTS_DIR / "languages-t.json").read_text())
    assert set(summary["rows"]) == {"a", "b"}
    # A second run on one corpus adds its tier and keeps the other row.
    assert rl.main(["--nexus-venv", "v", "--label", "t", "--tiers", "graph"]) == 0
    summary = json.loads((rl.RESULTS_DIR / "languages-t.json").read_text())
    assert "quick" in summary["rows"]["a"] and "graph" in summary["rows"]["a"]


def test_main_exit_code_3_when_the_usage_limit_stops_the_live_tier(
    stubbed, pinned, monkeypatch
):
    def limit(*a, **k):
        raise runner.UsageLimitReached("limit")

    monkeypatch.setattr(runner, "load_pinned", lambda venv: pinned)
    monkeypatch.setattr(corpora, "load", lambda: [_corpus()])
    monkeypatch.setattr(rl, "tier_live", limit)
    assert rl.main(["--nexus-venv", "v", "--label", "t", "--tiers", "live"]) == 3


def test_main_a_failed_tier_gives_exit_code_1_and_continues(stubbed, pinned, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("quick tier failed")

    monkeypatch.setattr(runner, "load_pinned", lambda venv: pinned)
    monkeypatch.setattr(corpora, "load", lambda: [_corpus(name="a"), _corpus(name="b")])
    monkeypatch.setattr(rl, "tier_quick", lambda c, *a, **k: boom() if c["name"] == "a" else {})
    assert rl.main(["--nexus-venv", "v", "--label", "t", "--tiers", "quick"]) == 1
    summary = json.loads((rl.RESULTS_DIR / "languages-t.json").read_text())
    assert set(summary["rows"]) == {"b"}


def test_main_rejects_an_unknown_tier(pinned, monkeypatch):
    monkeypatch.setattr(runner, "load_pinned", lambda venv: pinned)
    with pytest.raises(SystemExit):
        rl.main(["--nexus-venv", "v", "--label", "t", "--tiers", "fast"])

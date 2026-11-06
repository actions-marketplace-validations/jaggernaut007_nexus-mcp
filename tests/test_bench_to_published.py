"""benchmarks/to_published.py turns run summaries into published language entries."""

import json

import pytest

from benchmarks import to_published


def _row(language="Java", live=True, hit=0.8, tokens=1000):
    row = {
        "name": "commons-lang", "language": language, "repo": "apache/commons-lang",
        "url": "https://github.com/apache/commons-lang.git", "pin": "abc",
        "quick": {
            "created": "2026-11-06T06:00:00+00:00",
            "repo": {"files": 264, "index_seconds": 12.5},
            "summary": {"grep": {"hit_at_1": 0.2, "median_tokens": 9000},
                        "nexus": {"hit_at_1": hit, "median_tokens": tokens}},
            "verdict": {"label": "Strong fit"},
        },
        "retrieval": {"modes": {"hybrid-source-first": {"hit@1": 0.733}}},
        "graph": {"java": {"functions": 100, "calls": 75}},
    }
    if live:
        side = {"total_tokens": 30000.4, "total_cost_usd": 0.0221, "tasks_correct": 6, "n_tasks": 6}
        row["live"] = {"model": "sonnet", "effort": "medium", "isolation": ["reduced"],
                       "conditions": {"baseline": dict(side, total_tokens=50000), "nexus": side}}
    return row


@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(to_published, "RESULTS_DIR", tmp_path)

    def write(label, rows):
        text = json.dumps({"label": label, "rows": rows})
        (tmp_path / f"languages-{label}.json").write_text(text)
    return write


def test_build_writes_before_and_after_for_a_named_language(results):
    results("before", {"commons-lang": _row()})
    results("final", {"commons-lang": _row(hit=0.9, tokens=900)})
    (entry,) = to_published.build(["Java"], "before", "final", "2.2.2", "2.4.0")
    assert entry["project"] == "apache/commons-lang"
    assert entry["url"] == "https://github.com/apache/commons-lang"
    assert entry["before"]["quick_tier"]["nexus"] == {"hit_at_1": 0.8, "median_tokens": 1000}
    assert entry["after"]["quick_tier"]["nexus"]["hit_at_1"] == 0.9
    assert entry["after"]["live"]["nexus"] == {
        "median_tokens": 30000, "cost_usd": 0.022, "correct": 6, "of": 6,
    }
    assert entry["after"]["graph"] == {"fn_nodes": 100, "calls_per_fn": 0.75}
    assert entry["isolation"] == "reduced"


def test_build_drops_an_after_run_without_the_live_tier(results):
    results("before", {"commons-lang": _row()})
    results("final", {"commons-lang": _row(live=False)})
    (entry,) = to_published.build(["Java"], "before", "final", "2.2.2", "2.4.0")
    assert "after" not in entry
    assert entry["before"]["live"]["baseline"]["median_tokens"] == 50000


def test_build_stops_for_a_language_without_a_live_run(results):
    results("before", {"commons-lang": _row(live=False)})
    with pytest.raises(SystemExit):
        to_published.build(["Java"], "before", None, "2.2.2", "")


def test_build_leaves_out_languages_that_are_not_named(results):
    php = dict(_row(language="PHP"), name="laravel")
    results("before", {"commons-lang": _row(), "laravel": php})
    entries = to_published.build(["Java"], "before", None, "2.2.2", "")
    assert [e["language"] for e in entries] == ["Java"]

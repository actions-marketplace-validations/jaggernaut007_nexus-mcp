"""benchmarks/latest_published.py keeps the latest run of each repo and tier."""

import json

import pytest

from benchmarks import latest_published as lp


def _row(name="commons-lang", language="Java", quick_tokens=None, live=None, ref="abc1234"):
    row = {
        "name": name, "language": language, "repo": f"owner/{name}",
        "url": f"https://github.com/owner/{name}.git", "pin": "f" * 40,
        "nexus_version": "2.2.2", "nexus_ref": ref + "0" * 33,
    }  # fmt: skip
    if quick_tokens is not None:
        row["quick"] = {
            "created": "2026-11-06T06:00:00+00:00",
            "questions": 30,
            "repo": {"files": 264, "index_seconds": 12.5},
            "summary": {
                "grep": {"hit_at_1": 0.2, "hit_at_3": 0.5, "median_tokens": 9000.5,
                         "median_ms": 200.0},
                "nexus": {"hit_at_1": 0.9, "hit_at_3": 1.0, "median_tokens": quick_tokens,
                          "median_ms": 25.0},
            },  # fmt: skip
            "verdict": {"label": "Strong fit"},
        }
        row["retrieval"] = {"modes": {"hybrid-source-first": {"hit@1": 0.733}}}
        row["graph"] = {"java": {"functions": 100, "calls": 75}}
    if live is not None:
        row["live"] = {"model": "sonnet", "effort": "medium", "reps": 3, "conditions": live}
    return row


def _side(tokens, cost=0.03, correct=6, turns=3.0):
    return {"total_tokens": tokens, "total_cost_usd": cost, "tasks_correct": correct,
            "n_tasks": 6, "num_turns": turns, "wall_seconds": 20.04}  # fmt: skip


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(lp, "REPOS", ["commons-lang", "redis"])

    def write(label, rows):
        folder = tmp_path / label
        folder.mkdir()
        (folder / f"{label}.json").write_text(json.dumps({"label": label, "rows": rows}))

    return tmp_path, write


def test_build_keeps_the_latest_quick_run(runs):
    path, write = runs
    write("languages-before", {"commons-lang": _row(quick_tokens=2000)})
    write("languages-final", {"commons-lang": _row(quick_tokens=1200, ref="11cfccc")})
    (entry,) = lp.build(path)
    assert entry["quick"]["nexus"]["median_tokens"] == 1200
    assert entry["quick"]["run"] == "languages-final"
    assert entry["quick"]["build"] == "2.2.2+11cfccc"
    assert entry["url"] == "https://github.com/owner/commons-lang"
    assert entry["files"] == 264 and entry["retrieval_hit_at_1"] == 0.73
    assert entry["calls_per_fn"] == 0.75


def test_build_pairs_the_latest_side_of_each_condition(runs):
    path, write = runs
    write("languages-before", {
        "commons-lang": _row(quick_tokens=2000,
                             live={"baseline": _side(50000), "nexus": _side(40000)}),
    })  # fmt: skip
    write("languages-v2.4.1", {"commons-lang": _row(live={"nexus": _side(30000, cost=0.02)})})
    (entry,) = lp.build(path)
    live = entry["live"]
    assert live["baseline"]["median_tokens"] == 50000
    assert live["baseline"]["run"] == "languages-before"
    assert live["baseline"]["build"] == "built-in tools"
    assert live["nexus"]["median_tokens"] == 30000 and live["nexus"]["build"] == "2.2.2"
    assert live["nexus"]["wall_seconds"] == 20.0


def test_build_reads_a_live_only_run_from_jsonl(runs):
    path, write = runs
    write("languages-before", {
        "redis": _row(name="redis", language="C", quick_tokens=900,
                      live={"baseline": _side(41000), "nexus": _side(66000)}),
    })  # fmt: skip
    folder = path / "live-opt2"
    folder.mkdir()
    records = [
        {"task_id": t, "condition": "nexus", "total_tokens": tok, "total_cost_usd": 0.03,
         "num_turns": 3, "wall_seconds": 20, "mechanical_correct": True, "rep": rep,
         "model": "sonnet", "effort": "medium", "nexus_version": "2.2.2",
         "nexus_ref": "e5a9d9a" + "0" * 33}
        for t, tok in (("a", 50000), ("b", 52000)) for rep in (0, 1, 2)
    ]  # fmt: skip
    (folder / "redis.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    (entry,) = lp.build(path)
    assert entry["live"]["nexus"]["median_tokens"] == 51000
    assert entry["live"]["nexus"]["build"] == "2.2.2+e5a9d9a"
    assert entry["live"]["nexus"]["correct"] == 2 and entry["live"]["reps"] == 3
    assert entry["live"]["baseline"]["median_tokens"] == 41000


def test_build_skips_a_crashed_tier_and_an_unknown_repo(runs):
    path, write = runs
    crashed = _row(quick_tokens=1000)
    crashed["quick"] = {"status": "error"}
    write("languages-before", {"commons-lang": _row(quick_tokens=2000)})
    write("languages-final", {"commons-lang": crashed})
    (entry,) = lp.build(path)
    assert entry["quick"]["nexus"]["median_tokens"] == 2000
    assert "live" not in entry


def test_build_label_of_a_release_run():
    release = {"nexus_version": "2.4.1", "nexus_ref": "195090c"}
    assert lp.build_label(release, "languages-v2.4.1") == "2.4.1"
    assert lp.build_label({"nexus_version": "2.4.0"}, "languages-py-fix") == "2.4.0"


def test_published_repos_are_current():
    assert lp.main(["--check"]) == 0

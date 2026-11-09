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


def _side(tokens, cost=0.03, correct=6, turns=3.0, n_tasks=6):
    return {"total_tokens": tokens, "total_cost_usd": cost, "tasks_correct": correct,
            "n_tasks": n_tasks, "num_turns": turns, "wall_seconds": 20.04}  # fmt: skip


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
                      live={"baseline": _side(41000, n_tasks=2), "nexus": _side(66000, n_tasks=2)}),
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


def test_build_skips_a_crashed_tier(runs):
    path, write = runs
    crashed = _row(quick_tokens=1000)
    crashed["quick"] = {"status": "error"}
    write("languages-before", {"commons-lang": _row(quick_tokens=2000)})
    write("languages-final", {"commons-lang": crashed})
    (entry,) = lp.build(path)
    assert entry["quick"]["nexus"]["median_tokens"] == 2000
    assert "live" not in entry


def test_build_drops_a_repo_with_no_repo_field(runs):
    path, write = runs
    bad = _row(quick_tokens=1000)
    del bad["repo"]
    write("languages-before", {"commons-lang": bad})
    assert lp.build(path) == []


def test_build_missing_runs_dir_gives_no_entries(tmp_path):
    assert lp.build(tmp_path / "none") == []


def test_repo_entry_zero_functions_gives_no_calls_per_fn(runs):
    path, write = runs
    row = _row(quick_tokens=1000)
    row["graph"] = {"java": {"functions": 0, "calls": 0}}
    write("languages-before", {"commons-lang": row})
    (entry,) = lp.build(path)
    assert entry["calls_per_fn"] is None


def test_check_pair_rejects_sides_with_another_setup(runs):
    path, write = runs
    write("languages-before", {
        "commons-lang": _row(live={"baseline": _side(50000), "nexus": _side(40000)}),
    })  # fmt: skip
    later = _row(live={"nexus": _side(30000, n_tasks=5)})
    write("languages-v2.4.1", {"commons-lang": later})
    with pytest.raises(ValueError, match="6 and 5 tasks|5 and 6 tasks"):
        lp.build(path)
    later["live"]["conditions"]["nexus"]["n_tasks"] = 6
    later["live"]["effort"] = "high"
    (path / "languages-v2.4.1" / "languages-v2.4.1.json").write_text(
        json.dumps({"label": "v2.4.1", "rows": {"commons-lang": later}})
    )
    with pytest.raises(ValueError, match="effort"):
        lp.build(path)


def test_live_only_rows_skips_an_empty_and_a_blank_jsonl(tmp_path):
    (tmp_path / "empty.jsonl").write_text("")
    (tmp_path / "blank.jsonl").write_text("\n  \n")
    assert lp.live_only_rows(tmp_path) == {}


def test_unlisted_runs_names_a_folder_missing_from_order(tmp_path):
    (tmp_path / "languages-before").mkdir()
    (tmp_path / "setup").mkdir()
    (tmp_path / "languages-new").mkdir()
    assert lp.unlisted_runs(tmp_path) == ["languages-new"]


def test_unlisted_runs_skips_tool_comparison_folders(tmp_path):
    (tmp_path / "competitors-2026-11-09").mkdir()
    (tmp_path / "languages-new").mkdir()
    assert lp.unlisted_runs(tmp_path) == ["languages-new"]


def test_load_runs_never_reads_a_tool_comparison_folder(tmp_path):
    folder = tmp_path / "competitors-2026-11-09"
    folder.mkdir()
    (folder / "django-live.jsonl").write_text(json.dumps({"task_id": "t", "condition": "nexus"}))
    assert all(rows == {} for rows in lp.load_runs(tmp_path))


def test_unlisted_runs_is_empty_for_the_committed_runs():
    assert lp.unlisted_runs() == []


def test_main_check_returns_1_when_out_of_date(tmp_path, monkeypatch, capsys):
    pub = tmp_path / "results.json"
    pub.write_text(json.dumps({"repos": [{"name": "stale"}]}))
    monkeypatch.setattr(lp, "PUBLISHED", pub)
    monkeypatch.setattr(lp, "build", lambda *a, **k: [{"name": "fresh"}])
    assert lp.main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().out


def test_main_writes_repos_and_keeps_other_keys(tmp_path, monkeypatch):
    pub = tmp_path / "results.json"
    pub.write_text(json.dumps({"method": {"k": 1}, "repos": []}))
    monkeypatch.setattr(lp, "PUBLISHED", pub)
    monkeypatch.setattr(lp, "ROOT", tmp_path)
    monkeypatch.setattr(lp, "build", lambda *a, **k: [{"name": "x"}])
    assert lp.main([]) == 0
    saved = json.loads(pub.read_text())
    assert saved["repos"] == [{"name": "x"}] and saved["method"] == {"k": 1}
    assert lp.main(["--check"]) == 0


def test_build_label_of_a_release_run():
    release = {"nexus_version": "2.4.1", "nexus_ref": "195090c"}
    assert lp.build_label(release, "languages-v2.4.1") == "2.4.1"
    assert lp.build_label({"nexus_version": "2.4.0"}, "languages-py-fix") == "2.4.0"


def test_published_repos_are_current():
    assert lp.main(["--check"]) == 0

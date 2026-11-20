"""The change-safety tasks prove themselves without a model (ADR-028).

For each task: the starting project passes its visible tests, fails with the hidden ones,
passes with the reference change, and fails with the typical partial change. A scorer that
cannot tell these apart would make a live run meaningless.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from benchmarks.change_safety import report, run, score

TASKS = score.load_tasks()


def _node_can_strip_types() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
    match = re.match(r"v(\d+)\.(\d+)", out)
    return bool(match) and (int(match[1]), int(match[2])) >= (23, 6)


needs_node = pytest.mark.skipif(not _node_can_strip_types(), reason="node 23.6+ needed")


def _params():
    for task in TASKS:
        marks = [needs_node] if task["language"] == "typescript" else []
        yield pytest.param(task, id=task["id"], marks=marks)


def _project(tmp_path, task, solution=None):
    score.materialize(task, tmp_path)
    if solution:
        score.write_files(tmp_path, task[solution])
    return tmp_path


def test_load_tasks_has_python_and_typescript_and_a_boundary_task_in_each():
    by_language = {}
    for task in TASKS:
        by_language.setdefault(task["language"], set()).add(task["kind"])
    assert set(by_language) == {"python", "typescript"}
    assert all("boundary" in kinds for kinds in by_language.values())


def test_load_tasks_rejects_a_task_without_sites(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("tasks:\n  - id: x\n    language: python\n")
    with pytest.raises(ValueError, match="missing"):
        score.load_tasks(path)


@pytest.mark.parametrize("task", _params())
def test_task_start_state_passes_its_visible_tests(tmp_path, task):
    project = _project(tmp_path, task)
    result = score.run_tests(project, task["test_command"])
    assert result["failed"] == 0 and result["passed"] > 0, result["output_tail"]


@pytest.mark.parametrize("task", _params())
def test_task_start_state_fails_the_hidden_tests_and_misses_every_site(tmp_path, task):
    result = score.score_project(_project(tmp_path, task), task)
    assert not result["safe"]
    assert result["tests_failed"] > 0
    assert result["missed_sites"] == result["sites_total"]


@pytest.mark.parametrize("task", _params())
def test_task_reference_change_is_safe(tmp_path, task):
    result = score.score_project(_project(tmp_path, task, "reference"), task)
    assert result["safe"], result
    assert result["missed_sites"] == 0 and result["tests_failed"] == 0


@pytest.mark.parametrize("task", _params())
def test_task_partial_change_is_not_safe_and_leaves_sites_behind(tmp_path, task):
    result = score.score_project(_project(tmp_path, task, "partial"), task)
    assert not result["safe"]
    assert result["tests_failed"] > 0, "a partial change must break a test"
    assert 0 < result["missed_sites"] < result["sites_total"]


def test_each_task_marks_at_most_one_unseen_site_and_names_its_file():
    for task in TASKS:
        files = {s["file"] for s in task["sites"]}
        assert files <= set(task["files"]), task["id"]
        assert sum(1 for s in task["sites"] if s.get("unseen")) <= 1


def test_acceptance_tests_are_not_in_the_starting_project(tmp_path):
    for task in TASKS:
        assert not set(task["acceptance"]) & set(task["files"]), task["id"]


def test_parse_test_output_reads_pytest_and_node_and_counts_a_crash_as_a_failure():
    assert score.parse_test_output("1 failed, 4 passed in 0.1s", 1) == {"failed": 1, "passed": 4}
    assert score.parse_test_output("3 passed in 0.1s", 0) == {"failed": 0, "passed": 3}
    assert score.parse_test_output("# pass 2\n# fail 1\n", 1) == {"failed": 1, "passed": 2}
    assert score.parse_test_output("ℹ pass 5\nℹ fail 0\n", 0) == {"failed": 0, "passed": 5}
    assert score.parse_test_output("ImportError", 2) == {"failed": 1, "passed": 0}


def test_summarize_gives_safe_rate_and_missed_site_rate():
    rows = [
        {"safe": True, "sites_total": 4, "missed_sites": 0, "tests_failed": 0},
        {"safe": False, "sites_total": 4, "missed_sites": 2, "tests_failed": 3},
    ]
    assert score.summarize(rows) == {
        "runs": 2, "safe_rate": 0.5, "missed_site_rate": 0.25, "tests_failed": 3,
    }
    assert score.summarize([])["safe_rate"] is None


def test_site_missed_is_true_for_a_missing_file(tmp_path):
    assert score.site_missed(tmp_path, {"file": "gone.py", "stale": ["x"]})


# --- live runner and report: nothing here starts a model -----------------------------

def _task(task_id):
    return next(t for t in TASKS if t["id"] == task_id)


def test_bash_pattern_allows_only_the_test_command():
    assert run.bash_pattern("python -m pytest -q -p no:cacheprovider") == "Bash(python -m pytest:*)"
    assert run.bash_pattern("node --test tests/*.test.ts") == "Bash(node --test:*)"


def test_build_command_gives_both_conditions_the_same_edit_tools_and_no_bypass(tmp_path):
    task = _task("py-add-required-parameter")
    base = run.build_command(task, "baseline", "sonnet", tmp_path)["argv"]
    nexus = run.build_command(task, "nexus", "sonnet", tmp_path)["argv"]
    for argv in (base, nexus):
        assert "--dangerously-skip-permissions" not in argv
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--tools") + 1] == run.CHANGE_TOOLS
        allowed = argv[argv.index("--allowedTools") + 1]
        assert "Edit" in allowed and "Write" in allowed and "Bash(python -m pytest:*)" in allowed
        assert "Edit" not in argv[argv.index("--disallowedTools") + 1].split(",")
        assert argv[-2] == "--" and "Run `python -m pytest" in argv[-1]
    assert "--mcp-config" not in base and "--mcp-config" in nexus


def test_task_prompt_never_leaks_the_sites_or_the_acceptance_tests():
    for task in TASKS:
        prompt = run.task_prompt(task)
        for path in task["acceptance"]:
            assert path not in prompt
        assert "reference" not in prompt.lower().split()


def test_main_is_a_dry_run_without_go_and_starts_nothing(tmp_path, capsys, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a dry run started a process")

    monkeypatch.setattr(run.subprocess, "Popen", refuse)
    monkeypatch.setattr(run.runner, "require_login", refuse)
    code = run.main(["--out", str(tmp_path / "out.jsonl"), "--reps", "1"])
    out = capsys.readouterr().out
    assert code == 0 and "Dry run" in out and "at most $" in out
    assert out.count("rep 0") == len(TASKS) * 2


def test_main_skips_runs_that_are_already_recorded(tmp_path, capsys):
    out = tmp_path / "out.jsonl"
    done = {"task_id": "py-rename-route", "condition": "baseline", "rep": 0,
            "model": "sonnet", "effort": "medium"}
    out.write_text(json.dumps(done) + "\n")
    run.main(["--out", str(out), "--reps", "1", "--tasks", "py-rename-route"])
    text = capsys.readouterr().out
    assert "1 runs to do (1 already done)" in text


def test_report_groups_by_condition_and_task():
    rows = [
        {"task_id": "a", "condition": "baseline", "safe": True, "sites_total": 4,
         "missed_sites": 0, "tests_failed": 0, "total_cost_usd": 0.5, "wall_seconds": 60,
         "num_turns": 10},
        {"task_id": "a", "condition": "nexus", "safe": False, "sites_total": 4,
         "missed_sites": 1, "tests_failed": 2, "total_cost_usd": 0.3, "wall_seconds": 40,
         "num_turns": 6},
    ]
    text = report.render(rows)
    assert "| baseline | 1 | 1.00 | 0.00 |" in text
    assert "| nexus | 1 | 0.00 | 0.25 |" in text
    assert "| a / nexus |" in text


def test_report_load_reads_jsonl(tmp_path):
    path = Path(tmp_path / "r.jsonl")
    path.write_text('{"a": 1}\n\n{"a": 2}\n')
    assert report.load(path) == [{"a": 1}, {"a": 2}]

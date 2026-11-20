"""The agent hook of `check`: silent unless a new high finding exists (ADR-027).

A real git repository, a graph from the real parsers, and the real check. Only the stdin of
the process is replaced.
"""

import io
import json
import os
import subprocess
import sys

from nexus_mcp.evaluate import check, cli
from tests.test_check import PY_BASE, _commit_and_index, _write, repo  # noqa: F401

SIGNATURE_CHANGE = {"lib.py": "def total(a, b, c):\n    return a + b + c\n"}
BODY_CHANGE = {"lib.py": "def total(a, b):\n    return b + a\n"}


def test_hook_decision_blocks_on_a_new_high_finding(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    decision = check.hook_decision(repo, store)
    assert decision["decision"] == "block"
    assert "app.py" in decision["reason"]
    assert "tests/test_app.py" in decision["reason"]
    assert len(decision["reason"]) < 1200


def test_hook_decision_is_silent_for_the_same_finding_the_second_time(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    assert check.hook_decision(repo, store) is not None
    assert check.hook_decision(repo, store) is None


def test_hook_decision_speaks_again_after_the_findings_went_and_came_back(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    assert check.hook_decision(repo, store) is not None
    _write(repo, PY_BASE)  # the change is undone: quiet, and the digest is forgotten
    assert check.hook_decision(repo, store) is None
    _write(repo, SIGNATURE_CHANGE)
    assert check.hook_decision(repo, store) is not None


def test_hook_decision_is_silent_when_the_hook_already_blocked_this_stop(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    assert check.hook_decision(repo, store, {"stop_hook_active": True}) is None


def test_hook_decision_is_silent_when_switched_off(repo, monkeypatch):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    monkeypatch.setenv("NEXUS_HOOK", "off")
    assert check.hook_decision(repo, store) is None


def test_hook_decision_is_silent_for_a_medium_finding(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    _write(repo, BODY_CHANGE)
    report = check.run_check(repo, store)
    assert report.findings and all(f.severity != "high" for f in report.findings)
    assert check.hook_decision(repo, store) is None


def test_hook_decision_is_silent_without_an_index(repo, tmp_path):  # noqa: F811
    _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    assert check.hook_decision(repo, tmp_path / "no-index") is None


def test_hook_decision_is_silent_outside_a_git_repository(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert check.hook_decision(folder, folder / ".nexus") is None


def test_hook_decision_is_silent_when_nothing_changed(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_BASE)
    assert check.hook_decision(repo, store) is None


def test_hook_reason_caps_the_findings_and_names_the_rest(repo):  # noqa: F811
    files = {"lib.py": PY_BASE["lib.py"]}
    for i in range(9):
        files[f"user{i}.py"] = (
            f"from lib import total\n\n\ndef run{i}():\n    return total({i}, 1)\n"
        )
    store = _commit_and_index(repo, files)
    _write(repo, SIGNATURE_CHANGE)
    reason = check.hook_decision(repo, store)["reason"]
    assert reason.count("\n- ") == check.HOOK_MAX_FINDINGS + 1
    assert "and 3 more" in reason


def test_run_check_hook_prints_one_json_line_and_exits_zero(repo, monkeypatch, capsys):  # noqa: F811
    _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(repo)})))
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    assert cli.run_check(["--hook"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["decision"] == "block"


def _hook_process(folder, stdin: str):
    """The hook as Claude Code starts it: a new process, the hook JSON on stdin."""
    env = {k: v for k, v in os.environ.items() if k != "NEXUS_STORAGE_DIR"}
    return subprocess.run(
        [sys.executable, "-c", "from nexus_mcp.server import main; main()", "check", "--hook"],
        input=stdin, cwd=folder, env=env, capture_output=True, text=True, timeout=60,
    )


def test_hook_process_prints_a_block_once_then_stays_silent(repo):  # noqa: F811
    _commit_and_index(repo, PY_BASE)
    _write(repo, SIGNATURE_CHANGE)
    first = _hook_process(repo, json.dumps({"cwd": str(repo), "stop_hook_active": False}))
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["decision"] == "block"
    second = _hook_process(repo, json.dumps({"cwd": str(repo)}))
    assert (second.returncode, second.stdout) == (0, "")


def test_hook_process_is_silent_and_exits_zero_in_a_folder_with_no_index(tmp_path):
    result = _hook_process(tmp_path, "{}")
    assert (result.returncode, result.stdout) == (0, "")


def test_run_check_hook_survives_a_broken_stdin(repo, monkeypatch, capsys):  # noqa: F811
    _commit_and_index(repo, PY_BASE)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.run_check([str(repo), "--hook"]) == 0
    assert capsys.readouterr().out == ""

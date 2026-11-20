"""Score a change: which call sites did the agent leave behind, and which tests fail?

A task (see `tasks.yaml`) is a tiny project, a change request, hidden acceptance tests, and a
list of call sites. After the agent edits a copy of the project, this module adds the hidden
tests, runs the test command, and checks each site for text that the change had to remove.

The result has two numbers that no agent can argue with:
  missed_sites   sites whose file still holds the old form (a regex that must be gone)
  tests_failed   tests that fail with the acceptance tests in place
A change is `safe` when both are zero. Pure functions and one subprocess call; no LLM.
"""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

import yaml

TASKS_FILE = Path(__file__).resolve().parent / "tasks.yaml"
TEST_TIMEOUT_S = 120


def load_tasks(path: Path = TASKS_FILE) -> List[Dict[str, Any]]:
    """The tasks of a file. Raises ValueError on a task that cannot be scored."""
    tasks = yaml.safe_load(Path(path).read_text())["tasks"]
    seen = set()
    for task in tasks:
        for key in ("id", "language", "prompt", "test_command", "files", "acceptance",
                    "reference", "partial", "sites"):
            if key not in task:
                raise ValueError(f"{task.get('id', '?')}: missing {key}")
        if task["id"] in seen:
            raise ValueError(f"duplicate task id {task['id']}")
        seen.add(task["id"])
    return tasks


def write_files(dest: Path, files: Mapping[str, str]) -> None:
    for rel, text in files.items():
        path = Path(dest) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def materialize(task: Mapping[str, Any], dest: Path) -> Path:
    """Write the starting project of a task into `dest`. The acceptance tests are not in it."""
    Path(dest).mkdir(parents=True, exist_ok=True)
    write_files(dest, task["files"])
    return Path(dest)


def parse_test_output(text: str, returncode: int) -> Dict[str, int]:
    """Failed and passed counts from pytest (`2 failed, 3 passed`) or node:test (`# fail 2`).

    A run that fails with no count (an import error before collection, a crash) has an
    unknown number of failures, so it counts as one: a broken project is never safe.
    """
    failed = passed = None
    node_fail = re.search(r"^(?:#|ℹ) fail (\d+)", text, re.M)  # TAP or the spec reporter
    node_pass = re.search(r"^(?:#|ℹ) pass (\d+)", text, re.M)
    if node_fail and node_pass:
        failed, passed = int(node_fail.group(1)), int(node_pass.group(1))
    else:
        for count, word in re.findall(r"(\d+) (failed|passed|error|errors)\b", text):
            if word == "passed":
                passed = (passed or 0) + int(count)
            else:
                failed = (failed or 0) + int(count)
    failed, passed = failed or 0, passed or 0
    if returncode != 0 and failed == 0:
        failed = 1
    return {"failed": failed, "passed": passed}


def run_tests(project: Path, command: str, timeout_s: int = TEST_TIMEOUT_S) -> Dict[str, Any]:
    """Run the test command of a task in `project`. `python` means this interpreter."""
    argv = shlex.split(command)
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    try:
        proc = subprocess.run(
            argv, cwd=str(project), capture_output=True, text=True, timeout=timeout_s,
            env=_test_env(),
        )
        output, code = proc.stdout + "\n" + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        output, code = f"timeout after {timeout_s}s\n{exc.stdout or ''}", 124
    counts = parse_test_output(output, code)
    return {**counts, "returncode": code, "output_tail": output[-1500:]}


def _test_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("NEXUS_STORAGE_DIR", "PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def site_missed(project: Path, site: Mapping[str, Any]) -> bool:
    """True when the file of `site` still matches a `stale` regex, or is gone."""
    path = Path(project) / site["file"]
    if not path.is_file():
        return True
    text = path.read_text(errors="replace")
    return any(re.search(pattern, text) for pattern in site["stale"])


def score_project(project: Path, task: Mapping[str, Any]) -> Dict[str, Any]:
    """Add the acceptance tests to `project`, run the tests, check the sites."""
    write_files(project, task["acceptance"])
    tests = run_tests(project, task["test_command"])
    missed = [s["file"] for s in task["sites"] if site_missed(project, s)]
    unseen = {s["file"] for s in task["sites"] if s.get("unseen")}
    return {
        "task_id": task["id"],
        "sites_total": len(task["sites"]),
        "missed_sites": len(missed),
        "missed_files": missed,
        "missed_unseen": sorted(set(missed) & unseen),
        "tests_failed": tests["failed"],
        "tests_passed": tests["passed"],
        "safe": not missed and tests["failed"] == 0 and tests["passed"] > 0,
        "test_output_tail": tests["output_tail"] if tests["failed"] else "",
    }


def summarize(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Totals over scored runs: the share that is safe, and the sites and tests left behind."""
    n = len(rows)
    sites = sum(r["sites_total"] for r in rows)
    return {
        "runs": n,
        "safe_rate": sum(1 for r in rows if r["safe"]) / n if n else None,
        "missed_site_rate": sum(r["missed_sites"] for r in rows) / sites if sites else None,
        "tests_failed": sum(r["tests_failed"] for r in rows),
    }


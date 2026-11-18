"""How much of a partial change does `nexus-mcp-ci check` see? (ADR-028, offline, no model)

The change-safety tasks have a `partial` solution: the definition and the obvious caller are
updated, the rest is left behind. The files that the scorer calls missed are the ground truth,
written in `tasks.yaml` from the code. This test asks the check which of them it names. The
share is the offline signal of the benchmark: the live run shows whether an agent with these
tools leaves fewer sites behind.

Honest limits: a computed name (`unseen: true`) is a known miss, and the check reads a git
diff, which an agent does not have to run.
"""

import pytest

from benchmarks.change_safety import score
from nexus_mcp.evaluate import check
from tests.test_check import _commit_and_index, _write, repo  # noqa: F401

TASKS = score.load_tasks()


def _seen_and_missed(repo, task):  # noqa: F811
    store = _commit_and_index(repo, task["files"])
    _write(repo, task["partial"])
    report = check.run_check(repo, store, base="HEAD")
    named = {f.file for f in report.findings}
    missed = [s["file"] for s in task["sites"] if score.site_missed(repo, s)]
    return report, named, missed


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_check_names_every_left_behind_site_it_can_see(repo, task):  # noqa: F811
    _, named, missed = _seen_and_missed(repo, task)
    seeable = [
        f for f in missed
        if f not in {s["file"] for s in task["sites"] if s.get("unseen")}
    ]
    assert seeable, "the partial change must leave a visible site behind"
    assert set(seeable) <= named, f"check did not name {sorted(set(seeable) - named)}"


# The top severity that the check gives. A response field that changes inside a function
# body is `medium`: the check sees that the provider changed, not what its answer looks like.
TOP_SEVERITY = {
    "py-add-required-parameter": "high",
    "py-rename-route": "high",
    "ts-rename-function": "high",
    "ts-edge-function-response": "medium",
}


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_check_gives_the_expected_top_severity_for_a_partial_change(repo, task):  # noqa: F811
    report, _, _ = _seen_and_missed(repo, task)
    top = max((f.severity for f in report.findings), key=check.SEVERITY_RANK.get)
    assert top == TOP_SEVERITY[task["id"]]


def test_every_task_has_an_expected_top_severity():
    assert set(TOP_SEVERITY) == {t["id"] for t in TASKS}


def test_check_does_not_see_the_computed_route_name_and_the_task_keeps_it(repo):  # noqa: F811
    task = next(t for t in TASKS if t["id"] == "py-rename-route")
    _, named, missed = _seen_and_missed(repo, task)
    assert "sync.py" in missed
    assert "sync.py" not in named  # a known miss: BASE = "/orders" then client.post(BASE)

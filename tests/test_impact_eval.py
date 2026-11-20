"""The seeded breakage set is part of the quality gate: recall and precision must not fall.

The floor is the first measured run (2026-11-17: recall 0.867, precision 1.000), rounded
down a little. Raise the floor when the check improves. Never lower it to pass.
"""

import pytest
import yaml

from evals.impact import run as impact

RECALL_FLOOR = 0.85
PRECISION_FLOOR = 0.95


@pytest.fixture(scope="module")
def result():
    return impact.run_all()


def test_cases_file_has_python_and_typescript_cases():
    cases = yaml.safe_load(impact.CASES.read_text())["cases"]
    assert {c["language"] for c in cases} == {"python", "typescript"}
    assert sum(c["language"] == "python" for c in cases) >= 5
    assert sum(c["language"] == "typescript" for c in cases) >= 5
    assert len({c["id"] for c in cases}) == len(cases)


def test_cases_declare_their_expected_breaks_before_the_run():
    for case in yaml.safe_load(impact.CASES.read_text())["cases"]:
        expected = case.get("breaks", []) + case.get("breaks_files", [])
        expected += case.get("breaks_unseen", [])
        assert expected, f"{case['id']} expects nothing"
        assert set(case["change"]) <= set(case["files"]), f"{case['id']} changes a new file"


def test_run_all_recall_and_precision_stay_above_the_floor(result):
    summary = result["summary"]["all"]
    assert summary["recall"] >= RECALL_FLOOR, impact.render(result)
    assert summary["precision"] >= PRECISION_FLOOR, impact.render(result)


def test_run_all_each_language_finds_most_of_its_breaks(result):
    for language in ("python", "typescript"):
        assert result["summary"][language]["recall"] >= 0.7, impact.render(result)


def test_run_all_tests_to_run_are_all_named(result):
    assert result["summary"]["all"]["tests_recall"] == 1.0, impact.render(result)


def test_run_all_keeps_the_misses_it_is_known_for(result):
    """The set must keep cases that the check misses; a set with no miss measures nothing."""
    rows = {r["id"]: r for r in result["cases"]}
    assert "report.py::function::dump" in rows["py-rename-method"]["missed"]


def test_summarize_pools_cases_not_averages():
    rows = [
        {"language": "python", "expected": 1, "found": 1, "claims": 1, "correct": 1,
         "tests_expected": 0, "tests_found": 0},
        {"language": "python", "expected": 9, "found": 3, "claims": 3, "correct": 3,
         "tests_expected": 0, "tests_found": 0},
    ]
    assert impact.summarize(rows)["all"]["recall"] == 0.4


def test_main_min_recall_sets_the_exit_code(capsys):
    assert impact.main(["--min-recall", "0.0"]) == 0
    assert impact.main(["--min-recall", "1.01"]) == 1
    capsys.readouterr()

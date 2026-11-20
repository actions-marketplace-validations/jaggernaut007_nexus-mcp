"""The boundary breakage set is part of the quality gate (ADR-026).

The floor is the first measured run (2026-11-17: recall 0.889, precision 1.000; the same
set gave recall 0.000 before boundary edges existed), rounded down a little. One case holds
a computed URL that no static tool can see, so recall below 1.0 is expected. Raise the floor
when the check improves. Never lower it to pass.
"""

import pytest
import yaml

from evals.impact import run as impact

BOUNDARY_CASES = impact.CASES.with_name("boundary_cases.yaml")
RECALL_FLOOR = 0.85
PRECISION_FLOOR = 0.95


@pytest.fixture(scope="module")
def result():
    return impact.run_all(BOUNDARY_CASES)


def test_boundary_cases_cover_routes_edge_functions_and_tables():
    cases = yaml.safe_load(BOUNDARY_CASES.read_text())["cases"]
    ids = {c["id"] for c in cases}
    assert {"route-path-renamed", "edge-function-response-changed", "table-column-dropped"} <= ids
    assert len(ids) == len(cases)
    for case in cases:
        expected = case.get("breaks", []) + case.get("breaks_unseen", [])
        assert expected, f"{case['id']} expects nothing"
        assert set(case["change"]) <= set(case["files"]), f"{case['id']} changes a new file"


def test_boundary_run_all_recall_and_precision_stay_above_the_floor(result):
    summary = result["summary"]["all"]
    assert summary["recall"] >= RECALL_FLOOR, impact.render(result)
    assert summary["precision"] >= PRECISION_FLOOR, impact.render(result)


def test_boundary_run_all_keeps_the_miss_that_no_static_tool_sees(result):
    row = next(r for r in result["cases"] if r["id"] == "route-path-with-computed-url")
    assert row["missed"] == ["web/src/api.ts::function::listRaw"]

"""scripts/publish_results.py: one data file drives every published benchmark number."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "publish_results", ROOT / "scripts" / "publish_results.py"
)
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)
DATA = json.loads((ROOT / "benchmarks" / "published" / "results.json").read_text())


def test_publish_results_check_passes_on_the_committed_files():
    assert publish.main(["--check"]) == 0


def test_pct_change_uses_a_true_minus_sign():
    assert publish.pct_change(0.446, 0.571) == "−22%"
    assert publish.pct_change(1.1, 1.0) == "+10%"


def test_readme_block_lists_every_run_with_its_change():
    block = publish.readme_block(DATA)
    for run in DATA["runs"]:
        assert run["project"] in block
    assert "-22%" in block and "benchmarks.json" in block


def test_site_block_totals_the_correct_answers():
    block = publish.site_block(DATA)
    nexus = sum(r["nexus"]["correct"] for r in DATA["runs"])
    assert f"{nexus} · " in block


def test_csv_has_two_rows_per_run():
    lines = publish.csv_text(DATA).strip().splitlines()
    assert len(lines) == 1 + 2 * len(DATA["runs"])


def test_page_html_has_dataset_json_ld_and_a_title():
    page = publish.page_html(DATA)
    assert '"@type": "Dataset"' in page and "<title>Nexus-MCP Benchmarks</title>" in page


def test_replace_block_fails_without_markers(tmp_path):
    with pytest.raises(SystemExit):
        publish.replace_block("no markers", "x", tmp_path / "f")


def test_check_fails_when_a_generated_number_is_edited(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    original = (ROOT / "README.md").read_text()
    readme.write_text(original.replace("$0.446", "$0.111", 1))
    real_outputs = publish.outputs

    def outputs(data):
        files = real_outputs(data)
        return {readme: files[ROOT / "README.md"]}

    monkeypatch.setattr(publish, "outputs", outputs)
    assert publish.main(["--check"]) == 1


def test_comparison_md_has_one_row_per_condition():
    comp = DATA["comparisons"][0]
    lines = publish.comparison_md(comp)
    rows = [line for line in lines if line.startswith("| ") and "Condition" not in line]
    assert len(rows) == len(comp["conditions"])
    assert "| 164,192 |" in "\n".join(lines)


def test_comparison_html_has_every_task_and_escapes_text():
    comp = dict(DATA["comparisons"][0], title="A <b> test")
    page = publish.comparison_html(comp)
    for task in comp["per_task"]:
        assert task["task"] in page
    assert "A &lt;b&gt; test" in page and "<b>" not in page


def test_page_html_and_llms_block_without_comparisons():
    data = {k: v for k, v in DATA.items() if k != "comparisons"}
    assert "Head to head" not in publish.page_html(data, community=[])
    assert "GitNexus" not in publish.llms_block(data)

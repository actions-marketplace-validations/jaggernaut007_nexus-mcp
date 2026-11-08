"""scripts/publish_results.py: one data file drives every published benchmark number."""

import copy
import csv
import html
import importlib.util
import io
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


def _repo(language, grep_tokens, nexus_tokens, base_live=None, nexus_live=None):
    """A sample repo entry. The numbers are not measured."""
    entry = {
        "name": language.lower(), "language": language, "project": f"<sample> {language}",
        "url": f"https://example.com/{language}", "pin": "0123456789", "files": 100,
        "quick": {
            "run": "languages-final", "build": "2.2.2+11cfccc", "date": "2026-11-06",
            "questions": 30, "index_seconds": 10.0, "verdict": "Strong fit",
            "grep": {"hit_at_1": 0.2, "hit_at_3": 0.5, "median_tokens": grep_tokens,
                     "median_ms": 200.0},
            "nexus": {"hit_at_1": 0.9, "hit_at_3": 1.0, "median_tokens": nexus_tokens,
                      "median_ms": 20.0},
        },
    }  # fmt: skip
    if base_live is not None:

        def side(tokens, build, run):
            return {"run": run, "build": build, "median_tokens": tokens, "cost_usd": tokens / 1e6,
                    "turns": 3.0, "wall_seconds": 20.0, "correct": 6, "of": 6}  # fmt: skip

        entry["live"] = {
            "model": "sonnet", "effort": "medium", "reps": 3,
            "baseline": side(base_live, "built-in tools", "languages-before"),
            "nexus": side(nexus_live, "2.4.1", "languages-v2.4.1"),
        }  # fmt: skip
    return entry


SAMPLE = dict(
    copy.deepcopy(DATA),
    repos=[
        _repo("Java", 10000, 1000, base_live=50000, nexus_live=30000),
        _repo("Rust", 20000, 1000, base_live=40000, nexus_live=60000),
        _repo("Go", 30000, 3000),
    ],
)


def test_publish_results_check_passes_on_the_committed_files():
    assert publish.main(["--check"]) == 0


def test_pct_change_uses_a_true_minus_sign_and_rounds_zero():
    assert publish.pct_change(0.446, 0.571) == "−22%"
    assert publish.pct_change(1.1, 1.0) == "+10%"
    assert publish.pct_change(1.001, 1.0) == "0%"


def test_headline_counts_wins_and_correct_answers():
    h = publish.headline(SAMPLE)
    assert h["offline_repos"] == 3 and h["languages"] == 3
    assert h["token_cut"] == pytest.approx(0.9)
    assert h["speedup"] == pytest.approx(10.0)
    assert h["live_repos"] == 2 and h["cost_lower"] == 1
    assert h["correct_nexus"] == 12 and h["tasks"] == 12


def test_not_yet_names_the_languages_that_use_more_tokens():
    rows = publish.not_yet(SAMPLE)
    assert [r["language"] for r in rows] == ["Rust"]
    assert "Rust (<sample> Rust, +50% tokens, build 2.4.1)" in publish.not_yet_sentence(SAMPLE)
    no_loss = dict(SAMPLE, repos=SAMPLE["repos"][:1])
    assert publish.not_yet_sentence(no_loss) == ""


def test_readme_block_has_one_row_per_repo_and_bolds_only_wins():
    block = publish.readme_block(SAMPLE)
    assert block.count("| Java |") == 2 and block.count("| Go |") == 1
    assert "**30,000 (-40%)**" in block
    assert "60,000 (+50%)" in block and "**60,000" not in block
    assert "benchmarks.json" in block and "Not there yet: Rust" in block


def test_readme_block_tables_have_one_width():
    for lines in (publish.offline_md(SAMPLE), publish.live_md(SAMPLE)):
        assert len({line.count("|") for line in lines}) == 1


def test_site_block_marks_wins_and_losses_and_escapes_text():
    block = publish.site_block(SAMPLE)
    assert '<td class="win">30,000<small>−40%</small></td>' in block
    assert '<td class="loss">60,000<small>+50%</small></td>' in block
    assert "&lt;sample&gt; Java" in block and "<sample>" not in block
    assert 'role="region"' in block and 'class="table-scroll"' in block


def test_hero_block_has_one_tab_and_panel_per_offline_repo():
    block = publish.hero_block(SAMPLE)
    assert block.count('role="tab"') == 3 and block.count('role="tabpanel"') == 3
    assert block.count(" hidden") == 2 and 'aria-selected="true"' in block
    assert 'style="--w:10.0%"' in block  # Java: 1,000 of 10,000 tokens


def test_llms_block_has_no_arrows_and_lists_each_tier():
    block = publish.llms_block(SAMPLE)
    assert "→" not in block
    assert "Offline check, Go" in block and "Live run, Java" in block


def test_csv_has_one_width_and_rows_per_tier():
    rows = list(csv.DictReader(io.StringIO(publish.csv_text(SAMPLE))))
    raw = list(csv.reader(io.StringIO(publish.csv_text(SAMPLE))))
    assert raw[0] == publish.CSV_HEAD and {len(r) for r in raw} == {len(publish.CSV_HEAD)}
    java = [r for r in rows if r["language"] == "Java"]
    assert [(r["tier"], r["condition"]) for r in java] == [
        ("offline", "grep_and_read"), ("offline", "nexus_mcp"),
        ("live", "grep_and_read"), ("live", "nexus_mcp"),
    ]  # fmt: skip
    assert len([r for r in rows if r["tier"] == "live-12q"]) == 2 * len(DATA["runs"])


def test_page_html_has_dataset_json_ld_the_site_styles_and_every_repo():
    page = publish.page_html(SAMPLE, community=[])
    assert '"@type": "Dataset"' in page and "<title>Nexus-MCP Benchmarks</title>" in page
    assert 'href="styles.css"' in page and "Every column" in page
    assert page.count("&lt;sample&gt; Go") >= 2


def test_replace_block_fails_without_markers(tmp_path):
    with pytest.raises(SystemExit):
        publish.replace_block("no markers", "x", tmp_path / "f")


def test_replace_block_replaces_only_the_named_block():
    text = (
        "<!-- hero:start -->\nold\n<!-- hero:end -->\n"
        "<!-- results:start -->\nkeep\n<!-- results:end -->"
    )
    out = publish.replace_block(text, "new", Path("f"), "hero")
    assert "new" in out and "old" not in out and "keep" in out


def test_check_fails_when_a_generated_number_is_edited(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    original = (ROOT / "README.md").read_text()
    edited = original.replace("42,458", "11,111", 1)
    assert edited != original
    readme.write_text(edited)
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


def test_comparison_html_lists_every_fairness_rule():
    comp = DATA["comparisons"][0]
    page = publish.comparison_html(comp)
    assert "How the comparison was kept fair" in page
    for rule in comp["fairness"]:
        assert html.escape(rule) in page


def test_page_html_and_llms_block_without_comparisons():
    data = {k: v for k, v in DATA.items() if k != "comparisons"}
    assert "Head to head" not in publish.page_html(data, community=[])
    assert "GitNexus" not in publish.llms_block(data)


def test_community_html_without_entries_invites_a_first_result():
    assert "Yours can be the first" in publish.community_html([])


def test_main_rejects_an_old_schema_version(tmp_path, monkeypatch):
    bad = tmp_path / "results.json"
    bad.write_text(json.dumps(dict(DATA, schema_version=2)))
    monkeypatch.setattr(publish, "DATA", bad)
    with pytest.raises(SystemExit):
        publish.main(["--check"])

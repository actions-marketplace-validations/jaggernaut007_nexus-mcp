"""scripts/check_docs.py and scripts/gen_config_docs.py: the docs cannot drift from the code."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load("check_docs")
config_docs = _load("gen_config_docs")


def test_check_docs_passes_on_the_committed_files():
    assert check.main() == 0


def test_check_versions_reports_a_mismatch(monkeypatch):
    monkeypatch.setattr(check, "project_version", lambda: "9.9.9")
    errors = check.check_versions()
    assert errors and all("9.9.9" in e for e in errors)


def test_tools_claim_matches_the_phrases_we_use():
    found = check.TOOLS_CLAIM.findall("with 8 tools; 8 focused tools; Core Tools (8 MCP tools)")
    assert found == ["8", "8", "8"]


def test_test_count_pattern_catches_a_hand_written_count():
    assert check.TEST_COUNT.findall("pytest -v  # 1,132 tests (1,120 without slow)")
    assert not check.TEST_COUNT.findall("pytest -v  # all tests")


def test_check_docs_index_reports_an_unlinked_page(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("- [A.md](A.md)\n")
    (tmp_path / "docs" / "A.md").write_text("a")
    (tmp_path / "docs" / "B.md").write_text("b")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    assert check.check_docs_index() == ["docs/B.md is not linked from docs/README.md"]


def test_config_docs_lists_every_variable_with_its_default():
    text = config_docs.build()
    assert "| `NEXUS_STORAGE_DIR` | `.nexus` |" in text
    assert "| `NEXUS_AUTO_WATCH` | `true` |" in text
    assert "| `NEXUS_MAX_WORKERS` | unset |" in text
    assert "`NEXUS_OFFLINE`" in text


def test_config_docs_fails_for_a_variable_with_no_description(monkeypatch):
    descriptions = dict(config_docs.DESCRIPTIONS)
    descriptions.pop("NEXUS_SEARCH_MODE")
    monkeypatch.setattr(config_docs, "DESCRIPTIONS", descriptions)
    with pytest.raises(SystemExit, match="NEXUS_SEARCH_MODE"):
        config_docs.build()


def test_config_docs_check_passes_on_the_committed_page():
    assert config_docs.main(["--check"]) == 0

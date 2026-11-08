"""The size caps of `graph`: caller files and the counted files of `references`.

On Home Assistant `graph("async_setup_component")` gave 222,303 characters (4,979 callers in
about 3,000 files, and 1,200+ test files of references), above the 25k-token MCP result
limit of Claude Code. These tests use real files and a real grep, no mocks.
"""

from pathlib import Path

from nexus_mcp import core_api
from nexus_mcp.engines import live_grep
from nexus_mcp.engines.live_grep import LiveGrepEngine


def _grouped(source: int, tests: int) -> dict[str, list[str]]:
    grouped = {f"pkg/mod_{i:03}.py": [f"f{i}:1"] for i in range(source)}
    grouped |= {f"tests/sub/test_{i:03}.py": [f"t{i}:1", f"t{i}b:5"] for i in range(tests)}
    return grouped


def test_cap_files_lists_every_file_below_the_caps():
    listed, omitted = core_api._cap_files(_grouped(3, 2))

    assert omitted is None
    assert list(listed) == [
        "pkg/mod_000.py", "pkg/mod_001.py", "pkg/mod_002.py",
        "tests/sub/test_000.py", "tests/sub/test_001.py",
    ]


def test_cap_files_counts_the_test_files_past_the_cap_by_folder():
    tests = core_api.MAX_GRAPH_TEST_FILES + 25
    listed, omitted = core_api._cap_files(_grouped(2, tests))

    assert sum(1 for f in listed if f.startswith("tests/")) == core_api.MAX_GRAPH_TEST_FILES
    assert "pkg/mod_000.py" in listed and "pkg/mod_001.py" in listed  # source kept
    assert omitted == {
        "files": 25, "test_files": 25, "symbols": 50, "by_folder": {"tests/sub": 25},
    }


def test_cap_files_keeps_the_files_given_first():
    # A transitive walk gives the nearest callers first. A direct caller in `z/` must not
    # lose its place to far callers whose paths sort before it.
    far = {f"a/far_{i:03}.py": [f"f{i}"] for i in range(core_api.MAX_GRAPH_SOURCE_FILES)}
    listed, omitted = core_api._cap_files({"z/direct.py": ["caller"]} | far)

    assert "z/direct.py" in listed
    assert "a/far_059.py" not in listed
    assert omitted["files"] == 1


def test_cap_files_counts_the_source_files_past_the_cap():
    source = core_api.MAX_GRAPH_SOURCE_FILES + 4
    listed, omitted = core_api._cap_files(_grouped(source, 0))

    assert len(listed) == core_api.MAX_GRAPH_SOURCE_FILES
    assert omitted["files"] == 4 and omitted["test_files"] == 0
    assert omitted["by_folder"] == {"pkg": 4}


def test_references_caps_the_test_file_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(live_grep, "MAX_COUNTED_FILES", 3)
    (tmp_path / "app.py").write_text("def setup_thing():\n    pass\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    for i in range(5):
        (tests / f"test_{i}.py").write_text("setup_thing()\nsetup_thing()\n")
    engine = LiveGrepEngine(str(tmp_path))
    engine.rg_path = None

    refs = engine.references("setup_thing")

    assert refs["total_files"] == 6  # totals still count every file
    assert len(refs["test_files"]) == 3
    assert refs["test_files_not_listed"] == {"files": 2, "lines": 4}


def test_references_below_the_cap_has_no_not_listed_key(tmp_path):
    (tmp_path / "app.py").write_text("def setup_thing():\n    pass\n")
    (tmp_path / "test_app.py").write_text("setup_thing()\n")
    engine = LiveGrepEngine(str(tmp_path))
    engine.rg_path = None

    refs = engine.references("setup_thing")

    assert refs["test_files"] == {"test_app.py": 1}
    assert "test_files_not_listed" not in refs


def test_merge_references_sums_the_files_not_listed():
    one = {"total_files": 4, "total_lines": 9, "files": {},
           "test_files_not_listed": {"files": 2, "lines": 5}}
    two = {"total_files": 3, "total_lines": 3, "files": {},
           "test_files_not_listed": {"files": 1, "lines": 1},
           "more_files_not_listed": {"files": 7, "lines": 8}}

    merged = core_api._merge_references([(Path("/r/a"), one), (Path("/r/b"), two)])

    assert merged["test_files_not_listed"] == {"files": 3, "lines": 6}
    assert merged["more_files_not_listed"] == {"files": 7, "lines": 8}

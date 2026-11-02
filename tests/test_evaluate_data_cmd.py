"""Tests for the ``data`` helpers in nexus_mcp.evaluate.data_cmd."""

import pytest

from nexus_mcp.evaluate import cli, data_cmd, paths


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_EVAL_HOME", str(tmp_path / "evals"))
    monkeypatch.setenv("NEXUS_EVAL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)


def _make_store(project):
    store = project / ".nexus"
    (store / "lancedb" / "chunks.lance").mkdir(parents=True)
    (store / "lancedb" / "chunks.lance" / "data.bin").write_bytes(b"x" * 2048)
    (store / "lancedb" / "memories.lance").mkdir(parents=True)
    (store / "lancedb" / "memories.lance" / "m.bin").write_bytes(b"m" * 10)
    (store / "graph.db").write_bytes(b"g" * 100)
    (store / "index_metadata.json").write_text("{}")
    return store


def test_locations_lists_every_target(tmp_path):
    _make_store(tmp_path)
    rows = {r["target"]: r for r in data_cmd.locations(tmp_path)}
    assert set(rows) == set(data_cmd.TARGETS)
    assert rows["index"]["exists"] and rows["index"]["holds_code"]
    assert rows["graph"]["bytes"] == 100
    assert not rows["models"]["exists"]


def test_where_mentions_every_path_and_the_privacy_line(tmp_path):
    _make_store(tmp_path)
    text = data_cmd.where(tmp_path)
    for row in data_cmd.locations(tmp_path):
        assert row["path"] in text
    assert "Nothing leaves this machine" in text


def test_clear_is_a_dry_run_by_default(tmp_path):
    store = _make_store(tmp_path)
    outcome = data_cmd.clear(tmp_path, ["index", "memories", "graph"], yes=False)
    assert outcome["dry_run"] and outcome["plan"] and not outcome["deleted"]
    assert (store / "graph.db").exists()


def test_clear_memories_leaves_the_index(tmp_path):
    store = _make_store(tmp_path)
    data_cmd.clear(tmp_path, ["memories"], yes=True)
    assert not (store / "lancedb" / "memories.lance").exists()
    assert (store / "lancedb" / "chunks.lance").exists()
    assert (store / "graph.db").exists()


def test_clear_graph_only_removes_the_graph(tmp_path):
    store = _make_store(tmp_path)
    data_cmd.clear(tmp_path, ["graph"], yes=True)
    assert not (store / "graph.db").exists()
    assert (store / "lancedb" / "chunks.lance").exists()


def test_clear_index_removes_chunks_and_metadata(tmp_path):
    store = _make_store(tmp_path)
    data_cmd.clear(tmp_path, ["index"], yes=True)
    assert not (store / "lancedb" / "chunks.lance").exists()
    assert not (store / "index_metadata.json").exists()
    assert (store / "lancedb" / "memories.lance").exists()


def test_clear_refuses_a_folder_that_is_not_a_store(tmp_path):
    (tmp_path / ".nexus").mkdir()
    (tmp_path / ".nexus" / "precious.txt").write_text("keep me")
    with pytest.raises(ValueError, match="does not look like"):
        data_cmd.clear(tmp_path, ["index"], yes=True)
    assert (tmp_path / ".nexus" / "precious.txt").exists()


def test_clear_evals_removes_saved_results(tmp_path):
    (paths.eval_home() / "run1").mkdir(parents=True)
    (paths.eval_home() / "run1" / "report.json").write_text("{}")
    data_cmd.clear(tmp_path, ["evals"], yes=True)
    assert not paths.eval_home().exists()


def test_cli_clear_without_target_exits_2(tmp_path, capsys):
    assert cli.run_data(["clear", str(tmp_path)]) == 2
    assert "Choose what to delete" in capsys.readouterr().err


def test_cli_clear_dry_run_prints_plan(tmp_path, capsys):
    _make_store(tmp_path)
    assert cli.run_data(["clear", str(tmp_path), "--graph"]) == 0
    out = capsys.readouterr().out
    assert "Would delete" in out and "Dry run" in out


def test_export_memories_without_store_raises(tmp_path):
    with pytest.raises(ValueError, match="No memories"):
        data_cmd.export_memories(tmp_path, tmp_path / "out.json")


def test_human_size_units():
    assert paths.human_size(10) == "10 B"
    assert paths.human_size(2048) == "2.0 KB"

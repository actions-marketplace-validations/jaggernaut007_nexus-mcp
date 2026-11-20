"""Tests for the ``data`` helpers in nexus_mcp.evaluate.data_cmd."""

import pytest

from nexus_mcp.evaluate import cli, data_cmd, paths


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_EVAL_HOME", str(tmp_path / "evals"))
    monkeypatch.setenv("NEXUS_EVAL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "links.json"))
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


def test_model_dir_is_the_cache_folder_of_the_default_model():
    """The folder comes from the registry. It was hardcoded to the BAAI repo, so after the
    default moved to the int8 ONNX repo (3.0.0) `data where` and the doctor looked in the
    wrong place."""
    from nexus_mcp.indexing.embedding_service import DEFAULT_MODEL, EMBEDDING_MODELS

    hf_name = EMBEDDING_MODELS[DEFAULT_MODEL]["hf_name"]
    assert paths.model_dir().name == "models--" + hf_name.replace("/", "--")
    assert paths.model_dir().name == "models--Xenova--bge-small-en-v1.5"


def test_model_dirs_cover_every_registered_model():
    names = {p.name for p in paths.model_dirs()}
    assert names == {"models--Xenova--bge-small-en-v1.5", "models--BAAI--bge-small-en-v1.5"}


def test_clear_models_removes_the_default_and_the_torch_model_folders(tmp_path):
    for folder in paths.model_dirs():
        (folder / "snapshots").mkdir(parents=True)
        (folder / "snapshots" / "weights.bin").write_bytes(b"w" * 10)
    other = paths.hf_hub_dir() / "models--someone--else"  # another tool's model must stay
    other.mkdir(parents=True)

    data_cmd.clear(tmp_path, ["models"], yes=True)

    assert not any(folder.exists() for folder in paths.model_dirs())
    assert other.exists()


def test_where_counts_the_torch_model_folder_that_clear_would_delete(tmp_path):
    """Found by the test audit: `where` showed only the default model, while `clear --models`
    deletes every registered model folder. Only the torch folder is written here, so a
    `where` that looks at the default folder alone reports 0 bytes."""
    torch_folder = next(p for p in paths.model_dirs() if p.name.startswith("models--BAAI"))
    (torch_folder / "snapshots").mkdir(parents=True)
    (torch_folder / "snapshots" / "w.bin").write_bytes(b"w" * 1000)
    row = {r["target"]: r for r in data_cmd.locations(tmp_path)}["models"]
    assert row["exists"] and row["bytes"] >= 1000 and row["files"] == 1


def test_doctor_model_check_follows_the_default_model_folder():
    """The folder was hardcoded to the BAAI repo, so the doctor looked in the wrong place
    after the default moved (3.0.0). Only the old folder is cached here: still not cached."""
    from nexus_mcp.evaluate import doctor

    def model_check():
        items = doctor.checks(which=lambda cmd: None)
        return next((ok, detail) for name, ok, detail in items if name == "embedding model cached")

    ok, detail = model_check()
    assert ok is False and "~32MB" in detail

    old_folder = next(p for p in paths.model_dirs() if p.name.startswith("models--BAAI"))
    old_folder.mkdir(parents=True)
    assert model_check()[0] is False

    paths.model_dir().mkdir(parents=True)
    ok, detail = model_check()
    assert ok is True and detail == str(paths.model_dir())


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


def test_where_lists_the_link_registry(tmp_path):
    row = {r["target"]: r for r in data_cmd.locations(tmp_path)}["links"]
    assert row["path"] == str(tmp_path / "links.json")
    assert row["exists"] is False


def test_clear_links_removes_the_registry_and_no_index(tmp_path):
    store = _make_store(tmp_path)
    (tmp_path / "links.json").write_text("{}")
    data_cmd.clear(tmp_path, ["links"], yes=True)
    assert not (tmp_path / "links.json").exists()
    assert (store / "graph.db").exists()

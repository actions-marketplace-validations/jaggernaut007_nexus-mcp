"""A new server process attaches the linked repos of the project, read-only (ADR-030)."""

import json
import shutil

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import links
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state

pytestmark = pytest.mark.usefixtures("fake_embeddings")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A primary project that is indexed, and a backend that is linked and indexed.

    Afterwards all in-memory state is dropped, as in a new server process.
    """
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    primary = tmp_path / "primary"
    backend = tmp_path / "backend"
    primary.mkdir()
    backend.mkdir()
    (primary / "ui.py").write_text("def render():\n    return 1\n")
    (backend / "api.py").write_text(
        "def handle():\n    return helper()\n\n\ndef helper():\n    return 2\n"
    )
    monkeypatch.chdir(primary)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(primary / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(backend)]) == 0
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    monkeypatch.setenv("NEXUS_STALENESS_CHECK_INTERVAL", "0")
    reset_settings()
    yield primary, backend
    reset_settings()


def _linked_status():
    return core_api.status()["linked"]


def test_restore_attaches_the_linked_repo(world):
    primary, backend = world
    assert core_api.restore_session() is True
    [repo] = get_state().linked
    assert repo.name == "backend"
    assert repo.skip_reason is None
    assert repo.vector_engine.count() > 0
    assert repo.graph_engine.nodes
    assert repo.bm25_engine is not None


def test_status_lists_the_linked_repo(world):
    [entry] = _linked_status()
    assert entry["name"] == "backend"
    assert entry["files"] == 1
    assert entry["stale"] is False
    assert entry["skipped"] is None
    assert entry["path"] == str(world[1])


def test_status_without_a_link_has_no_linked_key(world):
    links.remove_link(world[0], "backend")
    result = core_api.status()
    assert result["indexed"] is True
    assert "linked" not in result
    assert get_state().linked == []


def test_status_of_a_project_with_a_different_primary_shows_no_link(world, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "o.py").write_text("def o():\n    return 1\n")
    monkeypatch.chdir(other)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(other / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(other)
    reset_state()
    core_api._pipeline = None
    assert "linked" not in core_api.status()


def test_status_flags_a_stale_linked_repo(world):
    primary, backend = world
    (backend / "new.py").write_text("def new():\n    return 3\n")
    [entry] = _linked_status()
    assert entry["stale"] is True
    assert core_api.status()["linked_warning"] == (
        "Linked repos out of date: backend. Run `nexus-mcp-ci link --refresh` to index them again."
    )


def test_restore_does_not_write_to_the_linked_store(world):
    primary, backend = world
    store = backend / ".nexus"

    def snapshot():
        return {
            str(p.relative_to(store)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in store.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    core_api.status()
    core_api.status()
    assert snapshot() == before


def test_a_gone_folder_is_skipped_with_a_reason(world, caplog):
    primary, backend = world
    shutil.rmtree(backend)
    assert core_api.restore_session() is True
    [entry] = _linked_status()
    assert "folder is gone" in entry["skipped"]
    assert entry["files"] is None
    assert get_state().linked[0].vector_engine is None
    assert "backend" in caplog.text


def test_a_repo_without_an_index_is_skipped(world):
    primary, backend = world
    shutil.rmtree(backend / ".nexus")
    [entry] = _linked_status()
    assert "no index" in entry["skipped"]
    assert not (backend / ".nexus").exists()  # a skipped link creates no store


def test_a_corrupt_store_is_skipped(world):
    primary, backend = world
    shutil.rmtree(backend / ".nexus" / "lancedb" / "chunks.lance")
    [entry] = _linked_status()
    assert "corrupt" in entry["skipped"]
    assert core_api.status()["indexed"] is True


def test_unreadable_metadata_is_skipped(world):
    primary, backend = world
    (backend / ".nexus" / "index_metadata.json").write_text("{broken")
    [entry] = _linked_status()
    assert "corrupt" in entry["skipped"]


def test_a_different_embedding_model_is_skipped(world):
    primary, backend = world
    path = backend / ".nexus" / "index_metadata.json"
    meta = json.loads(path.read_text())
    meta["embedding_model"] = "other-model"
    path.write_text(json.dumps(meta))
    [entry] = _linked_status()
    assert "embedding model" in entry["skipped"]
    assert "other-model" in entry["skipped"]


def test_a_changed_repo_id_is_skipped(world):
    primary, backend = world
    path = backend / ".nexus" / "index_metadata.json"
    meta = json.loads(path.read_text())
    meta["repo_id"] = "f" * 32
    path.write_text(json.dumps(meta))
    [entry] = _linked_status()
    assert "repo id" in entry["skipped"]


def test_an_index_built_for_another_folder_is_skipped(world, tmp_path):
    """The store in a linked repo is not trusted: its roots must match the registry."""
    primary, backend = world
    path = backend / ".nexus" / "index_metadata.json"
    meta = json.loads(path.read_text())
    meta["codebase_path"] = str(tmp_path)
    path.write_text(json.dumps(meta))
    [entry] = _linked_status()
    assert "different folder" in entry["skipped"]


def test_a_missing_graph_file_is_skipped(world):
    primary, backend = world
    (backend / ".nexus" / "graph.db").unlink()
    [entry] = _linked_status()
    assert "graph" in entry["skipped"]


def test_one_bad_link_does_not_hide_a_good_one(world, tmp_path):
    primary, backend = world
    second = tmp_path / "second"
    second.mkdir()
    (second / "s.py").write_text("def s():\n    return 1\n")
    assert cli.dispatch(["link", str(second)]) == 0
    shutil.rmtree(backend)
    reset_state()
    core_api._pipeline = None
    by_name = {e["name"]: e for e in _linked_status()}
    assert by_name["backend"]["skipped"]
    assert by_name["second"]["skipped"] is None


def test_a_broken_registry_does_not_break_the_primary(world, tmp_path):
    links.registry_path().write_text("{broken")
    result = core_api.status()
    assert result["indexed"] is True
    assert "linked" not in result


def test_the_index_tool_attaches_the_linked_repos(world):
    import asyncio

    primary, backend = world
    asyncio.run(core_api.index(str(primary)))
    [repo] = get_state().linked
    assert repo.name == "backend" and repo.skip_reason is None


def test_attach_does_not_change_a_graph_file_in_delete_journal_mode(world):
    """Found by the code review: opening the graph with a pragma rewrote its header."""
    import sqlite3

    primary, backend = world
    graph = backend / ".nexus" / "graph.db"
    conn = sqlite3.connect(graph)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    import os

    os.utime(graph, (graph.stat().st_atime, graph.stat().st_mtime + 5))  # newer than metadata
    before = graph.read_bytes()
    [entry] = _linked_status()
    assert entry["skipped"] is None
    assert graph.read_bytes() == before
    assert sorted(p.name for p in graph.parent.iterdir() if p.name.startswith("graph.db")) == [
        "graph.db"
    ]


def test_a_malformed_codebase_paths_value_skips_only_that_link(world, tmp_path, capsys):
    """Found by the code review: a value that is not a list hid every link."""
    primary, backend = world
    second = tmp_path / "second"
    second.mkdir()
    (second / "s.py").write_text("def s():\n    return 1\n")
    assert cli.dispatch(["link", str(second)]) == 0
    path = backend / ".nexus" / "index_metadata.json"
    meta = json.loads(path.read_text())
    meta["codebase_paths"] = 5
    path.write_text(json.dumps(meta))
    reset_state()
    core_api._pipeline = None
    by_name = {e["name"]: e for e in _linked_status()}
    assert "different folder" in by_name["backend"]["skipped"]
    assert by_name["second"]["skipped"] is None
    assert cli.dispatch(["links"]) == 0

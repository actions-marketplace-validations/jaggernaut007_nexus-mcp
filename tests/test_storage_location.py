"""Where the index folder goes when `NEXUS_STORAGE_DIR` is relative (the default `.nexus`).

Cline starts MCP servers with `/` as the working folder. Before 2.4.3 a relative storage
folder resolved against the working folder, so the first `index` call failed with
`[Errno 30] Read-only file system: '/.nexus'` on macOS.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import nexus_mcp.core_api as core_api
import nexus_mcp.server as server_module
from nexus_mcp.config import Settings, get_settings, reset_settings
from tests.conftest import _call_tool, _mock_embedding_service


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "lib.py").write_text("def helper():\n    return 1\n")
    return root


def _index(path: Path) -> dict:
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_svc:
        get_svc.return_value = _mock_embedding_service()
        mcp = server_module.create_server()
        return asyncio.run(_call_tool(mcp, "index", {"path": str(path)}))


@pytest.fixture
def no_watch(monkeypatch):
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setenv("NEXUS_AUTO_WATCH", "false")
    reset_settings()


def test_storage_path_resolves_a_relative_folder_against_the_base():
    assert Settings(storage_base="/srv/app").storage_path == Path("/srv/app/.nexus")
    assert Settings().storage_path == Path(".nexus")


def test_storage_path_ignores_the_base_for_an_absolute_folder():
    s = Settings(storage_dir="/data/index", storage_base="/srv/app")
    assert s.storage_path == Path("/data/index")
    assert s.lancedb_path == Path("/data/index/lancedb")


def test_storage_path_expands_the_home_folder():
    assert Settings(storage_dir="~/nexus-index").storage_path == Path.home() / "nexus-index"


def test_storage_base_for_a_project_in_the_working_folder_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert core_api.storage_base_for(tmp_path) == ""
    assert core_api.storage_base_for(_project(tmp_path / "sub")) == ""


def test_storage_base_for_a_project_outside_the_working_folder_is_the_project(
    tmp_path, monkeypatch
):
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    project = _project(tmp_path / "project")
    assert core_api.storage_base_for(project) == str(project.resolve())


def test_storage_base_for_a_server_started_in_the_filesystem_root(tmp_path, monkeypatch):
    monkeypatch.chdir("/")
    project = _project(tmp_path / "project")
    assert core_api.storage_base_for(project) == str(project.resolve())


def test_index_from_the_filesystem_root_writes_the_index_in_the_project(
    tmp_path, monkeypatch, no_watch
):
    monkeypatch.chdir("/")
    project = _project(tmp_path / "project")
    result = _index(project)
    assert "error" not in result, result
    meta = json.loads((project / ".nexus" / "index_metadata.json").read_text())
    assert meta["codebase_path"] == str(project.resolve())
    assert (project / ".nexus" / "graph.db").exists()


def test_index_of_a_project_in_the_working_folder_keeps_the_old_place(
    tmp_path, monkeypatch, no_watch
):
    monkeypatch.chdir(tmp_path)
    project = _project(tmp_path / "project")
    assert "error" not in _index(project)
    # `restore_session` looks in `<working folder>/.nexus` at the next start.
    assert (tmp_path / ".nexus" / "index_metadata.json").exists()
    assert not (project / ".nexus").exists()


def test_index_of_a_second_project_opens_that_project_index(tmp_path, monkeypatch, no_watch):
    monkeypatch.chdir("/")
    first = _project(tmp_path / "first")
    second = _project(tmp_path / "second")
    assert "error" not in _index(first)
    first_pipeline = core_api._pipeline
    assert "error" not in _index(second)
    assert core_api._pipeline is not first_pipeline
    assert get_settings().storage_path == second.resolve() / ".nexus"
    meta = json.loads((second / ".nexus" / "index_metadata.json").read_text())
    assert meta["codebase_path"] == str(second.resolve())
    # The first index is untouched.
    meta = json.loads((first / ".nexus" / "index_metadata.json").read_text())
    assert meta["codebase_path"] == str(first.resolve())


def test_index_with_an_absolute_storage_folder_ignores_the_project(
    tmp_path, monkeypatch, no_watch
):
    monkeypatch.chdir("/")
    storage = tmp_path / "storage"
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(storage))
    reset_settings()
    project = _project(tmp_path / "project")
    assert "error" not in _index(project)
    assert (storage / "index_metadata.json").exists()
    assert not (project / ".nexus").exists()

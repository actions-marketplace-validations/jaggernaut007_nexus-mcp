"""Gap tests for linked repos (ADR-030): branches that the first test files do not reach."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import linked, links
from nexus_mcp.config import Settings
from nexus_mcp.evaluate import cli
from nexus_mcp.links import Link, LinksError
from nexus_mcp.state import SessionState


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    file = tmp_path / "home" / "links.json"
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(file))
    return file


@pytest.fixture
def folders(tmp_path):
    primary, backend = tmp_path / "front", tmp_path / "back"
    primary.mkdir()
    backend.mkdir()
    return primary, backend


# ---- links.py ----


@pytest.mark.parametrize("text", ['{"links": []}', "[]", '{"version": 1}'])
def test_add_link_raises_on_valid_json_of_the_wrong_shape(folders, registry, text):
    registry.parent.mkdir(parents=True)
    registry.write_text(text)
    with pytest.raises(LinksError, match="unexpected shape"):
        links.add_link(*folders, "id")
    assert registry.read_text() == text


def test_plan_link_writes_nothing_and_has_an_empty_repo_id(folders, registry):
    planned = links.plan_link(*folders)
    assert planned.repo_id == "" and planned.name == "back"
    assert not registry.exists()


def test_plan_link_keeps_the_name_of_an_existing_link(folders):
    primary, backend = folders
    links.add_link(primary, backend, "a", name="api")
    assert links.plan_link(primary, backend).name == "api"
    assert links.plan_link(primary, backend, "api").name == "api"
    assert links.add_link(primary, backend, "b").name == "api"
    assert [x.repo_id for x in links.links_for(primary)] == ["b"]


def test_plan_link_rejects_a_file_path(folders, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(LinksError, match="not a folder"):
        links.plan_link(folders[0], f)


@pytest.mark.parametrize("n,ok", [(64, True), (65, False)])
def test_validate_name_length_limit(n, ok):
    if ok:
        assert links.validate_name("a" * n)
    else:
        with pytest.raises(LinksError):
            links.validate_name("a" * n)


@pytest.mark.parametrize("name", ["", None, "../x", "a/b", ".hidden", "-x", "a b"])
def test_validate_name_rejects_unsafe_values(name):
    with pytest.raises(LinksError):
        links.validate_name(name)


def test_default_name_cleans_odd_folder_names(tmp_path):
    odd = tmp_path / "My Repo!!"
    odd.mkdir()
    assert links.default_name(odd) == "My-Repo-"
    hidden = tmp_path / "___"
    hidden.mkdir()
    assert links.default_name(hidden) == "repo"
    assert len(links.default_name(tmp_path / ("x" * 100))) == 64


def test_add_link_with_an_unsafe_default_name_still_gets_a_valid_name(folders, tmp_path):
    odd = tmp_path / "a b c"
    odd.mkdir()
    assert links.add_link(folders[0], odd, "id").name == "a-b-c"


def test_links_for_ignores_non_list_values_and_non_string_fields(folders, registry):
    primary, _ = folders
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"version": 1, "links": {str(primary.resolve()): "oops", "k": [{"name": 1}]}})
    )
    assert links.links_for(primary) == []


def test_links_for_resolves_symlinked_primary(folders, tmp_path):
    primary, backend = folders
    links.add_link(primary, backend, "id")
    alias = tmp_path / "alias"
    alias.symlink_to(primary)
    assert len(links.links_for(alias)) == 1


# ---- linked.py ----


def test_linked_repo_engine_properties_are_none_without_a_pipeline():
    repo = linked.LinkedRepo("n", Path("/x"), "id", skip_reason="gone")
    assert repo.vector_engine is None
    assert repo.bm25_engine is None
    assert repo.graph_engine is None


def test_linked_repo_engine_properties_come_from_the_pipeline():
    pipe = SimpleNamespace(vector_engine="v", bm25_engine="b", graph_engine="g")
    repo = linked.LinkedRepo("n", Path("/x"), "id", pipeline=pipe)
    assert (repo.vector_engine, repo.bm25_engine, repo.graph_engine) == ("v", "b", "g")


def test_stored_roots_match_cases(tmp_path):
    other = tmp_path / "o"
    other.mkdir()
    assert linked.stored_roots_match({"codebase_path": str(tmp_path)}, tmp_path)
    assert linked.stored_roots_match({"codebase_paths": [str(tmp_path)]}, tmp_path)
    assert not linked.stored_roots_match({"codebase_paths": [str(tmp_path), str(other)]}, tmp_path)
    assert not linked.stored_roots_match({"codebase_paths": [str(tmp_path)] * 2}, tmp_path)
    assert not linked.stored_roots_match({}, tmp_path)
    assert not linked.stored_roots_match({"codebase_paths": [5]}, tmp_path)
    assert not linked.stored_roots_match({"codebase_path": str(other)}, tmp_path)


def _store(root, **meta):
    (root / ".nexus" / "lancedb").mkdir(parents=True)
    base = {"mtimes": {}, "codebase_path": str(root), "repo_id": "id"}
    base.update(meta)
    (root / ".nexus" / "index_metadata.json").write_text(json.dumps(base))


def test_check_store_accepts_a_matching_store(folders):
    _, backend = folders
    _store(backend)
    assert linked.check_store(Link("b", str(backend), "id"), Settings()) is None


def test_check_store_rejects_a_store_with_no_repo_id(folders):
    _, backend = folders
    _store(backend, repo_id=None)
    assert "no repo id" in linked.check_store(Link("b", str(backend), "id"), Settings())


def test_check_store_rejects_metadata_without_mtimes(folders):
    _, backend = folders
    _store(backend)
    p = backend / ".nexus" / "index_metadata.json"
    p.write_text(json.dumps({"codebase_path": str(backend), "repo_id": "id"}))
    assert "corrupt" in linked.check_store(Link("b", str(backend), "id"), Settings())


def test_check_store_rejects_a_missing_lancedb_folder(folders):
    _, backend = folders
    _store(backend)
    (backend / ".nexus" / "lancedb").rmdir()
    assert "no index" in linked.check_store(Link("b", str(backend), "id"), Settings())


def test_attach_one_skips_when_the_pipeline_cannot_open(folders, monkeypatch):
    _, backend = folders
    _store(backend)

    def boom(*a, **k):
        raise RuntimeError("bad store")

    monkeypatch.setattr("nexus_mcp.indexing.pipeline.IndexingPipeline", boom)
    repo = linked._attach_one(Link("b", str(backend), "id"), Settings())
    assert repo.pipeline is None and "could not be opened" in repo.skip_reason


def test_staleness_of_a_skipped_repo_is_not_stale():
    repo = linked.LinkedRepo("n", Path("/x"), "id", skip_reason="gone")
    assert linked.staleness(repo, 0)["stale"] is False
    assert linked.describe(repo, 0)["skipped"] == "gone"


def test_staleness_is_cached_inside_the_interval_and_refreshed_after():
    pipe = MagicMock()
    pipe.check_staleness.return_value = {"stale": True, "changed_files": 1, "reason": "r"}
    repo = linked.LinkedRepo("n", Path("/x"), "id", pipeline=pipe)
    linked.staleness(repo, 1000)
    linked.staleness(repo, 1000)
    assert pipe.check_staleness.call_count == 1
    linked.staleness(repo, 0)
    assert pipe.check_staleness.call_count == 2


def test_staleness_swallows_a_check_error():
    pipe = MagicMock()
    pipe.check_staleness.side_effect = OSError("denied")
    repo = linked.LinkedRepo("n", Path("/x"), "id", pipeline=pipe)
    assert linked.staleness(repo, 0) == {"stale": False, "changed_files": 0, "reason": None}


# ---- core_api / link_cmd ----


def test_attach_linked_failure_leaves_an_empty_list(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(linked, "attach_all", boom)
    state = SessionState()
    state.codebase_path = Path("/x")
    state.linked = ["stale"]
    core_api._attach_linked(state, Settings())
    assert state.linked == []


def test_links_command_with_a_broken_registry_returns_2(registry, capsys, tmp_path, monkeypatch):
    registry.parent.mkdir(parents=True)
    registry.write_text("{broken")
    monkeypatch.chdir(tmp_path)
    assert cli.dispatch(["links"]) == 2
    assert "error:" in capsys.readouterr().err


def test_unlink_with_a_broken_registry_returns_2(registry, capsys, tmp_path, monkeypatch):
    registry.parent.mkdir(parents=True)
    registry.write_text("{broken")
    monkeypatch.chdir(tmp_path)
    assert cli.dispatch(["unlink", "x"]) == 2

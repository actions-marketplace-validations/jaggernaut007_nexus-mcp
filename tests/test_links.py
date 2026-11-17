"""The link registry: a user-level file outside each repo (ADR-027)."""

import json

import pytest

from nexus_mcp import links
from nexus_mcp.links import LinksError


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    file = tmp_path / "home" / "links.json"
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(file))
    return file


@pytest.fixture
def folders(tmp_path):
    primary = tmp_path / "frontend"
    backend = tmp_path / "backend"
    primary.mkdir()
    backend.mkdir()
    return primary, backend


def test_registry_path_uses_the_env_variable(registry):
    assert links.registry_path() == registry


def test_registry_path_defaults_to_the_user_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("NEXUS_LINKS_FILE")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert links.registry_path() == tmp_path / ".nexus-mcp" / "links.json"


def test_links_for_with_no_file_is_empty(folders):
    assert links.links_for(folders[0]) == []


def test_add_link_stores_name_path_and_repo_id(folders, registry):
    primary, backend = folders
    link = links.add_link(primary, backend, "abc123")
    assert (link.name, link.path, link.repo_id) == ("backend", str(backend.resolve()), "abc123")
    assert links.links_for(primary) == [link]
    stored = json.loads(registry.read_text())
    assert stored["links"][str(primary.resolve())] == [
        {"name": "backend", "path": str(backend.resolve()), "repo_id": "abc123"}
    ]


def test_add_link_uses_the_given_name(folders):
    primary, backend = folders
    assert links.add_link(primary, backend, "id1", name="api").name == "api"


def test_add_link_again_for_the_same_path_updates_the_repo_id(folders):
    primary, backend = folders
    links.add_link(primary, backend, "old")
    links.add_link(primary, backend, "new")
    assert [(x.name, x.repo_id) for x in links.links_for(primary)] == [("backend", "new")]


def test_add_link_rejects_the_primary_itself(folders):
    primary, _ = folders
    with pytest.raises(LinksError, match="itself"):
        links.add_link(primary, primary, "id1")


def test_add_link_rejects_a_missing_folder(folders, tmp_path):
    with pytest.raises(LinksError, match="not a folder"):
        links.add_link(folders[0], tmp_path / "nope", "id1")


def test_add_link_rejects_a_name_used_for_another_path(folders, tmp_path):
    primary, backend = folders
    other = tmp_path / "other"
    other.mkdir()
    links.add_link(primary, backend, "id1", name="svc")
    with pytest.raises(LinksError, match="already names"):
        links.add_link(primary, other, "id2", name="svc")


def test_add_link_rejects_a_path_linked_under_another_name(folders):
    primary, backend = folders
    links.add_link(primary, backend, "id1", name="one")
    with pytest.raises(LinksError, match="already linked as 'one'"):
        links.add_link(primary, backend, "id1", name="two")


@pytest.mark.parametrize("name", ["", "a/b", "a b", "..", "-x", "x" * 65, "a\x00b"])
def test_add_link_rejects_an_unsafe_name(folders, name):
    with pytest.raises(LinksError, match="name"):
        links.add_link(folders[0], folders[1], "id1", name=name)


def test_remove_link_deletes_one_entry_and_returns_it(folders):
    primary, backend = folders
    links.add_link(primary, backend, "id1")
    removed = links.remove_link(primary, "backend")
    assert removed.name == "backend"
    assert links.links_for(primary) == []


def test_remove_link_drops_an_empty_primary_key(folders, registry):
    primary, backend = folders
    links.add_link(primary, backend, "id1")
    links.remove_link(primary, "backend")
    assert json.loads(registry.read_text())["links"] == {}


def test_remove_link_with_an_unknown_name_fails(folders):
    with pytest.raises(LinksError, match="No link named 'x'"):
        links.remove_link(folders[0], "x")


def test_links_of_one_primary_do_not_show_for_another(folders, tmp_path):
    primary, backend = folders
    other = tmp_path / "other"
    other.mkdir()
    links.add_link(primary, backend, "id1")
    assert links.links_for(other) == []


def test_load_raises_on_a_corrupt_file_and_add_keeps_it(folders, registry):
    registry.parent.mkdir(parents=True)
    registry.write_text("{not json")
    with pytest.raises(LinksError, match="not valid JSON"):
        links.add_link(folders[0], folders[1], "id1")
    assert registry.read_text() == "{not json"


def test_links_for_returns_empty_and_warns_on_a_corrupt_file(folders, registry, caplog):
    registry.parent.mkdir(parents=True)
    registry.write_text("[1, 2]")
    assert links.links_for(folders[0]) == []
    assert "links" in caplog.text.lower()


def test_links_for_skips_malformed_entries(folders, registry):
    primary, backend = folders
    registry.parent.mkdir(parents=True)
    good = {"name": "backend", "path": str(backend), "repo_id": "id1"}
    registry.write_text(
        json.dumps(
            {"version": 1, "links": {str(primary.resolve()): [good, {"name": 5}, "x"]}}
        )
    )
    assert [x.name for x in links.links_for(primary)] == ["backend"]


def test_save_is_atomic_and_leaves_no_temp_file(folders, registry):
    primary, backend = folders
    links.add_link(primary, backend, "id1")
    assert [p.name for p in registry.parent.iterdir()] == ["links.json"]


def test_save_failure_keeps_the_old_file(folders, registry, monkeypatch):
    primary, backend = folders
    links.add_link(primary, backend, "id1")
    before = registry.read_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("nexus_mcp.links.os.replace", boom)
    with pytest.raises(LinksError, match="Could not write"):
        links.remove_link(primary, "backend")
    assert registry.read_text() == before
    assert [p.name for p in registry.parent.iterdir()] == ["links.json"]


def test_default_name_is_the_folder_name(tmp_path):
    assert links.default_name(tmp_path / "My Service") == "My-Service"


def test_store_dir_ignores_the_storage_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / "elsewhere"))
    assert links.store_dir(tmp_path / "repo") == tmp_path / "repo" / ".nexus"


def test_settings_for_points_at_the_repo_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / "elsewhere"))
    settings = links.settings_for(tmp_path / "repo")
    assert settings.storage_path == tmp_path / "repo" / ".nexus"


def test_links_for_drops_an_entry_with_an_unsafe_name(folders, registry):
    primary, backend = folders
    registry.parent.mkdir(parents=True)
    entries = [
        {"name": "../evil", "path": str(backend), "repo_id": "id1"},
        {"name": "good", "path": str(backend), "repo_id": "id2"},
    ]
    registry.write_text(json.dumps({"version": 1, "links": {str(primary.resolve()): entries}}))
    assert [x.name for x in links.links_for(primary)] == ["good"]

"""`nexus-mcp-ci link`, `unlink` and `links`, run through `cli.dispatch` on real stores."""

import json

import pytest

from nexus_mcp import links
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import EXTRACTOR_VERSION

pytestmark = pytest.mark.usefixtures("fake_embeddings")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A frontend (the working folder) and a backend repo, with the registry in tmp_path."""
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    for root in (frontend, backend):
        root.mkdir()
        (root / "app.py").write_text("def run():\n    return 1\n")
    monkeypatch.chdir(frontend)
    return frontend, backend


def _meta(root):
    return json.loads((root / ".nexus" / "index_metadata.json").read_text())


def test_link_indexes_a_repo_that_has_no_index(world, capsys):
    frontend, backend = world
    assert cli.dispatch(["link", str(backend)]) == 0
    meta = _meta(backend)
    assert meta["extractor_version"] == EXTRACTOR_VERSION
    [link] = links.links_for(frontend)
    assert (link.name, link.path, link.repo_id) == ("backend", str(backend), meta["repo_id"])
    assert "Linked backend" in capsys.readouterr().out


def test_link_accepts_a_relative_path_and_a_name(world):
    frontend, backend = world
    assert cli.dispatch(["link", "../backend", "--name", "api"]) == 0
    assert [x.name for x in links.links_for(frontend)] == ["api"]


def test_link_does_not_index_again_when_the_index_is_current(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    path = backend / ".nexus" / "index_metadata.json"
    before = path.read_bytes()
    cli.dispatch(["unlink", "backend"])
    assert cli.dispatch(["link", str(backend)]) == 0
    assert path.read_bytes() == before


def test_link_indexes_again_when_the_extractor_version_is_missing(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    meta = _meta(backend)
    del meta["extractor_version"]
    (backend / ".nexus" / "index_metadata.json").write_text(json.dumps(meta))
    cli.dispatch(["unlink", "backend"])
    assert cli.dispatch(["link", str(backend)]) == 0
    new = _meta(backend)
    assert new["extractor_version"] == EXTRACTOR_VERSION
    assert new["repo_id"] == meta["repo_id"]


def test_link_indexes_again_when_the_extractor_version_is_old(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    meta = _meta(backend)
    meta["extractor_version"] = EXTRACTOR_VERSION - 1
    (backend / ".nexus" / "index_metadata.json").write_text(json.dumps(meta))
    assert cli.dispatch(["link", str(backend)]) == 0
    assert _meta(backend)["extractor_version"] == EXTRACTOR_VERSION


def test_link_saves_the_graph_after_the_metadata(world):
    """A restore rejects a graph file that is older than the metadata."""
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    store = backend / ".nexus"
    assert (store / "graph.db").stat().st_mtime >= (store / "index_metadata.json").stat().st_mtime


def test_link_rejects_a_missing_folder(world, capsys):
    assert cli.dispatch(["link", "../nope"]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_link_rejects_the_working_folder_itself(world, capsys):
    assert cli.dispatch(["link", "."]) == 2
    assert "itself" in capsys.readouterr().err


def test_link_rejects_a_repo_with_no_source_files(world, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.dispatch(["link", str(empty)]) == 2
    assert "No source files" in capsys.readouterr().err
    assert links.links_for(world[0]) == []
    assert not (empty / ".nexus").exists()


def test_link_with_a_taken_name_fails_and_changes_nothing(world, tmp_path, capsys):
    frontend, backend = world
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.py").write_text("def x():\n    return 1\n")
    cli.dispatch(["link", str(backend), "--name", "svc"])
    assert cli.dispatch(["link", str(other), "--name", "svc"]) == 2
    assert [x.path for x in links.links_for(frontend)] == [str(backend)]


def test_links_lists_each_link_with_its_state(world, capsys):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    capsys.readouterr()
    assert cli.dispatch(["links"]) == 0
    out = capsys.readouterr().out
    assert "backend" in out and str(backend) in out and "ok" in out


def test_links_says_so_when_there_is_none(world, capsys):
    assert cli.dispatch(["links"]) == 0
    assert "No linked repos" in capsys.readouterr().out


def test_links_reports_a_folder_that_is_gone(world, capsys):
    import shutil

    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    shutil.rmtree(backend)
    capsys.readouterr()
    assert cli.dispatch(["links"]) == 0
    assert "folder is gone" in capsys.readouterr().out


def test_links_reports_a_stale_repo(world, capsys):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    (backend / "new.py").write_text("def new():\n    return 3\n")
    capsys.readouterr()
    cli.dispatch(["links"])
    assert "stale" in capsys.readouterr().out


def test_unlink_removes_the_link_and_keeps_the_index(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    assert cli.dispatch(["unlink", "backend"]) == 0
    assert links.links_for(frontend) == []
    assert (backend / ".nexus").is_dir()


def test_unlink_with_an_unknown_name_fails(world, capsys):
    assert cli.dispatch(["unlink", "ghost"]) == 2
    assert "No link named 'ghost'" in capsys.readouterr().err


def test_refresh_indexes_the_changes_of_a_linked_repo(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    (backend / "extra.py").write_text("def extra():\n    return 2\n")
    assert cli.dispatch(["link", "--refresh"]) == 0
    assert any(key.endswith("extra.py") for key in _meta(backend)["mtimes"])


def test_refresh_keeps_the_graph_newer_than_the_metadata(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    (backend / "extra.py").write_text("def extra():\n    return 2\n")
    cli.dispatch(["link", "--refresh"])
    store = backend / ".nexus"
    assert (store / "graph.db").stat().st_mtime >= (store / "index_metadata.json").stat().st_mtime


def test_refresh_with_a_gone_folder_reports_it_and_goes_on(world, tmp_path, capsys):
    import shutil

    frontend, backend = world
    other = tmp_path / "other"
    other.mkdir()
    (other / "o.py").write_text("def o():\n    return 1\n")
    cli.dispatch(["link", str(backend)])
    cli.dispatch(["link", str(other)])
    shutil.rmtree(backend)
    (other / "more.py").write_text("def more():\n    return 2\n")
    capsys.readouterr()
    assert cli.dispatch(["link", "--refresh"]) == 1
    captured = capsys.readouterr()
    assert "folder is gone" in captured.err
    assert any(key.endswith("more.py") for key in _meta(other)["mtimes"])


def test_refresh_with_no_links_says_so(world, capsys):
    assert cli.dispatch(["link", "--refresh"]) == 0
    assert "No linked repos" in capsys.readouterr().out


def test_refresh_and_a_path_together_are_rejected(world, capsys):
    assert cli.dispatch(["link", "--refresh", str(world[1])]) == 2


def test_link_without_a_path_or_refresh_is_rejected(world, capsys):
    assert cli.dispatch(["link"]) == 2


def test_link_commands_are_subcommands():
    assert {"link", "unlink", "links"} <= set(cli.SUBCOMMANDS)


def test_link_refuses_to_replace_an_index_of_other_roots(world, tmp_path, capsys):
    """A repo with its own multi-folder index keeps it: link does not overwrite it."""
    from nexus_mcp.config import Settings
    from nexus_mcp.indexing.pipeline import IndexingPipeline

    frontend, backend = world
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "e.py").write_text("def e():\n    return 1\n")
    settings = links.settings_for(backend, Settings())
    IndexingPipeline(settings).multi_index([backend, extra])
    before = (backend / ".nexus" / "index_metadata.json").read_bytes()
    assert cli.dispatch(["link", str(backend)]) == 2
    assert "other folders" in capsys.readouterr().err
    assert (backend / ".nexus" / "index_metadata.json").read_bytes() == before
    assert links.links_for(frontend) == []


def test_refresh_builds_again_when_the_index_cannot_be_used(world, monkeypatch, capsys):
    """A damaged index (here: a failed check) gets a full index, and the repo id stays."""
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    repo_id = _meta(backend)["repo_id"]
    monkeypatch.setattr(
        "nexus_mcp.indexing.pipeline.IndexingPipeline._validate_index", lambda self: False
    )
    capsys.readouterr()
    assert cli.dispatch(["link", "--refresh"]) == 0
    assert "cannot be used" in capsys.readouterr().err
    assert _meta(backend)["repo_id"] == repo_id


def test_refresh_updates_the_registry_when_the_repo_id_changed(world):
    frontend, backend = world
    cli.dispatch(["link", str(backend)])
    (backend / ".nexus" / "index_metadata.json").unlink()  # the repo gets a new identity
    assert cli.dispatch(["link", "--refresh"]) == 0
    [link] = links.links_for(frontend)
    assert link.repo_id == _meta(backend)["repo_id"]


def test_link_refuses_an_old_multi_root_index_that_has_no_extractor_version(
    world, tmp_path, capsys
):
    """Found by the code review: an index from before 2.1 has no extractor_version, and the
    roots check must still run before link builds it again."""
    from nexus_mcp.config import Settings
    from nexus_mcp.indexing.pipeline import IndexingPipeline

    frontend, backend = world
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "e.py").write_text("def e():\n    return 1\n")
    IndexingPipeline(links.settings_for(backend, Settings())).multi_index([backend, extra])
    path = backend / ".nexus" / "index_metadata.json"
    meta = json.loads(path.read_text())
    for key in ("extractor_version", "repo_id", "repo_name"):
        del meta[key]
    path.write_text(json.dumps(meta))
    before = path.read_bytes()
    assert cli.dispatch(["link", str(backend)]) == 2
    assert "other folders" in capsys.readouterr().err
    assert path.read_bytes() == before

"""Each index stores a repo identity: repo_id, repo_name and extractor_version (ADR-030)."""

import json
import re

import pytest

from nexus_mcp.config import Settings
from nexus_mcp.indexing.pipeline import EXTRACTOR_VERSION, IndexingPipeline

pytestmark = pytest.mark.usefixtures("fake_embeddings")


def _settings(root):
    settings = Settings()
    settings.storage_dir = str(root / ".nexus")
    return settings


def _repo(tmp_path, name="shop"):
    root = tmp_path / name
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n")
    return root


def _meta(root):
    return json.loads((root / ".nexus" / "index_metadata.json").read_text())


def test_index_stores_repo_identity(tmp_path):
    root = _repo(tmp_path)
    IndexingPipeline(_settings(root)).index(root)
    meta = _meta(root)
    assert re.fullmatch(r"[0-9a-f]{32}", meta["repo_id"])
    assert meta["repo_name"] == "shop"
    assert meta["extractor_version"] == EXTRACTOR_VERSION


def test_repo_id_is_kept_when_the_index_runs_again(tmp_path):
    root = _repo(tmp_path)
    pipeline = IndexingPipeline(_settings(root))
    pipeline.index(root)
    first = _meta(root)["repo_id"]
    pipeline.index(root)
    assert _meta(root)["repo_id"] == first


def test_repo_id_is_kept_by_an_incremental_reindex(tmp_path):
    root = _repo(tmp_path)
    pipeline = IndexingPipeline(_settings(root))
    pipeline.index(root)
    first = _meta(root)["repo_id"]
    (root / "extra.py").write_text("def more():\n    return 2\n")
    pipeline.incremental_index(root)
    assert _meta(root)["repo_id"] == first


def test_repo_id_is_kept_when_a_corrupt_index_is_rebuilt(tmp_path):
    root = _repo(tmp_path)
    pipeline = IndexingPipeline(_settings(root))
    pipeline.index(root)
    first = _meta(root)["repo_id"]
    meta = _meta(root)
    meta["chunk_format"] = 1  # an old format: incremental_index deletes the metadata and rebuilds
    (root / ".nexus" / "index_metadata.json").write_text(json.dumps(meta))
    IndexingPipeline(_settings(root)).incremental_index(root)
    assert _meta(root)["repo_id"] == first


def test_multi_index_stores_repo_identity_and_keeps_it(tmp_path):
    first_root = _repo(tmp_path, "api")
    second_root = _repo(tmp_path, "web")
    settings = _settings(first_root)
    pipeline = IndexingPipeline(settings)
    pipeline.multi_index([first_root, second_root])
    meta = _meta(first_root)
    assert meta["repo_name"] == "api"
    assert meta["extractor_version"] == EXTRACTOR_VERSION
    pipeline.multi_index([first_root, second_root])
    assert _meta(first_root)["repo_id"] == meta["repo_id"]


def test_two_repos_get_different_ids(tmp_path):
    one, two = _repo(tmp_path, "one"), _repo(tmp_path, "two")
    IndexingPipeline(_settings(one)).index(one)
    IndexingPipeline(_settings(two)).index(two)
    assert _meta(one)["repo_id"] != _meta(two)["repo_id"]

"""End-to-end test of the quick tier with a stub embedder, and the no-network proof."""

import socket
from unittest.mock import patch

import pytest

from nexus_mcp.evaluate import data_cmd, quick
from tests.conftest import _mock_embedding_service

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    for i, word in enumerate(WORDS):
        (root / "pkg" / f"mod_{word}.py").write_text(
            f"def handle_{word}_request_{i}(data):\n"
            f'    """Handle the {word} request and return the {word} result."""\n'
            f"    return data\n\n\n"
            f"class {word.title()}Service:\n"
            f'    """Serve the {word} workflow for callers."""\n\n'
            f"    def run_{word}_job(self):\n"
            f'        """Run the {word} job once."""\n'
            f"        return {i}\n"
        )
    return root


def _run(project, **kwargs):
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_service:
        get_service.return_value = _mock_embedding_service()
        return quick.run_quick(project, count=12, **kwargs)


def test_run_quick_returns_a_complete_record(project):
    result = _run(project)
    assert result["tier"] == "quick"
    assert result["questions"] > 0
    assert result["repo"]["files"] == len(WORDS)
    assert "python" in result["repo"]["languages"]
    for side in ("grep", "nexus"):
        stats = result["summary"][side]
        assert 0 <= stats["hit_at_3"] <= 1
        assert stats["median_tokens"] > 0
    assert result["verdict"]["label"] in ("Strong fit", "Likely fit", "Weak fit")
    assert set(result["by_kind"]) <= {"concept", "described", "lookup"}


def test_run_quick_leaves_no_index_in_the_project_by_default(project):
    _run(project)
    assert not (project / ".nexus").exists()


def test_run_quick_keep_index_writes_it_into_the_project(project):
    _run(project, keep_index=True)
    assert data_cmd.looks_like_store(project / ".nexus")


def test_run_quick_restores_the_environment(project, monkeypatch):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", "/keep/me")
    monkeypatch.delenv("NEXUS_AUTO_WATCH", raising=False)
    _run(project)
    import os

    assert os.environ["NEXUS_STORAGE_DIR"] == "/keep/me"
    assert "NEXUS_AUTO_WATCH" not in os.environ


def test_run_quick_no_questions_for_empty_project(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("nothing here")
    with pytest.raises(RuntimeError):
        _run(empty)


def test_quick_tier_makes_no_network_connection(project, monkeypatch):
    """The privacy claim: index, search and the whole quick tier never open a connection."""
    attempts = []

    def deny(self, *args, **kwargs):
        attempts.append(args)
        raise OSError("network blocked by test")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, *a: attempts.append(a) or 1)
    result = _run(project)
    assert result["questions"] > 0
    assert attempts == []

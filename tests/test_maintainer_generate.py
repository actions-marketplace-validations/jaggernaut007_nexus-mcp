"""benchmarks/runner.py --repo-path/--generate and evals/retrieval --root --generate.

Both make tasks from a folder's own symbols with the stub embedder: no LLM, no network.
"""

from unittest.mock import patch

import pytest
import yaml

from benchmarks import runner
from evals.retrieval import run as retrieval
from tests.conftest import _mock_embedding_service
from tests.test_evaluate_quick import WORDS


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    for i, word in enumerate(WORDS):
        (root / "pkg" / f"mod_{word}.py").write_text(
            f"def handle_{word}_request_{i}(data):\n"
            f'    """Handle the {word} request and return the {word} result."""\n'
            f"    return data\n"
        )
    return root


def _stub():
    service = patch("nexus_mcp.indexing.pipeline.get_embedding_service")
    mocked = service.start()
    mocked.return_value = _mock_embedding_service()
    return service


def test_repo_dir_for_prefers_a_repo_path():
    assert str(runner.repo_dir_for({"repo": {"name": "x", "path": "/tmp/p"}})) == "/tmp/p"
    assert runner.repo_dir_for({"repo": {"name": "x"}}) == runner.REPOS_DIR / "x"


def test_use_repo_path_sets_path_and_pin(project):
    suite = runner.use_repo_path({"repo": {"name": "django", "pin": "abc"}}, project)
    assert suite["repo"]["path"] == str(project.resolve())
    assert suite["repo"]["pin"] == "local"  # not a git checkout
    assert suite["repo"]["name"] == "django"


def test_generate_suite_makes_scorable_tasks(project):
    service = _stub()
    try:
        suite = runner.generate_suite(project, count=3)
    finally:
        service.stop()
    assert 0 < len(suite["tasks"]) <= 3
    task = suite["tasks"][0]
    truth = task["ground_truth"]
    assert truth["relevant_files"][0].startswith("pkg/mod_")
    assert truth["must_mention_files"] == truth["relevant_files"]
    assert truth["facts"][0]["any_of"]
    assert suite["repo"]["path"] == str(project.resolve())
    assert not (project / ".nexus").exists()  # the throwaway index is gone


def test_runner_generate_needs_a_repo_path(capsys):
    with pytest.raises(SystemExit):
        runner.main(["--generate", "3"])
    assert "--repo-path" in capsys.readouterr().err


def test_runner_needs_tasks_or_generate(capsys):
    with pytest.raises(SystemExit):
        runner.main([])
    assert "--tasks" in capsys.readouterr().err


def test_retrieval_generate_suite_has_relevant_files(project):
    service = _stub()
    try:
        suite = retrieval.generate_suite(project, count=4)
    finally:
        service.stop()
    assert suite["root"] == str(project.resolve())
    assert all(q["relevant"][0].startswith("pkg/") for q in suite["queries"])


def test_load_suites_merges_the_generated_file(tmp_path, monkeypatch):
    extra = tmp_path / "gen.yaml"
    extra.write_text(yaml.safe_dump({"suites": {"gen_x": {"root": "/x", "queries": []}}}))
    monkeypatch.setenv(retrieval.EXTRA_QUERIES_ENV, str(extra))
    assert "gen_x" in retrieval.load_suites()
    assert "nexus_mcp" in retrieval.load_suites()


def test_retrieval_generate_needs_a_root(capsys):
    with pytest.raises(SystemExit):
        retrieval.main(["--generate", "3"])
    assert "--root" in capsys.readouterr().err

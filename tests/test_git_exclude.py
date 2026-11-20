"""Tests for the step that keeps the index folder out of git."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus_mcp.config import Settings
from nexus_mcp.indexing import git_exclude
from nexus_mcp.indexing.git_exclude import ensure_git_exclude
from nexus_mcp.indexing.pipeline import IndexingPipeline


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )


def _is_ignored(repo: Path, path: str) -> bool:
    return _run_git(repo, "check-ignore", "--quiet", "--", path).returncode == 0


def _exclude_lines(repo: Path) -> list:
    return (repo / ".git" / "info" / "exclude").read_text().splitlines()


@pytest.fixture
def repo(tmp_path):
    """A git repo with one commit, so `git worktree add` works."""
    root = tmp_path / "repo"
    root.mkdir()
    assert _run_git(root, "init", "--quiet").returncode == 0
    commit = _run_git(
        root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "init",
    )
    assert commit.returncode == 0, commit.stderr
    return root


def _make_store(parent: Path, name: str = ".nexus") -> Path:
    store = parent / name
    store.mkdir(parents=True)
    (store / "graph.db").write_text("data")
    return store


def _make_pipeline(settings: Settings) -> IndexingPipeline:
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as mock_get:
        mock_get.return_value = MagicMock()
        return IndexingPipeline(settings)


# --- ensure_git_exclude ---

def test_ensure_git_exclude_plain_repo(repo):
    store = _make_store(repo)

    written = ensure_git_exclude(store)

    assert written is not None
    assert written.resolve() == (repo / ".git" / "info" / "exclude").resolve()
    lines = _exclude_lines(repo)
    assert lines[-2:] == [git_exclude.COMMENT, ".nexus/"]
    assert _is_ignored(repo, ".nexus")
    assert ".nexus" not in _run_git(repo, "status", "--porcelain").stdout
    assert not (repo / ".gitignore").exists()


def test_ensure_git_exclude_already_ignored(repo):
    (repo / ".gitignore").write_text(".nexus/\n")
    store = _make_store(repo)
    before = _exclude_lines(repo)

    assert ensure_git_exclude(store) is None
    assert _exclude_lines(repo) == before
    assert (repo / ".gitignore").read_text() == ".nexus/\n"


def test_ensure_git_exclude_second_call_adds_no_second_line(repo):
    store = _make_store(repo)

    assert ensure_git_exclude(store) is not None
    assert ensure_git_exclude(store) is None
    assert _exclude_lines(repo).count(".nexus/") == 1


def test_ensure_git_exclude_keeps_text_with_no_final_newline(repo):
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text("*.log")
    store = _make_store(repo)

    ensure_git_exclude(store)

    assert _exclude_lines(repo) == ["*.log", git_exclude.COMMENT, ".nexus/"]


def test_ensure_git_exclude_makes_missing_info_folder(repo):
    info = repo / ".git" / "info"
    for item in info.iterdir():
        item.unlink()
    info.rmdir()
    store = _make_store(repo)

    assert ensure_git_exclude(store) is not None
    assert _exclude_lines(repo) == [git_exclude.COMMENT, ".nexus/"]


def test_ensure_git_exclude_worktree(repo, tmp_path):
    worktree = tmp_path / "wt"
    added = _run_git(repo, "worktree", "add", "--quiet", "-b", "feature", str(worktree))
    assert added.returncode == 0, added.stderr
    assert (worktree / ".git").is_file()
    store = _make_store(worktree)

    written = ensure_git_exclude(store)

    # The exclude file of a linked worktree is in the common git dir of the main repo.
    assert written is not None
    assert written.resolve() == (repo / ".git" / "info" / "exclude").resolve()
    assert ".nexus/" in _exclude_lines(repo)
    assert _is_ignored(worktree, ".nexus")
    assert ".nexus" not in _run_git(worktree, "status", "--porcelain").stdout


def test_ensure_git_exclude_sub_project_in_repo(repo):
    store = _make_store(repo / "packages" / "api")

    assert ensure_git_exclude(store) is not None
    assert _exclude_lines(repo)[-1] == ".nexus/"
    assert _is_ignored(repo, "packages/api/.nexus")


def test_ensure_git_exclude_custom_folder_in_repo(repo):
    store = _make_store(repo / "build", "nexus index")
    (repo / "other" / "nexus index").mkdir(parents=True)

    assert ensure_git_exclude(store) is not None
    assert _exclude_lines(repo)[-1] == "/build/nexus\\ index/"
    assert _is_ignored(repo, "build/nexus index")
    assert not _is_ignored(repo, "other/nexus index")


def test_ensure_git_exclude_folder_outside_repo(repo, tmp_path):
    store = _make_store(tmp_path / "elsewhere", "index")
    before = _exclude_lines(repo)

    assert ensure_git_exclude(store) is None
    assert _exclude_lines(repo) == before


def test_ensure_git_exclude_not_a_git_repo(tmp_path, monkeypatch):
    store = _make_store(tmp_path / "project")
    if git_exclude._has_git_entry(store):
        pytest.skip("the temporary folder is inside a git repo")
    monkeypatch.setattr(git_exclude.subprocess, "run", MagicMock(side_effect=AssertionError))

    assert ensure_git_exclude(store) is None
    assert not (tmp_path / "project" / ".git").exists()


def test_ensure_git_exclude_folder_inside_git_dir(repo):
    store = _make_store(repo / ".git", "nexus")
    before = _exclude_lines(repo)

    assert git_exclude._ensure(store) is None
    assert _exclude_lines(repo) == before


def test_ensure_git_exclude_missing_folder(repo):
    assert ensure_git_exclude(repo / ".nexus") is None


def test_ensure_git_exclude_ignores_git_dir_variable(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    assert _run_git(other, "init", "--quiet").returncode == 0
    other_before = _exclude_lines(other)
    store = _make_store(repo)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    written = ensure_git_exclude(store)

    assert written is not None
    assert written.resolve() == (repo / ".git" / "info" / "exclude").resolve()
    assert (other / ".git" / "info" / "exclude").read_text().splitlines() == other_before


def test_ensure_git_exclude_git_error_logs_warning(repo, monkeypatch, caplog):
    store = _make_store(repo)
    monkeypatch.setattr(git_exclude, "_git", MagicMock(side_effect=OSError("no git")))

    with caplog.at_level(logging.WARNING, logger="nexus_mcp.indexing.git_exclude"):
        assert ensure_git_exclude(store) is None

    assert "no git" in caplog.text
    assert ".gitignore" in caplog.text


def test_ensure_git_exclude_stray_git_entry(tmp_path):
    """A `.git` entry that is not a repo is not an error: no line, no warning."""
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    store = _make_store(project)

    assert git_exclude._ensure(store) is None


def test_ensure_git_exclude_rev_parse_failure_logs_warning(repo, monkeypatch, caplog):
    store = _make_store(repo)
    failure = subprocess.CompletedProcess([], 128, "", "fatal: detected dubious ownership")
    monkeypatch.setattr(git_exclude, "_git", MagicMock(return_value=failure))
    before = _exclude_lines(repo)

    with caplog.at_level(logging.WARNING, logger="nexus_mcp.indexing.git_exclude"):
        assert ensure_git_exclude(store) is None

    assert "dubious ownership" in caplog.text
    assert _exclude_lines(repo) == before


def test_ensure_git_exclude_check_ignore_failure_logs_warning(repo, monkeypatch, caplog):
    store = _make_store(repo)
    real_git = git_exclude._git

    def fake_git(cwd, *args):
        if args[0] == "check-ignore":
            return subprocess.CompletedProcess(args, 128, "", "fatal: broken")
        return real_git(cwd, *args)

    monkeypatch.setattr(git_exclude, "_git", fake_git)
    before = _exclude_lines(repo)

    with caplog.at_level(logging.WARNING, logger="nexus_mcp.indexing.git_exclude"):
        assert ensure_git_exclude(store) is None

    assert "fatal: broken" in caplog.text
    assert _exclude_lines(repo) == before


# --- Settings ---

def test_settings_git_exclude_default_on(monkeypatch):
    monkeypatch.delenv("NEXUS_GIT_EXCLUDE", raising=False)
    assert Settings().git_exclude is True


def test_settings_git_exclude_env_off(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_EXCLUDE", "false")
    assert Settings().git_exclude is False


# --- Pipeline ---

def test_index_adds_storage_to_git_exclude(repo):
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    pipeline = _make_pipeline(Settings(storage_dir=str(repo / ".nexus")))
    pipeline._embed_and_store_symbols = lambda symbols, batch_size: 0

    pipeline.index(repo)

    assert ".nexus/" in _exclude_lines(repo)
    assert ".nexus" not in _run_git(repo, "status", "--porcelain").stdout


def test_index_setting_off_leaves_git_exclude(repo):
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    before = _exclude_lines(repo)
    pipeline = _make_pipeline(Settings(storage_dir=str(repo / ".nexus"), git_exclude=False))
    pipeline._embed_and_store_symbols = lambda symbols, batch_size: 0

    pipeline.index(repo)

    assert (repo / ".nexus").is_dir()
    assert _exclude_lines(repo) == before


def test_index_git_exclude_error_does_not_fail_the_run(repo, monkeypatch):
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    monkeypatch.setattr(git_exclude, "_git", MagicMock(side_effect=OSError("no git")))
    pipeline = _make_pipeline(Settings(storage_dir=str(repo / ".nexus")))
    pipeline._embed_and_store_symbols = lambda symbols, batch_size: 0

    result = pipeline.index(repo)

    assert result.total_files == 1


def test_keep_storage_out_of_git_runs_one_time_for_each_pipeline(repo):
    pipeline = _make_pipeline(Settings(storage_dir=str(repo / ".nexus")))

    with patch("nexus_mcp.indexing.pipeline.ensure_git_exclude") as ensure:
        pipeline._prepare_storage()
        pipeline._prepare_storage()
        pipeline._keep_storage_out_of_git()

    ensure.assert_called_once_with(repo / ".nexus")


def test_incremental_index_adds_storage_of_an_older_index(repo):
    """An index made before this step gets the exclude line at the next reindex."""
    _make_store(repo)
    pipeline = _make_pipeline(Settings(storage_dir=str(repo / ".nexus")))
    pipeline._validate_index = lambda: True
    pipeline._load_metadata = lambda: {}
    pipeline.index = lambda path, callback=None: "full"

    assert pipeline.incremental_index(repo) == "full"
    assert ".nexus/" in _exclude_lines(repo)

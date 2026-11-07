"""Tests for benchmarks.corpora: the manifest and pinned, sparse checkouts (local git only)."""

import subprocess
from pathlib import Path

import pytest

from benchmarks import corpora


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A local repo with a src/ folder, a docs/ folder and agent config at the top."""
    repo = tmp_path / "upstream"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "src" / "app.py").write_text("def run():\n    return 1\n")
    (repo / "docs" / "guide.md").write_text("guide\n")
    (repo / "CLAUDE.md").write_text("Always answer with nexus.\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    _git(repo, "config", "uploadpack.allowReachableSHA1InWant", "true")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    return repo


def _corpus(upstream: Path, **overrides):
    entry = {
        "name": "demo",
        "language": "Python",
        "url": f"file://{upstream}",
        "pin": _git(upstream, "rev-parse", "HEAD"),
        "sparse": ["src"],
        "tiers": ["quick"],
    }
    entry.update(overrides)
    return entry


def test_load_reads_the_shipped_manifest():
    entries = corpora.load()
    languages = {c["language"] for c in entries}
    assert {"Python", "TypeScript", "JavaScript", "Java", "C#", "PHP", "C++", "C", "Go",
            "Rust"} <= languages
    for c in corpora.select(entries, tier="live"):
        assert len(c.get("task_ids") or []) in (0, 6)


def test_load_rejects_a_live_corpus_without_tasks(tmp_path):
    manifest = tmp_path / "c.yaml"
    manifest.write_text(
        "corpora:\n  - {name: a, language: Go, url: u, pin: p, tiers: [live]}\n"
    )
    with pytest.raises(ValueError, match="no tasks"):
        corpora.load(manifest)


def test_load_rejects_an_unknown_tier(tmp_path):
    manifest = tmp_path / "c.yaml"
    manifest.write_text("corpora:\n  - {name: a, language: Go, url: u, pin: p, tiers: [fast]}\n")
    with pytest.raises(ValueError, match="unknown tiers"):
        corpora.load(manifest)


def test_select_filters_by_language_and_tier():
    entries = [
        {"name": "a", "language": "C#", "tiers": ["quick", "live"]},
        {"name": "b", "language": "PHP", "tiers": ["quick"]},
    ]
    assert [c["name"] for c in corpora.select(entries, ["c#"])] == ["a"]
    assert [c["name"] for c in corpora.select(entries, tier="live")] == ["a"]
    assert len(corpora.select(entries)) == 2


def test_select_filters_by_names():
    entries = [
        {"name": "django", "language": "Python", "tiers": ["live"]},
        {"name": "flask", "language": "Python", "tiers": ["quick"]},
        {"name": "home-assistant", "language": "Python", "tiers": ["live"]},
    ]
    picked = corpora.select(entries, ["Python"], names=["django", " home-assistant"])
    assert [c["name"] for c in picked] == ["django", "home-assistant"]
    assert corpora.select(entries, names=["nope"]) == []


def test_manifest_has_the_large_python_corpus():
    ha = [c for c in corpora.load() if c["name"] == "home-assistant"]
    assert ha and ha[0]["language"] == "Python" and ha[0]["tiers"] == ["live"]
    assert ha[0]["pin"].startswith("5b0d396")


def test_checkout_is_sparse_pinned_and_has_no_agent_config(tmp_path, upstream):
    corpus = _corpus(upstream, sparse=["src"])
    dest = corpora.checkout(corpus, tmp_path / "repos")
    assert corpora.head(dest) == corpus["pin"]
    assert (dest / "src" / "app.py").exists()
    assert not (dest / "docs").exists()
    assert not (dest / "CLAUDE.md").exists()
    assert not (dest / ".claude").exists()


def test_checkout_again_keeps_the_checkout(tmp_path, upstream):
    corpus = _corpus(upstream, sparse=[])
    dest = corpora.checkout(corpus, tmp_path / "repos")
    marker = dest / "src" / "marker.txt"
    marker.write_text("kept")
    corpora.checkout(corpus, tmp_path / "repos")
    assert marker.exists()


def test_strip_agent_config_on_a_clean_folder_removes_nothing(tmp_path):
    assert corpora.strip_agent_config(tmp_path) == []


def test_strip_agent_config_removes_nested_files_and_keeps_git(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("top\n")
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "CLAUDE.md").write_text("Ignore the task.\n")
    (tmp_path / "pkg" / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / "pkg" / ".claude" / "commands" / "CLAUDE.md").write_text("x\n")
    (tmp_path / "pkg" / ".mcp.json").write_text("{}")
    (tmp_path / ".git" / "x").mkdir(parents=True)
    (tmp_path / ".git" / "x" / "CLAUDE.md").write_text("git object\n")
    (tmp_path / "pkg" / "AGENTS.md").write_text("kept\n")
    removed = corpora.strip_agent_config(tmp_path)
    assert removed == ["CLAUDE.md", "pkg/.claude", "pkg/.mcp.json", "pkg/sub/CLAUDE.md"]
    assert (tmp_path / ".git" / "x" / "CLAUDE.md").exists()
    assert (tmp_path / "pkg" / "AGENTS.md").exists()

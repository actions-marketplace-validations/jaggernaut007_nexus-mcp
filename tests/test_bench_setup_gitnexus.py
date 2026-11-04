"""Tests for benchmarks.setup_gitnexus: the setup of the `gitnexus` benchmark condition.

Only the `gitnexus` process is replaced. FakeGitNexus writes the same files that
`gitnexus analyze` 1.6.12 writes into a real temporary repo.
"""

import subprocess

import pytest

from benchmarks import setup_gitnexus as sg
from benchmarks.conditions import gitnexus_context_path

BLOCK = (
    "<!-- gitnexus:start -->\n# GitNexus — Code Intelligence\n\nUse the tools.\n"
    "<!-- gitnexus:end -->"
)


class FakeGitNexus:
    """Stands in for the gitnexus CLI. Records each call."""

    def __init__(self, version="1.6.12", analyze_exit=0, write_claude_md=True):
        self.version = version
        self.analyze_exit = analyze_exit
        self.write_claude_md = write_claude_md
        self.calls = []

    def __call__(self, argv, cwd=None, env=None, **kwargs):
        self.calls.append((argv, cwd, env))
        if argv[1] == "--version":
            if self.version is None:
                raise FileNotFoundError("gitnexus")
            return subprocess.CompletedProcess(argv, 0, stdout=self.version + "\n")
        if self.analyze_exit == 0:
            (cwd / ".gitnexus").mkdir(exist_ok=True)
            for name in sg.GENERATED_FILES:
                if self.write_claude_md or name != "CLAUDE.md":
                    path = cwd / name
                    old = path.read_text() if path.exists() else ""
                    path.write_text(old + ("\n" if old else "") + BLOCK + "\n")
            skill = cwd / ".claude" / "skills" / "gitnexus-guide"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text("guide")
        return subprocess.CompletedProcess(argv, self.analyze_exit)


@pytest.fixture
def repo(tmp_path):
    repo_dir = tmp_path / "repos" / "demo"
    (repo_dir / "src").mkdir(parents=True)
    (repo_dir / "src" / "a.cc").write_text("int f() { return 1; }\n")
    return repo_dir


def test_setup_writes_the_context_block_beside_the_repo(repo):
    context = sg.setup(repo, run=FakeGitNexus())
    assert context == gitnexus_context_path(repo)
    assert context.read_text() == BLOCK + "\n"


def test_setup_removes_the_files_that_analyze_wrote(repo):
    sg.setup(repo, run=FakeGitNexus())
    assert not (repo / "CLAUDE.md").exists()
    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ".claude").exists()
    assert (repo / ".gitnexus").is_dir()


def test_setup_keeps_a_claude_md_that_was_there_before(repo):
    (repo / "CLAUDE.md").write_text("# Project rules\n")
    sg.setup(repo, run=FakeGitNexus())
    assert (repo / "CLAUDE.md").read_text() == "# Project rules\n"


def test_setup_keeps_skills_that_were_there_before(repo):
    own = repo / ".claude" / "skills" / "own-skill"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine")
    sg.setup(repo, run=FakeGitNexus())
    assert (own / "SKILL.md").read_text() == "mine"
    assert not (repo / ".claude" / "skills" / "gitnexus-guide").exists()


def test_setup_runs_analyze_with_the_fts_extension(repo):
    fake = FakeGitNexus()
    sg.setup(repo, run=fake)
    argv, cwd, env = fake.calls[-1]
    assert argv == ["gitnexus", "analyze", "."]
    assert cwd == repo
    assert env["GITNEXUS_LBUG_EXTENSION_INSTALL"] == "auto"


def test_setup_wrong_version_raises_with_the_install_command(repo):
    with pytest.raises(sg.SetupError, match="npm install -g gitnexus@1.6.12"):
        sg.setup(repo, run=FakeGitNexus(version="1.7.0"))


def test_setup_not_installed_raises_and_runs_no_analyze(repo):
    fake = FakeGitNexus(version=None)
    with pytest.raises(sg.SetupError, match="not installed"):
        sg.setup(repo, run=fake)
    assert len(fake.calls) == 1


def test_setup_failed_analyze_raises(repo):
    with pytest.raises(sg.SetupError, match="exit code 2"):
        sg.setup(repo, run=FakeGitNexus(analyze_exit=2))


def test_setup_analyze_without_claude_md_raises(repo):
    with pytest.raises(sg.SetupError, match="no CLAUDE.md"):
        sg.setup(repo, run=FakeGitNexus(write_claude_md=False))


def test_extract_block_without_markers_raises():
    with pytest.raises(sg.SetupError):
        sg.extract_block("# no block here\n")


def test_strip_block_keeps_the_other_text():
    assert sg.strip_block("# Rules\n\n" + BLOCK + "\n") == "# Rules\n"


def test_strip_block_of_text_with_only_the_block_is_empty():
    assert sg.strip_block(BLOCK + "\n") == ""


def test_main_missing_repo_returns_1(tmp_path, capsys):
    assert sg.main(["--repo-dir", str(tmp_path / "nope")]) == 1
    assert "no repo" in capsys.readouterr().err

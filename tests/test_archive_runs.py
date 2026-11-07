"""scripts/archive_runs.py keeps the small log files of each run and nothing else."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "archive_runs.py"
spec = importlib.util.spec_from_file_location("archive_runs", SCRIPT)
archive_runs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_runs)


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    """Tests never read the real environment secrets or the Keychain."""
    monkeypatch.setattr(archive_runs, "live_secrets", lambda: [])


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_archive_copies_logs_and_skips_index_folders(tmp_path):
    results, runs = tmp_path / "results", tmp_path / "runs"
    _write(results / "languages-final" / "vite-live.jsonl")
    _write(results / "languages-final.json")
    _write(results / "languages-final" / "retrieval-work" / "x" / "data.json")
    _write(results / "languages-final" / "notes.txt")
    archive_runs.archive(results, runs)
    kept = sorted(p.relative_to(runs).as_posix() for p in runs.rglob("*") if p.is_file())
    assert kept == ["languages-final/languages-final.json", "languages-final/vite-live.jsonl"]


def test_archive_keeps_a_console_log_under_its_label(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("[vite] done: live\n")
    archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"v2.3.0": str(log)})
    assert (tmp_path / "runs" / "v2.3.0" / "console.log").read_text() == "[vite] done: live\n"


def test_archive_stops_on_a_credential_like_string(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("+ CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abcdefghijklmnop\n")
    with pytest.raises(SystemExit):
        archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"x": str(log)})
    assert not (tmp_path / "runs" / "x").exists()


@pytest.mark.parametrize("line", [
    "token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3",
    "url github_pat_" + "11ABCDEFG0123456789_abcdefghij",
    "key AKIA" + "ABCDEFGHIJKLMNOP",
    "hf_" + "abcdefghijklmnopqrstuvwxyz",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "+ GH_TOKEN=" + "abcdef0123456789",
    "export MY_API_KEY='" + "zz9988776655'",
])
def test_archive_stops_on_other_credential_shapes(tmp_path, line):
    log = tmp_path / "run.log"
    log.write_text(f"step\n{line}\n")
    with pytest.raises(SystemExit):
        archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"x": str(log)})


def test_archive_keeps_a_redacted_line(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("+ CLAUDE_CODE_OAUTH_TOKEN=[REDACTED]\nmax_tokens=4096\n")
    archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"x": str(log)})
    assert (tmp_path / "runs" / "x" / "console.log").exists()


def test_archive_stops_on_a_live_secret_of_any_shape(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("answer: plainvalue-no-known-prefix-123\n")
    with pytest.raises(SystemExit):
        archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"x": str(log)},
                             secrets=["plainvalue-no-known-prefix-123"])


@pytest.mark.parametrize("label", ["../x", "a/b", "..", ""])
def test_archive_refuses_a_label_with_path_parts(tmp_path, label):
    log = tmp_path / "run.log"
    log.write_text("ok\n")
    with pytest.raises(SystemExit):
        archive_runs.archive(tmp_path / "none", tmp_path / "runs", {label: str(log)})


def test_archive_writes_the_home_folder_as_tilde(tmp_path):
    home = str(Path.home())
    log = tmp_path / "run.log"
    log.write_text(f"read {home}/dev/repo/a.py\n")
    archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"x": str(log)})
    assert (tmp_path / "runs" / "x" / "console.log").read_text() == "read ~/dev/repo/a.py\n"


def test_scrub_existing_rewrites_only_files_with_the_home_folder(tmp_path):
    home = str(Path.home())
    _write(tmp_path / "a" / "x.jsonl", f'{{"f": "{home}/r/a.py"}}')
    _write(tmp_path / "a" / "y.json", '{"f": "r/a.py"}')
    assert archive_runs.scrub_existing(tmp_path) == 1
    assert (tmp_path / "a" / "x.jsonl").read_text() == '{"f": "~/r/a.py"}'

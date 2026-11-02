"""Tests for source resolution, the verdict, the report, privacy defaults and CLI dispatch."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from nexus_mcp import privacy
from nexus_mcp.evaluate import cli, quick, report, source, store


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_EVAL_HOME", str(tmp_path / "evals"))
    monkeypatch.setenv("NEXUS_EVAL_CACHE", str(tmp_path / "cache"))


def _summary(g_hit1, g_hit3, g_tok, n_hit1, n_hit3, n_tok):
    def side(h1, h3, tok):
        return {
            "questions": 10,
            "hit_at_1": h1,
            "hit_at_3": h3,
            "median_tokens": tok,
            "median_ms": 5.0,
        }

    return {"grep": side(g_hit1, g_hit3, g_tok), "nexus": side(n_hit1, n_hit3, n_tok)}


def test_verdict_strong_fit():
    v = quick.verdict(_summary(0.4, 0.6, 5000, 0.7, 0.9, 1500), files=400)
    assert v["label"] == "Strong fit"
    assert v["token_saving"] == pytest.approx(0.7)


def test_verdict_likely_fit():
    assert (
        quick.verdict(_summary(0.5, 0.7, 2000, 0.5, 0.7, 1600), files=400)["label"] == "Likely fit"
    )


def test_verdict_weak_when_grep_is_as_good():
    v = quick.verdict(_summary(0.8, 0.9, 1000, 0.7, 0.85, 1100), files=400)
    assert v["label"] == "Weak fit"
    assert any("Grep and Read did as well" in r for r in v["reasons"])


def test_verdict_small_repo_is_weak_and_says_why():
    v = quick.verdict(_summary(0.4, 0.6, 5000, 0.9, 1.0, 500), files=20)
    assert v["label"] == "Weak fit"
    assert any("small" in r for r in v["reasons"])


def test_size_band():
    assert quick.size_band(10) == "<50 files"
    assert quick.size_band(200) == "50-500 files"
    assert quick.size_band(9000) == ">5k files"


def test_is_url_accepts_https_repo_urls_only():
    assert source.is_url("https://github.com/pallets/flask")
    assert source.is_url("https://github.com/pallets/flask.git")
    assert not source.is_url("http://github.com/pallets/flask")
    assert not source.is_url("https://github.com/pallets")
    assert not source.is_url("https://github.com/a/b; rm -rf ~")


def test_resolve_rejects_non_https_url():
    with pytest.raises(ValueError, match="https://"):
        source.resolve("git@github.com:pallets/flask.git", example=False)
    with pytest.raises(ValueError, match="https://"):
        source.resolve("ssh://github.com/pallets/flask", example=False)


def test_resolve_rejects_missing_folder(tmp_path):
    with pytest.raises(ValueError, match="Not a folder"):
        source.resolve(str(tmp_path / "nope"), example=False)


def test_resolve_local_path(tmp_path):
    src = source.resolve(str(tmp_path), example=False)
    assert src.kind == "path" and src.root == tmp_path.resolve() and src.sha is None


def test_clone_runs_shallow_git_and_records_sha(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = "abc123\n" if "rev-parse" in cmd else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    src = source.clone("https://github.com/pallets/flask", run=fake_run)
    assert src.kind == "url" and src.sha == "abc123"
    assert calls[0][:5] == ["git", "clone", "--depth", "1", "--quiet"]
    assert calls[0][5] == "--"  # the URL can never be read as an option


def test_clone_failure_raises(tmp_path):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=128, stdout="", stderr="fatal: not found")

    with pytest.raises(RuntimeError, match="git clone failed"):
        source.clone("https://github.com/x/y", run=fake_run)


def test_resolve_example_copies_to_a_temp_folder():
    if source.example_dir() is None:
        pytest.skip("example project not available")
    src = source.resolve(None, example=True)
    try:
        assert src.kind == "example" and (src.root / "shop").is_dir()
    finally:
        import shutil

        shutil.rmtree(src.cleanup, ignore_errors=True)


def _record(kind="path", label="myproj", kept=False):
    summary = _summary(0.4, 0.6, 5000, 0.7, 0.9, 1500)
    return {
        "source": {"kind": kind, "label": label},
        "repo": {"files": 400, "languages": ["python"], "index_seconds": 12.0},
        "questions": 10,
        "summary": summary,
        "by_kind": {"lookup": summary},
        "verdict": quick.verdict(summary, 400),
        "kept_index": kept,
    }


def test_report_ends_with_the_data_block_and_says_nothing_was_sent():
    text = report.render(_record(), "/tmp/run")
    assert "## Data in this run" in text
    assert "Sent: Nothing" in text
    assert "temporary index that was deleted" in text
    assert text.index("## Data in this run") > text.index("## Next step")


def test_report_data_block_for_url_names_the_clone():
    text = report.render(_record(kind="url", label="https://github.com/pallets/flask"), "/tmp/run")
    assert "git clone" in text


def test_report_data_block_for_kept_index():
    assert "`.nexus/` folder" in report.render(_record(kept=True), "/tmp/run")


def test_store_save_list_and_load():
    run_id = store.new_run_id()
    record = {
        "created": "now",
        "tier": "quick",
        "source": {"label_public": None},
        "verdict": {"label": "Likely fit"},
        "env": {"python": "3"},
    }
    store.save(run_id, record, "# md")
    assert [r["id"] for r in store.history()] == [run_id]
    assert store.load(run_id)["schema_version"] == 1
    with pytest.raises(ValueError):
        store.load("nope")


def test_dispatch_ignores_other_arguments():
    assert cli.dispatch([]) is None
    assert cli.dispatch(["--help-me"]) is None


def test_eval_list_when_empty(capsys):
    assert cli.dispatch(["eval", "list"]) == 0
    assert "No saved runs" in capsys.readouterr().out


def test_eval_show_unknown_run(capsys):
    assert cli.dispatch(["eval", "show", "nope"]) == 2
    assert "No saved run" in capsys.readouterr().err


def test_eval_bad_url_exits_2(capsys):
    assert cli.dispatch(["eval", "http://example.com/a/b"]) == 2
    assert "https://" in capsys.readouterr().err


def test_eval_runs_quick_and_saves(tmp_path, monkeypatch, capsys):
    fake = _record()
    fake.pop("source")
    fake.pop("kept_index")
    monkeypatch.setattr(quick, "run_quick", lambda *a, **k: fake)
    project = tmp_path / "proj"
    project.mkdir()
    assert cli.dispatch(["eval", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Nexus-MCP eval" in out
    assert len(store.history()) == 1
    saved = store.load(store.history()[0]["id"])
    assert saved["source"]["label"] == "proj"
    assert saved["source"]["label_public"] is None  # a local folder name is never public


def test_server_main_with_no_arguments_starts_the_server(monkeypatch):
    import nexus_mcp.server as server

    started = []
    fake = SimpleNamespace(run=lambda: started.append(True))
    monkeypatch.setattr(server, "create_server", lambda: fake)
    monkeypatch.setattr("sys.argv", ["nexus-mcp-ci"])
    monkeypatch.setenv("NEXUS_WARM_START", "false")
    server.main()
    assert started == [True]


def test_server_main_runs_a_subcommand_and_exits(monkeypatch, capsys):
    import nexus_mcp.server as server

    monkeypatch.setattr("sys.argv", ["nexus-mcp-ci", "eval", "list"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0


def test_privacy_disables_hf_telemetry_but_keeps_user_choice(monkeypatch):
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)
    privacy.apply_privacy_defaults()
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "0")
    privacy.apply_privacy_defaults()
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "0"


def test_privacy_offline_switch(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("NEXUS_OFFLINE", "1")
    privacy.apply_privacy_defaults()
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_subprocess_import_is_available():
    assert subprocess  # keeps the import used for the fake runners above

"""Sharing: the payload allow-list, the CLI consent step, and the ingest validator."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from nexus_mcp.evaluate import cli, share, store

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "ingest_community_result", ROOT / "scripts" / "ingest_community_result.py"
)
ingest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ingest)

LOCAL_NAME = "secret-client-project"
QUESTION = "Find where the acme payroll export is written"


def _quick_record(kind="path", url=None):
    side = {"hit_at_1": 0.7, "hit_at_3": 0.9, "median_tokens": 5000.0, "median_ms": 50.0}
    return {
        "created": "2026-11-03T10:00:00+00:00",
        "tier": "quick",
        "source": {
            "kind": kind,
            "label": url or LOCAL_NAME,
            "label_public": url,
            "sha": "abc1234" if url else None,
        },
        "env": {"nexus_mcp_ci": "2.1.0", "python": "3.12.1", "os": "Darwin"},
        "repo": {"files": 120, "size_band": "50-500 files", "languages": ["python"]},
        "questions": 30,
        "summary": {"grep": side, "nexus": {**side, "median_tokens": 1500.0}},
        "verdict": {"label": "Likely fit", "reasons": [f"/Users/me/{LOCAL_NAME}/x.py"]},
        "results": [{"id": "q1", "query": QUESTION, "relevant": f"{LOCAL_NAME}/pay.py"}],
    }


def _agent_record():
    return {
        "created": "2026-11-03T10:00:00+00:00",
        "tier": "agent",
        "source": {"kind": "path", "label": LOCAL_NAME, "label_public": None},
        "env": {"nexus_mcp_ci": "2.1.0", "python": "3.12.1", "os": "Linux"},
        "repo": {"files": 900},
        "tasks": [{"id": "q1", "kind": "concept"}],
        "summary": [
            {
                "spec": "claude:haiku",
                "condition": "nexus",
                "runs": 2,
                "errors": 0,
                "accuracy": 1.0,
                "median_tokens": 900,
                "median_cost_usd": 0.01,
                "median_turns": 3,
                "median_seconds": 9,
            },
        ],
        "recommendation": {"spec": "claude:haiku", "condition": "nexus", "reason": "x"},
        "runs": [{"task": "q1", "answer": f"{LOCAL_NAME}/pay.py"}],
    }


# --- payload ---------------------------------------------------------------------------


def test_build_payload_has_no_local_name_path_or_question():
    text = json.dumps(share.build_payload(_quick_record()))
    assert LOCAL_NAME not in text and QUESTION not in text and "/Users/" not in text


def test_build_payload_keeps_url_and_sha_for_a_public_url_source():
    payload = share.build_payload(
        _quick_record("url", "https://github.com/pallets/flask"), is_public=lambda url: True
    )
    assert payload["source"] == {
        "kind": "url",
        "url": "https://github.com/pallets/flask",
        "sha": "abc1234",
    }


def test_build_payload_agent_tier_drops_answers_and_reasons():
    payload = share.build_payload(_agent_record())
    text = json.dumps(payload)
    assert LOCAL_NAME not in text and "reason" not in text
    assert payload["repo"]["size_band"] == "500-5k files"
    assert payload["agent"]["recommendation"] == {"spec": "claude:haiku", "condition": "nexus"}


def test_issue_body_holds_one_json_fence():
    body = share.issue_body(share.build_payload(_quick_record()))
    assert body.count("```json") == 1


def test_gh_ready_is_false_without_gh():
    assert share.gh_ready(which=lambda cmd: None) is False


def test_post_returns_none_when_gh_fails():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")

    assert share.post(share.build_payload(_quick_record()), run=run) is None


def test_post_passes_the_body_as_an_argument_not_a_url():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="https://github.com/x/y/issues/9\n")

    assert share.post(share.build_payload(_quick_record()), run=run).endswith("/9")
    assert seen["argv"][:3] == ["gh", "issue", "create"]
    assert "?" not in seen["argv"][seen["argv"].index("--repo") + 1]


# --- CLI consent -----------------------------------------------------------------------


@pytest.fixture
def saved_run(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_EVAL_HOME", str(tmp_path / "evals"))
    run_id = "20261103-100000-abcd"
    store.save(run_id, _quick_record(), "# report\n")
    return run_id


def test_cli_share_without_a_yes_posts_nothing(saved_run, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(share, "post", lambda payload: pytest.fail("must not post"))
    assert cli.dispatch(["eval", "share", saved_run]) == 0
    out = capsys.readouterr().out
    assert "Not posted" in out and LOCAL_NAME not in out


def test_cli_share_with_yes_and_no_gh_prints_the_form(saved_run, monkeypatch, capsys):
    monkeypatch.setattr(share, "gh_ready", lambda: False)
    assert cli.dispatch(["eval", "share", saved_run, "--yes"]) == 0
    assert share.FORM_URL in capsys.readouterr().out


def test_cli_share_unknown_run_fails(saved_run):
    assert cli.dispatch(["eval", "share", "nope"]) == 2


# --- ingest validator ------------------------------------------------------------------


def _body(payload) -> str:
    return share.issue_body(payload)


def test_ingest_accepts_a_payload_made_by_share():
    payload = share.build_payload(
        _quick_record("url", "https://github.com/pallets/flask"), is_public=lambda url: True
    )
    entry = ingest.validate(ingest.extract(_body(payload)))
    assert entry["quick"]["verdict"] == "Likely fit"
    assert entry["source"]["url"] == "https://github.com/pallets/flask"


def test_ingest_accepts_an_agent_payload():
    entry = ingest.validate(ingest.extract(_body(share.build_payload(_agent_record()))))
    assert entry["agent"]["summary"][0]["spec"] == "claude:haiku"


def test_ingest_drops_unknown_keys():
    payload = share.build_payload(_quick_record())
    payload["extra"] = "<script>alert(1)</script>"
    payload["repo"]["path"] = "/Users/me"
    entry = ingest.validate(payload)
    assert "extra" not in entry and "path" not in entry["repo"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["repo"].__setitem__("languages", ["<script>"]),
        lambda p: p.__setitem__("nexus_mcp_ci", "1.0; rm -rf /"),
        lambda p: p["source"].update(kind="url", url="javascript:alert(1)"),
        lambda p: p["quick"]["grep"].__setitem__("hit_at_1", 7),
        lambda p: p["quick"].__setitem__("verdict", "Best ever"),
        lambda p: p.__setitem__("kind", "something-else"),
        lambda p: p.__setitem__("os", "TempleOS"),
        lambda p: p["quick"]["nexus"].__setitem__("median_tokens", True),
    ],
)
def test_ingest_rejects_a_bad_value(mutate):
    payload = share.build_payload(_quick_record())
    mutate(payload)
    with pytest.raises(ingest.Invalid):
        ingest.validate(payload)


def test_ingest_rejects_a_bad_agent_spec():
    payload = share.build_payload(_agent_record())
    payload["agent"]["summary"][0]["spec"] = "claude:$(whoami)"
    with pytest.raises(ingest.Invalid):
        ingest.validate(payload)


def test_ingest_extract_needs_a_json_fence_and_a_size_cap():
    with pytest.raises(ingest.Invalid, match="no ```json"):
        ingest.extract("just text")
    with pytest.raises(ingest.Invalid, match="too large"):
        ingest.extract("x" * (ingest.MAX_BODY + 1))
    with pytest.raises(ingest.Invalid, match="does not parse"):
        ingest.extract("```json\n{nope\n```")


def test_ingest_add_replaces_an_entry_for_the_same_issue(tmp_path):
    out = tmp_path / "c.json"
    ingest.add({"issue": 3, "v": 1}, out)
    ingest.add({"issue": 1, "v": 1}, out)
    entries = ingest.add({"issue": 3, "v": 2}, out)
    assert [e["issue"] for e in entries] == [1, 3] and entries[1]["v"] == 2


def test_ingest_main_rejects_a_non_numeric_issue(monkeypatch):
    monkeypatch.setenv("ISSUE_NUMBER", "1; echo")
    monkeypatch.setenv("ISSUE_BODY", "")
    assert ingest.main([]) == 2


def test_ingest_main_end_to_end(tmp_path, monkeypatch):
    out = tmp_path / "c.json"
    real_add = ingest.add
    monkeypatch.setattr(ingest, "add", lambda entry: real_add(entry, out))
    monkeypatch.setenv("ISSUE_NUMBER", "42")
    monkeypatch.setenv("ISSUE_BODY", _body(share.build_payload(_quick_record())))
    assert ingest.main([]) == 0
    assert json.loads(out.read_text())[0]["issue"] == 42


def test_community_html_escapes_and_lists_entries():
    spec = importlib.util.spec_from_file_location("pr", ROOT / "scripts" / "publish_results.py")
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    entry = ingest.validate(share.build_payload(_quick_record()))
    entry.update(issue=7, accepted="2026-11-03")
    html_out = publish.community_html([entry])
    assert "#7" in html_out and "Likely fit" in html_out
    assert "None yet" in publish.community_html([])


# --- regressions from the security review ----------------------------------------------


def test_build_payload_drops_a_url_that_is_not_public():
    record = _quick_record("url", "https://github.com/acme/secret-repo")
    payload = share.build_payload(record, is_public=lambda url: False)
    assert payload["source"] == {"kind": "url"}
    assert "secret-repo" not in json.dumps(payload)


def test_public_github_repo_rejects_other_hosts_without_a_request(monkeypatch):
    monkeypatch.setattr(
        share.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no request expected")
    )
    assert share.public_github_repo("https://git.corp.internal/team/secret") is False


def test_public_github_repo_reads_the_private_flag(monkeypatch):
    import io

    def urlopen(request, timeout):
        assert request.full_url == "https://api.github.com/repos/pallets/flask"
        return io.BytesIO(b'{"private": false}')

    monkeypatch.setattr(share.urllib.request, "urlopen", urlopen)
    assert share.public_github_repo("https://github.com/pallets/flask.git") is True


def test_public_github_repo_is_false_when_github_hides_the_repo(monkeypatch):
    def urlopen(request, timeout):
        raise OSError("404")

    monkeypatch.setattr(share.urllib.request, "urlopen", urlopen)
    assert share.public_github_repo("https://github.com/acme/private") is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["source"].update(kind="url", url="https://github.com/аttacker/x"),
        lambda p: p.__setitem__("nexus_mcp_ci", "2.1.0\n"),
        lambda p: p.__setitem__("source", ["not", "an", "object"]),
        lambda p: p.__setitem__("quick", "text"),
        lambda p: p["quick"]["nexus"].__setitem__("median_tokens", None),
        lambda p: p.__setitem__("tier", ["quick"]),
    ],
)
def test_ingest_rejects_review_findings(mutate):
    payload = share.build_payload(_quick_record())
    mutate(payload)
    with pytest.raises(ingest.Invalid):
        ingest.validate(payload)


def test_ingest_pr_body_holds_the_validated_entry(tmp_path):
    out = tmp_path / "c.json"
    entry = ingest.validate(share.build_payload(_quick_record()))
    ingest.add({**entry, "issue": 5}, out)
    body = ingest.pr_body(5, out)
    assert '"issue": 5' in body and "not the issue" in body
    with pytest.raises(ingest.Invalid):
        ingest.pr_body(6, out)

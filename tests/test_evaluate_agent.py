"""The agent tier: specs, argv builders, output parsers, the cost gate and the summary.

No real CLI runs here. ``run_fn`` stands in for the agent process, which is the only
network boundary of this tier.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus_mcp.evaluate import agent, cli, doctor, runners, specs
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


# --- specs -----------------------------------------------------------------------------


def test_parse_specs_defaults_the_model_per_runner():
    parsed = specs.parse_specs("claude, codex,claude:haiku")
    assert [s.label for s in parsed] == ["claude:sonnet", "codex:gpt-5-codex", "claude:haiku"]


def test_parse_specs_drops_duplicates():
    assert len(specs.parse_specs("claude,claude:sonnet")) == 1


def test_parse_specs_rejects_an_unknown_runner():
    with pytest.raises(ValueError, match="Unknown runner"):
        specs.parse_specs("gemini")


def test_parse_conditions_rejects_an_unknown_condition():
    with pytest.raises(ValueError):
        specs.parse_conditions("baseline,magic")


def test_codex_cost_uses_cached_and_fresh_prices():
    usage = {"input_tokens": 1_000_000, "cached_input_tokens": 600_000, "output_tokens": 10_000}
    # 400k fresh * 1.25 + 600k cached * 0.125 + 10k out * 10, per million
    assert specs.codex_cost("gpt-5-codex", usage) == pytest.approx(0.5 + 0.075 + 0.1)


def test_codex_cost_is_none_for_an_unpriced_model():
    assert specs.codex_cost("mystery", {"input_tokens": 5}) is None


def test_prices_override_file_is_merged(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"mystery": {"input": 1, "cached_input": 0, "output": 0}}))
    monkeypatch.setenv("NEXUS_EVAL_PRICING", str(path))
    assert specs.codex_cost("mystery", {"input_tokens": 1_000_000}) == 1.0


def test_run_estimate_by_model_family():
    assert specs.run_estimate(specs.Spec("claude", "haiku")) < specs.run_estimate(
        specs.Spec("claude", "opus")
    )


# --- argv builders ---------------------------------------------------------------------


def test_claude_argv_baseline_has_no_mcp_server_and_no_bash():
    argv = runners.claude_argv("haiku", "baseline", "find x", None, bare=False)
    assert "--mcp-config" not in argv
    assert "--strict-mcp-config" in argv
    assert "Bash" in argv[argv.index("--disallowedTools") + 1]
    assert argv[-2:] == ["--", "find x"]
    assert argv[argv.index("--setting-sources") + 1] == ""


def test_claude_argv_nexus_adds_server_and_skill(tmp_path):
    argv = runners.claude_argv("sonnet", "nexus", "q", tmp_path / "m.json", bare=True)
    assert argv[2] == "--bare"
    assert argv[argv.index("--mcp-config") + 1] == str(tmp_path / "m.json")
    assert "nexus-mcp" in argv[argv.index("--append-system-prompt") + 1].lower()
    assert "--setting-sources" not in argv


def test_claude_argv_mcp_needs_a_config():
    with pytest.raises(ValueError):
        runners.claude_argv("sonnet", "mcp", "q", None, bare=False)


def test_claude_mcp_config_starts_the_server_on_the_prebuilt_index(tmp_path):
    server = runners.claude_mcp_config(tmp_path, python="/py")["mcpServers"]["nexus-mcp"]
    assert server["command"] == "/py" and server["args"] == ["-m", "nexus_mcp"]
    assert server["env"]["NEXUS_STORAGE_DIR"] == str(tmp_path)
    assert server["env"]["NEXUS_OFFLINE"] == "1"


def test_codex_argv_is_read_only_and_passes_the_server_as_config(tmp_path):
    argv = runners.codex_argv("gpt-5", "mcp", "q", tmp_path, tmp_path / "idx", python="/py")
    assert argv[:3] == ["codex", "exec", "--json"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    configs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert 'mcp_servers.nexus.command="/py"' in configs
    assert any("NEXUS_STORAGE_DIR" in c for c in configs)


def test_codex_argv_nexus_puts_the_skill_before_the_prompt(tmp_path):
    argv = runners.codex_argv("gpt-5", "nexus", "the question", tmp_path, tmp_path)
    assert argv[-1].endswith("the question") and len(argv[-1]) > len("the question")


def test_skill_text_has_no_frontmatter():
    text = runners.skill_text()
    assert text and not text.startswith("---")


# --- parsers ---------------------------------------------------------------------------

CODEX_LINES = [
    '{"type":"thread.started","thread_id":"t"}',
    '{"type":"item.completed","item":{"type":"mcp_tool_call","server":"nexus"}}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"pkg/mod_alpha.py\\nIt."}}',
    '{"type":"turn.completed","usage":{"input_tokens":2000,"cached_input_tokens":1000,'
    '"output_tokens":100}}',
]


def test_parse_codex_reads_answer_usage_and_cost():
    out = runners.parse_codex(CODEX_LINES, "gpt-5-codex")
    assert out["answer"].startswith("pkg/mod_alpha.py")
    assert out["tokens"] == 2100 and out["tool_calls"] == 1 and not out["is_error"]
    assert out["cost_usd"] > 0


def test_parse_codex_without_a_completed_turn_is_an_error():
    out = runners.parse_codex(CODEX_LINES[:3] + ["not json"], "gpt-5-codex")
    assert out["is_error"] and out["tokens"] is None


def test_parse_claude_reads_the_result_event():
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "result",
                "result": "pkg/mod_bravo.py",
                "total_cost_usd": 0.02,
                "num_turns": 3,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ),
    ]
    out = runners.parse_claude(lines)
    assert out["answer"] == "pkg/mod_bravo.py" and out["cost_usd"] == 0.02
    assert out["tokens"] == 15 and out["turns"] == 3 and not out["is_error"]


def test_run_reports_a_timeout_as_an_error(tmp_path):
    def fake(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 1)

    out = runners.run(specs.Spec("claude", "haiku"), "baseline", "q", tmp_path, tmp_path, fake)
    assert out["is_error"] and out["error"] == "TimeoutExpired"


def test_run_keeps_the_tool_calls_of_a_timed_out_run(tmp_path):
    started = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "a", "name": "mcp__nexus-mcp__index"}]
            },
        }
    )

    def fake(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 1, output=(started + "\n").encode())

    out = runners.run(specs.Spec("claude", "haiku"), "nexus", "q", tmp_path, tmp_path, fake)
    assert out["is_error"] and out["error"] == "TimeoutExpired"
    assert out["tools"] == {"mcp__nexus-mcp__index": 1}


def test_run_passes_the_project_as_cwd(tmp_path):
    seen = {}

    def fake(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runners.run(specs.Spec("claude", "haiku"), "nexus", "q", tmp_path, tmp_path, fake)
    assert seen["cwd"] == str(tmp_path)
    assert seen["env"]["ENABLE_TOOL_SEARCH"] == "false"


# --- scoring, summary, recommendation --------------------------------------------------


def test_answer_correct_accepts_relative_and_absolute_paths():
    assert agent.answer_correct("/abs/root/pkg/a.py\nok", "pkg/a.py")
    assert not agent.answer_correct("pkg/b.py", "pkg/a.py")
    assert not agent.answer_correct("", "pkg/a.py")


def _row(spec, condition, accuracy, cost):
    return {"spec": spec, "condition": condition, "accuracy": accuracy, "median_cost_usd": cost}


def test_recommend_picks_the_cheapest_setup_at_baseline_accuracy():
    rows = [
        _row("claude:sonnet", "baseline", 1.0, 0.20),
        _row("claude:sonnet", "nexus", 1.0, 0.10),
        _row("claude:haiku", "baseline", 0.5, 0.02),
        _row("claude:haiku", "nexus", 0.25, 0.01),
    ]
    rec = agent.recommend(rows)
    assert (rec["spec"], rec["condition"]) == ("claude:haiku", "baseline")


def test_recommend_reports_the_saving_against_the_same_model():
    rows = [_row("claude:sonnet", "baseline", 1.0, 0.20), _row("claude:sonnet", "nexus", 1.0, 0.1)]
    rec = agent.recommend(rows)
    assert rec["condition"] == "nexus" and "50% cheaper" in rec["reason"]


def test_recommend_with_no_costs_says_so():
    assert agent.recommend([_row("codex:x", "baseline", 1.0, None)])["spec"] is None


def test_estimate_usd_sums_per_run_estimates():
    plan = [{"spec": specs.Spec("claude", "haiku")}] * 3
    assert agent.estimate_usd(plan) == pytest.approx(3 * specs.RUN_ESTIMATE_USD["haiku"])


# --- run_agent end to end with a stub agent -------------------------------------------


def _stub_run(answer_right=True, cost=0.01):
    def run(spec, condition, prompt, root, storage):
        assert (storage / "index_metadata.json").exists()  # the shared index is built
        rel = "pkg/mod_alpha.py" if not answer_right else None
        for path in sorted(root.rglob("*.py")):
            name = path.stem.split("_", 1)[1]
            if name in prompt:
                rel = str(path.relative_to(root))
        return {
            "answer": rel or "",
            "tokens": 100,
            "cost_usd": cost,
            "turns": 2,
            "tool_calls": 1,
            "is_error": False,
            "seconds": 1.0,
            "isolation": "reduced",
        }

    return run


def _run_agent(project, **kwargs):
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_service:
        get_service.return_value = _mock_embedding_service()
        return agent.run_agent(project, **kwargs)


def test_run_agent_runs_every_spec_condition_and_task(project):
    result = _run_agent(
        project,
        specs_=specs.parse_specs("claude:haiku,claude:sonnet"),
        conditions=["baseline", "nexus"],
        tasks=2,
        run_fn=_stub_run(),
    )
    assert result["tier"] == "agent"
    assert len(result["runs"]) == 2 * 2 * len(result["tasks"])
    assert {r["spec"] for r in result["summary"]} == {"claude:haiku", "claude:sonnet"}
    assert all(r["accuracy"] == 1.0 for r in result["summary"])
    # Answers are kept, capped, in the local report so a wrong run can be read (2026-11-03:
    # a nexus run answered wrong and its answer was lost). Sharing drops them: see
    # test_build_payload_agent_tier_drops_answers_and_reasons.
    assert all(len(r["answer"]) <= 500 for r in result["runs"])
    assert not (project / ".nexus").exists()


def test_run_agent_stops_at_the_spend_cap(project):
    result = _run_agent(
        project,
        specs_=specs.parse_specs("claude:haiku"),
        conditions=["baseline", "nexus"],
        tasks=3,
        max_usd=0.015,
        concurrency=1,
        run_fn=_stub_run(cost=0.01),
    )
    assert result["skipped"] > 0
    assert result["spent_usd"] <= 0.02 + 1e-9


def test_run_agent_cancelled_sends_nothing(project):
    calls = []

    def run(*args):
        calls.append(args)

    with pytest.raises(RuntimeError, match="Cancelled"):
        _run_agent(
            project,
            specs_=specs.parse_specs("claude"),
            conditions=["baseline"],
            confirm=lambda plan: False,
            run_fn=run,
        )
    assert calls == []


# --- doctor and CLI --------------------------------------------------------------------


def test_doctor_checks_report_missing_clis():
    items = doctor.checks(which=lambda cmd: None)
    names = {name: ok for name, ok, _ in items}
    assert names["claude CLI"] is False and names["codex CLI"] is False
    assert "not found" in doctor.render(items)


def test_doctor_checks_read_the_cli_version():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="9.9.9 (Claude Code)\n", stderr="")

    items = doctor.checks(which=lambda cmd: f"/bin/{cmd}", run=run)
    claude = next(detail for name, _, detail in items if name == "claude CLI")
    assert claude.startswith("9.9.9")


def test_cli_agent_refuses_without_a_terminal_or_yes(project, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_service:
        get_service.return_value = _mock_embedding_service()
        code = cli.dispatch(["eval", str(project), "--agent", "claude:haiku", "--tasks", "1"])
    assert code == 1
    assert "--yes" in capsys.readouterr().err


def test_cli_agent_rejects_a_bad_spec(capsys):
    assert cli.dispatch(["eval", "--agent", "gemini"]) == 2


def test_cli_doctor_prints_checks(capsys):
    assert cli.dispatch(["eval", "doctor"]) == 0
    assert "eval doctor" in capsys.readouterr().out


def test_agent_choices_from_preset():
    args = cli._eval_parser().parse_args(["--preset", "efficient", "--agent", "claude:haiku"])
    chosen, conditions, tasks = cli._agent_choices(args)
    assert [s.label for s in chosen] == ["claude:haiku"]
    assert conditions == ["baseline", "mcp", "nexus"] and tasks == 6


def test_render_agent_names_the_providers_and_the_data_sent():
    from nexus_mcp.evaluate import report

    record = {
        "source": {"label": "proj"},
        "repo": {"files": 3},
        "tasks": [{"id": "q1"}],
        "specs": ["claude:haiku", "codex:gpt-5"],
        "spent_usd": 0.1,
        "estimate_usd": 0.2,
        "max_usd": 5,
        "summary": [
            {
                "spec": "claude:haiku",
                "condition": "nexus",
                "accuracy": 1.0,
                "median_tokens": 10,
                "median_cost_usd": 0.01,
                "median_turns": 2,
                "median_seconds": 3,
                "errors": 0,
            }
        ],
        "recommendation": {"spec": "claude:haiku", "condition": "nexus", "reason": "Cheap."},
    }
    md = report.render_agent(record, "/tmp/x")
    assert "Anthropic and OpenAI" in md and "claude:haiku" in md and "$0.010" in md


def test_benchmarks_transcript_shim_still_exports_the_parser():
    from benchmarks import transcript as shim

    assert shim.parse_lines is runners.transcript.parse_lines
    assert Path(shim.__file__).name == "transcript.py"


# --- regressions from the first paid run (an expired CLI login) ------------------------

# The shape of the real `claude -p` output when the login has expired (2026-11-03).
AUTH_FAIL_LINES = [
    json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}),
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": "<synthetic>",
                "content": [
                    {
                        "type": "text",
                        "text": "Failed to authenticate: OAuth session expired and could "
                        "not be refreshed",
                    }
                ],
            },
        }
    ),
    json.dumps(
        {"type": "result", "is_error": True, "total_cost_usd": 0, "num_turns": 1, "usage": {}}
    ),
]


def test_parse_claude_reports_the_login_error_text():
    out = runners.parse_claude(AUTH_FAIL_LINES)
    assert out["is_error"] and out["error"].startswith("Failed to authenticate")
    assert out["cost_usd"] == 0


def _failing_run(spec, condition, prompt, root, storage):
    return {**runners.parse_claude(AUTH_FAIL_LINES), "seconds": 1.0, "isolation": "reduced"}


def test_run_agent_stops_early_and_reports_the_error_when_every_run_fails(project):
    result = _run_agent(
        project,
        specs_=specs.parse_specs("claude:haiku"),
        conditions=["baseline", "nexus"],
        tasks=3,
        concurrency=1,
        run_fn=_failing_run,
    )
    assert result["error"].startswith("Failed to authenticate")
    assert result["failed"] == agent.FAIL_FAST_AFTER
    assert result["skipped"] == len(result["runs"]) - agent.FAIL_FAST_AFTER
    assert result["spent_usd"] == 0  # a zero cost is not replaced by the estimate
    assert result["recommendation"]["spec"] is None


def test_render_agent_shows_the_cli_error(project):
    from nexus_mcp.evaluate import report

    result = _run_agent(
        project,
        specs_=specs.parse_specs("claude:haiku"),
        conditions=["baseline"],
        tasks=2,
        concurrency=1,
        run_fn=_failing_run,
    )
    md = report.render_agent({**result, "source": {"label": "proj"}}, "/tmp/x")
    assert "Every run failed" in md and "OAuth session expired" in md


def test_recommend_skips_a_setup_whose_runs_all_failed():
    rows = [
        {**_row("claude:haiku", "baseline", 0.0, 0.0), "runs": 2, "errors": 2},
        {**_row("claude:haiku", "nexus", 1.0, 0.02), "runs": 2, "errors": 0},
    ]
    assert agent.recommend(rows)["condition"] == "nexus"


# --- which tools a run used -------------------------------------------------------------


def test_parse_claude_records_tool_names():
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "a", "name": "mcp__nexus-mcp__search"},
                        {"type": "tool_use", "id": "b", "name": "Read"},
                    ]
                },
            }
        ),
        json.dumps({"type": "result", "result": "x", "total_cost_usd": 0.01, "usage": {}}),
    ]
    assert runners.parse_claude(lines)["tools"] == {"mcp__nexus-mcp__search": 1, "Read": 1}


def test_parse_codex_records_tool_kinds():
    assert runners.parse_codex(CODEX_LINES, "gpt-5-codex")["tools"] == {"mcp_tool_call": 1}


def test_uses_nexus_detects_claude_and_codex_mcp_calls():
    assert agent.uses_nexus({"tools": {"mcp__nexus-mcp__find_symbol": 2}})
    assert agent.uses_nexus({"tools": {"mcp_tool_call": 1}})
    assert not agent.uses_nexus({"tools": {"Grep": 3, "Read": 1}})
    assert not agent.uses_nexus({})


def test_render_agent_flags_a_nexus_row_that_never_called_nexus():
    from nexus_mcp.evaluate import report

    row = {
        "spec": "claude:haiku",
        "median_tokens": 10,
        "median_cost_usd": 0.01,
        "median_turns": 2,
        "median_seconds": 3,
        "errors": 0,
        "accuracy": 1.0,
        "runs": 2,
    }
    record = {
        "source": {"label": "p"},
        "repo": {"files": 1},
        "tasks": [{}],
        "specs": ["claude:haiku"],
        "spent_usd": 0.1,
        "estimate_usd": 0.1,
        "max_usd": 5,
        "summary": [
            {**row, "condition": "baseline"},
            {**row, "condition": "nexus", "used_nexus": 0},
        ],
        "recommendation": {"spec": None, "reason": "x"},
    }
    md = report.render_agent(record, "/tmp/x")
    assert "| 0/2 |" in md and "called a Nexus-MCP tool" in md

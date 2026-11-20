"""The plugin hook and the `check` skill (ADR-027): a hook must be silent, safe and pinned."""

import json
import re
from pathlib import Path

from nexus_mcp.evaluate import cli

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "plugin" / "hooks" / "hooks.json"


def _command() -> str:
    data = json.loads(HOOKS.read_text())
    (group,) = data["hooks"]["Stop"]
    (hook,) = group["hooks"]
    assert hook["type"] == "command"
    assert hook["timeout"] <= 60
    return hook["command"]


def test_plugin_hooks_has_one_stop_hook_and_no_hook_on_a_tool_call():
    assert set(json.loads(HOOKS.read_text())["hooks"]) == {"Stop"}


def test_plugin_hook_command_runs_check_hook_and_never_blocks_on_failure():
    command = _command()
    assert " check --hook" in command
    # An older pinned release has no --hook flag: exit code 2 would block the stop.
    assert command.endswith("|| true")
    assert "2>/dev/null" in command
    assert "--python 3.12" in command


def test_plugin_hook_pin_matches_the_package_version():
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert f"nexus-mcp-ci=={version.group(1)}" in _command()


def test_check_parser_accepts_the_hook_flag():
    assert cli._check_parser().parse_args(["--hook"]).hook is True


def test_check_skill_is_started_by_the_user_only():
    text = (ROOT / "plugin" / "skills" / "check" / "SKILL.md").read_text()
    head = text.split("---")[1]
    assert "name: check" in head
    assert "disable-model-invocation: true" in head
    assert "check . --format json" in text

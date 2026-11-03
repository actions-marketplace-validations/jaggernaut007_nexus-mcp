"""``nexus-mcp-ci eval share <id>``: post the numbers of a saved run, only after a yes.

The payload is built from an allow-list. It never holds a path, code, question text or the
name of a local project. A repo URL and its commit are sent only for a URL source on
github.com that GitHub serves without a login (one unauthenticated API read checks this).
The user sees the exact payload first. With a logged-in ``gh`` it opens a GitHub issue;
otherwise it prints the issue-form link and the payload to paste. No data goes in a URL.
"""

import json
import re
import shutil
import subprocess
import urllib.request
from typing import Any, Callable, Dict, Optional

from nexus_mcp.evaluate import SCHEMA_VERSION
from nexus_mcp.evaluate.quick import size_band

REPO = "jaggernaut007/Nexus-MCP"
FORM_URL = f"https://github.com/{REPO}/issues/new?template=eval-result.yml"
PAYLOAD_KIND = "nexus-eval-share"
GITHUB_REPO = re.compile(r"https://github\.com/([\w.-]{1,40})/([\w.-]{1,100})", re.ASCII)
QUICK_STATS = ("hit_at_1", "hit_at_3", "median_tokens", "median_ms")
AGENT_STATS = (
    "spec",
    "condition",
    "runs",
    "errors",
    "accuracy",
    "median_tokens",
    "median_cost_usd",
    "median_turns",
    "median_seconds",
)


def _pick(obj: Dict[str, Any], keys) -> Dict[str, Any]:
    return {k: obj[k] for k in keys if k in obj}


def public_github_repo(url: str, timeout: float = 10) -> bool:
    """True only for a github.com repo that GitHub serves without a login.

    A private repo (cloned with your own git credentials) or another host is never named
    in a public issue. This is one unauthenticated read of api.github.com.
    """
    match = GITHUB_REPO.fullmatch(url.rstrip("/").removesuffix(".git"))
    if not match:
        return False
    request = urllib.request.Request(
        f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "nexus-mcp-ci"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get("private") is False
    except (OSError, ValueError):
        return False


def build_payload(
    record: Dict[str, Any], is_public: Callable[[str], bool] = public_github_repo
) -> Dict[str, Any]:
    """The allow-listed numbers of a saved run. Nothing else is copied from the record."""
    env = record.get("env", {})
    src = record.get("source", {})
    repo = record.get("repo", {})
    files = int(repo.get("files", 0) or 0)
    source: Dict[str, Any] = {"kind": src.get("kind")}
    url = src.get("label_public")
    if src.get("kind") == "url" and url and is_public(url):
        source["url"] = url.rstrip("/").removesuffix(".git")
        source["sha"] = src.get("sha")
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PAYLOAD_KIND,
        "nexus_mcp_ci": env.get("nexus_mcp_ci"),
        "python": env.get("python"),
        "os": env.get("os"),
        "tier": record.get("tier"),
        "created": (record.get("created") or "")[:10],
        "source": source,
        "repo": {
            "size_band": repo.get("size_band") or size_band(files),
            "languages": list(repo.get("languages", []))[:10],
        },
    }
    if record.get("tier") == "quick":
        summary = record.get("summary", {})
        payload["quick"] = {
            "questions": record.get("questions"),
            "verdict": record.get("verdict", {}).get("label"),
            "grep": _pick(summary.get("grep", {}), QUICK_STATS),
            "nexus": _pick(summary.get("nexus", {}), QUICK_STATS),
        }
    elif record.get("tier") == "agent":
        rec = record.get("recommendation", {})
        payload["agent"] = {
            "tasks": len(record.get("tasks", [])),
            "summary": [_pick(row, AGENT_STATS) for row in record.get("summary", [])],
            "recommendation": {"spec": rec.get("spec"), "condition": rec.get("condition")},
        }
    return payload


def issue_title(payload: Dict[str, Any]) -> str:
    verdict = (payload.get("quick") or {}).get("verdict")
    rec = (payload.get("agent") or {}).get("recommendation") or {}
    detail = verdict or (f"best: {rec.get('spec')} {rec.get('condition')}" if rec else "")
    return f"Eval result: {payload['tier']} tier, {payload['repo']['size_band']}, {detail}".strip()


def issue_body(payload: Dict[str, Any]) -> str:
    return (
        "Shared with `nexus-mcp-ci eval share`. The payload below is the whole submission.\n\n"
        "```json\n" + json.dumps(payload, indent=2) + "\n```\n"
    )


def gh_ready(which=shutil.which, run=subprocess.run) -> bool:
    if not which("gh"):
        return False
    try:
        proc = run(["gh", "auth", "status"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def post(payload: Dict[str, Any], run=subprocess.run) -> Optional[str]:
    """Open the issue with ``gh``. Returns the issue URL, or None when gh failed."""
    proc = run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            REPO,
            "--title",
            issue_title(payload),
            "--body",
            issue_body(payload),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""

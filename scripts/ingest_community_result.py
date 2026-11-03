"""Validate one shared eval result from a GitHub issue and add it to the community data.

Run by .github/workflows/community-results.yml after a maintainer adds the `eval-accepted`
label. The issue body arrives in the ISSUE_BODY environment variable, never on a command
line. Nothing in the payload is executed. Every kept value is checked against an
allow-list and rebuilt; any other key is dropped, and a bad value stops the run.

Run: ISSUE_BODY=... ISSUE_NUMBER=12 python scripts/ingest_community_result.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data" / "community-results.json"
MAX_BODY = 20_000
FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.S)

SIZE_BANDS = ("<50 files", "50-500 files", "500-5k files", ">5k files")
VERDICTS = ("Strong fit", "Likely fit", "Weak fit")
CONDITIONS = ("baseline", "mcp", "nexus")
VERSION = re.compile(r"[0-9A-Za-z.+-]{1,20}", re.ASCII)
PYVER = re.compile(r"[0-9.]{1,10}", re.ASCII)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
LANG = re.compile(r"[a-z0-9_+#-]{1,20}", re.ASCII)
GITHUB_URL = re.compile(r"https://github\.com/[\w.-]{1,40}/[\w.-]{1,100}", re.ASCII)
SHA = re.compile(r"[0-9a-f]{7,40}", re.ASCII)
SPEC = re.compile(r"(claude|codex):[\w.-]{1,40}", re.ASCII)
ISSUE = re.compile(r"[0-9]{1,9}", re.ASCII)


class Invalid(ValueError):
    pass


def _str(value: Any, pattern: re.Pattern, name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Invalid(f"bad {name}")
    return value


def _choice(value: Any, options, name: str) -> str:
    if not isinstance(value, str) or value not in options:
        raise Invalid(f"bad {name}")
    return value


def _obj(value: Any, name: str) -> Dict[str, Any]:
    """A nested object: missing is empty, any other type is invalid."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Invalid(f"bad {name}")
    return value


def _num(value: Any, name: str, low: float = 0, high: float = 1e9) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise Invalid(f"bad {name}")
    return value


def _required(value: Any, name: str, high: float) -> float:
    if value is None:
        raise Invalid(f"missing {name}")
    return _num(value, name, 0, high)


def _quick_side(side: Any, name: str) -> Dict[str, Any]:
    if not isinstance(side, dict):
        raise Invalid(f"bad {name}")
    return {
        "hit_at_1": _num(side.get("hit_at_1"), f"{name}.hit_at_1", 0, 1),
        "hit_at_3": _num(side.get("hit_at_3"), f"{name}.hit_at_3", 0, 1),
        # Required: the community table prints it.
        "median_tokens": _required(side.get("median_tokens"), f"{name}.median_tokens", 1e7),
        "median_ms": _num(side.get("median_ms"), f"{name}.median_ms", 0, 1e7),
    }


def _agent_row(row: Any) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise Invalid("bad agent row")
    return {
        "spec": _str(row.get("spec"), SPEC, "spec"),
        "condition": _choice(row.get("condition"), CONDITIONS, "condition"),
        "runs": _num(row.get("runs"), "runs", 0, 1000),
        "errors": _num(row.get("errors"), "errors", 0, 1000),
        "accuracy": _num(row.get("accuracy"), "accuracy", 0, 1),
        "median_tokens": _num(row.get("median_tokens"), "median_tokens", 0, 1e8),
        "median_cost_usd": _num(row.get("median_cost_usd"), "median_cost_usd", 0, 100),
        "median_turns": _num(row.get("median_turns"), "median_turns", 0, 1000),
        "median_seconds": _num(row.get("median_seconds"), "median_seconds", 0, 1e5),
    }


def validate(raw: Any) -> Dict[str, Any]:
    """Return a rebuilt payload with only allow-listed, checked values. Raises Invalid."""
    if not isinstance(raw, dict):
        raise Invalid("payload is not an object")
    if raw.get("kind") != "nexus-eval-share" or raw.get("schema_version") != 1:
        raise Invalid("not a nexus-eval-share payload, schema 1")
    tier = _choice(raw.get("tier"), ("quick", "agent"), "tier")
    src = _obj(raw.get("source"), "source")
    source = {"kind": _choice(src.get("kind"), ("path", "url", "example"), "source.kind")}
    if source["kind"] == "url" and src.get("url"):
        source["url"] = _str(src.get("url"), GITHUB_URL, "source.url")
        if src.get("sha"):
            source["sha"] = _str(src.get("sha"), SHA, "source.sha")
    repo = _obj(raw.get("repo"), "repo")
    languages = repo.get("languages") or []
    if not isinstance(languages, list) or len(languages) > 10:
        raise Invalid("bad languages")
    out: Dict[str, Any] = {
        "nexus_mcp_ci": _str(raw.get("nexus_mcp_ci"), VERSION, "nexus_mcp_ci"),
        "python": _str(raw.get("python"), PYVER, "python"),
        "os": _choice(raw.get("os"), ("Darwin", "Linux", "Windows"), "os"),
        "created": _str(raw.get("created"), DATE, "created"),
        "tier": tier,
        "source": source,
        "repo": {
            "size_band": _choice(repo.get("size_band"), SIZE_BANDS, "size_band"),
            "languages": [_str(lang, LANG, "language") for lang in languages],
        },
    }
    if tier == "quick":
        quick = _obj(raw.get("quick"), "quick")
        out["quick"] = {
            "questions": _num(quick.get("questions"), "questions", 1, 500),
            "verdict": _choice(quick.get("verdict"), VERDICTS, "verdict"),
            "grep": _quick_side(quick.get("grep"), "grep"),
            "nexus": _quick_side(quick.get("nexus"), "nexus"),
        }
    else:
        agent = _obj(raw.get("agent"), "agent")
        rows = agent.get("summary") or []
        if not isinstance(rows, list) or not 0 < len(rows) <= 20:
            raise Invalid("bad agent summary")
        rec = _obj(agent.get("recommendation"), "recommendation")
        out["agent"] = {
            "tasks": _num(agent.get("tasks"), "tasks", 1, 100),
            "summary": [_agent_row(row) for row in rows],
            "recommendation": {
                "spec": _str(rec["spec"], SPEC, "spec") if rec.get("spec") else None,
                "condition": _choice(rec["condition"], CONDITIONS, "condition")
                if rec.get("condition")
                else None,
            },
        }
    return out


def extract(body: str) -> Any:
    if len(body) > MAX_BODY:
        raise Invalid("issue body is too large")
    match = FENCE.search(body)
    if not match:
        raise Invalid("no ```json block in the issue body")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise Invalid(f"the json block does not parse: {exc.msg}") from None


def add(entry: Dict[str, Any], path: Path = OUT) -> List[Dict[str, Any]]:
    entries = json.loads(path.read_text()) if path.exists() else []
    entries = [e for e in entries if e.get("issue") != entry.get("issue")]
    entries.append(entry)
    entries.sort(key=lambda e: e.get("issue") or 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n")
    return entries


def pr_body(number: int, path: Path = OUT) -> str:
    """The PR description: the validated entry, so the maintainer reviews exactly this data."""
    entries = json.loads(path.read_text()) if path.exists() else []
    entry = next((e for e in entries if e.get("issue") == number), None)
    if entry is None:
        raise Invalid(f"no entry for issue {number}")
    return (
        f"Validated payload from #{number}, added by `community-results.yml`.\n\n"
        "Review this data, not the issue: the issue can change after the label.\n\n"
        "```json\n" + json.dumps(entry, indent=2) + "\n```\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--show"] and len(argv) == 2 and ISSUE.fullmatch(argv[1]):
        print(pr_body(int(argv[1])))
        return 0
    body = os.environ.get("ISSUE_BODY", "")
    number = os.environ.get("ISSUE_NUMBER", "")
    if not ISSUE.fullmatch(number):
        print("ISSUE_NUMBER must be a number", file=sys.stderr)
        return 2
    try:
        entry = validate(extract(body))
    except Invalid as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1
    entry["issue"] = int(number)
    entry["accepted"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = len(add(entry))
    print(f"Added issue {number}. Community results: {total}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

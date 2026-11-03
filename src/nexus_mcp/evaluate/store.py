"""Save eval runs under ``~/.nexus-mcp/evals/<run-id>/`` and list them again."""

import json
import platform
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from nexus_mcp.evaluate import SCHEMA_VERSION, paths


def new_run_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def environment() -> Dict[str, Any]:
    try:
        from importlib.metadata import version

        pkg = version("nexus-mcp-ci")
    except Exception:  # noqa: BLE001 - a source checkout has no installed metadata
        pkg = "unknown"
    return {"nexus_mcp_ci": pkg, "python": sys.version.split()[0], "os": platform.system()}


def save(run_id: str, record: Dict[str, Any], markdown: str) -> Path:
    """Write report.json, report.md and env.json, and add a line to history.jsonl."""
    run_dir = paths.eval_home() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    full = {"schema_version": SCHEMA_VERSION, "id": run_id, **record}
    (run_dir / "report.json").write_text(json.dumps(full, indent=2))
    (run_dir / "report.md").write_text(markdown)
    (run_dir / "env.json").write_text(json.dumps(record.get("env", {}), indent=2))
    line = {
        "id": run_id,
        "created": record.get("created"),
        "tier": record.get("tier"),
        "source": record.get("source", {}).get("label_public"),
        "verdict": record.get("verdict", {}).get("label"),
    }
    with open(paths.eval_home() / "history.jsonl", "a") as handle:
        handle.write(json.dumps(line) + "\n")
    return run_dir


def history() -> List[Dict[str, Any]]:
    path = paths.eval_home() / "history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load(run_id: str) -> Dict[str, Any]:
    path = paths.eval_home() / run_id / "report.json"
    if not path.exists():
        raise ValueError(f"No saved run '{run_id}'. List runs with: nexus-mcp-ci eval list")
    return json.loads(path.read_text())

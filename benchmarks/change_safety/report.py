"""Report of recorded change-safety runs: python -m benchmarks.change_safety.report <runs.jsonl>

One row for each condition: how often the change was safe, how many sites and tests it left
behind, and the median cost, wall time and turns. A second table gives the same for each
task, so a task where Nexus-MCP does not help shows. Nothing is hidden or dropped.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from benchmarks.change_safety import score


def load(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _median(rows: List[Dict[str, Any]], key: str):
    values = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(values) if values else None


def group_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    stats = score.summarize(rows)
    stats["median_cost_usd"] = _median(rows, "total_cost_usd")
    stats["median_wall_s"] = _median(rows, "wall_seconds")
    stats["median_turns"] = _median(rows, "num_turns")
    return stats


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def render(rows: List[Dict[str, Any]]) -> str:
    out = ["# Change-safety benchmark", ""]
    columns = ("runs", "safe_rate", "missed_site_rate", "tests_failed",
               "median_cost_usd", "median_wall_s", "median_turns")
    for title, key in (("By condition", "condition"), ("By task and condition", None)):
        out += [f"## {title}", "", "| Group | " + " | ".join(columns) + " |",
                "|---|" + "---|" * len(columns)]
        groups = defaultdict(list)
        for row in rows:
            name = row["condition"] if key else f"{row['task_id']} / {row['condition']}"
            groups[name].append(row)
        for name in sorted(groups):
            stats = group_stats(groups[name])
            out.append(f"| {name} | " + " | ".join(_cell(stats[c]) for c in columns) + " |")
        out.append("")
    return "\n".join(out)


def main(argv: List[str]) -> int:
    if len(argv) != 1:
        print("Usage: python -m benchmarks.change_safety.report <runs.jsonl>", file=sys.stderr)
        return 2
    print(render(load(Path(argv[0]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

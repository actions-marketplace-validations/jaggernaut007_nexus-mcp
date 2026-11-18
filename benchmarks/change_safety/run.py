"""Live runner of the change-safety benchmark. It costs money, so it is a dry run by default.

    python -m benchmarks.change_safety.run                       # prints the plan, spends nothing
    python -m benchmarks.change_safety.run --smoke --go          # 1 task x 1 rep, both conditions
    python -m benchmarks.change_safety.run --reps 3 --go         # the full pilot set

Each run: a fresh copy of the project, the agent edits it (Read, Grep, Glob, Edit, Write, and
Bash for the test command only), then `score.score_project` adds the hidden tests, runs them
and checks the sites. Conditions are those of `benchmarks/conditions.py`; the `nexus` condition
gets an index of the copy before the run, outside the measured time. Records go to a JSONL
file; a finished run is skipped when the same command runs again.
"""

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks import conditions as cond
from benchmarks import runner
from benchmarks import transcript as tx
from benchmarks.change_safety import score

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
DEFAULT_OUT = RESULTS_DIR / "change-safety.jsonl"
CHANGE_TOOLS = "Read,Grep,Glob,Edit,Write,Bash"
CHANGE_DISALLOWED = "NotebookEdit,WebFetch,WebSearch,Task"
DEFAULT_BUDGET_USD = 1.50
DEFAULT_TIMEOUT_S = 600
SMOKE_TASKS = 1


def bash_pattern(test_command: str) -> str:
    """The `--allowedTools` rule that lets the agent run its test command and nothing else."""
    words = shlex.split(test_command)
    head = words[:3] if words[:2] in (["python", "-m"], ["python3", "-m"]) else words[:2]
    return f"Bash({' '.join(head)}:*)"


def allowed_tools(task: Dict[str, Any]) -> str:
    return ",".join([
        "Read", "Grep", "Glob", "Edit", "Write", "ToolSearch", bash_pattern(task["test_command"]),
        "mcp__nexus-mcp__*", "mcp__plugin_nexus-mcp_nexus-mcp__*",
    ])


def task_prompt(task: Dict[str, Any]) -> str:
    return (
        task["prompt"].strip()
        + "\n\nMake the change by editing the files in the current folder. Run "
        + f"`{task['test_command']}` before you finish. End with one sentence that says "
        + "what you changed."
    )


def build_command(
    task: Dict[str, Any], condition: str, model: str, config_dir: Path,
    effort: Optional[str] = None, budget: float = DEFAULT_BUDGET_USD,
) -> Dict[str, Any]:
    """The (argv, env, isolation_mode) of one run. Pure: nothing is started."""
    return cond.build_run(
        condition, task_prompt(task), model, budget, config_dir,
        builtin_tools=CHANGE_TOOLS, effort=effort,
        allowed_tools=allowed_tools(task), disallowed_tools=CHANGE_DISALLOWED,
    )


def index_copy(project: Path) -> None:
    """Index the copy before a `nexus` run, so the agent does not pay for it."""
    env = {**os.environ, "NEXUS_EMBEDDING_DEVICE": cond.BENCH_EMBEDDING_DEVICE,
           "NEXUS_AUTO_WATCH": "false"}
    env.pop("NEXUS_STORAGE_DIR", None)
    subprocess.run(
        [sys.executable, "-c", "from nexus_mcp.server import main; main()", "index", str(project)],
        env=env, check=True, capture_output=True, timeout=600,
    )


def run_once(
    task: Dict[str, Any], condition: str, rep: int, model: str, config_dir: Path,
    effort: Optional[str], budget: float, timeout_s: int, version: str,
) -> Dict[str, Any]:
    """One agent run on a fresh copy of the project, scored. Raises on a usage limit."""
    base = cond.split_condition(condition)[0]
    work = Path(tempfile.mkdtemp(prefix=f"nexus-change-{task['id']}-"))
    try:
        score.materialize(task, work)
        if base != "baseline":
            index_copy(work)
        built = build_command(task, condition, model, config_dir, effort, budget)
        started = time.time()
        proc = subprocess.Popen(
            built["argv"], cwd=str(work), env=built["env"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, _ = proc.communicate()
        wall_s = time.time() - started
        trace = tx.parse_lines(stdout.splitlines())
        if trace.is_error and any(
            m in (trace.final_answer or "").lower() for m in runner.USAGE_LIMIT_MARKERS
        ):
            raise runner.UsageLimitReached(trace.final_answer)
        shutil.rmtree(work / ".nexus", ignore_errors=True)
        result = score.score_project(work, task)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        **result,
        "kind": task["kind"], "language": task["language"], "condition": condition,
        "rep": rep, "model": model, "effort": effort, "claude_version": version,
        "isolation_mode": built["isolation_mode"], "timed_out": timed_out,
        "wall_seconds": round(wall_s, 2), "tool_call_counts": trace.tool_call_counts,
        "total_tokens": trace.total_tokens, "fresh_tokens": trace.fresh_tokens,
        "total_cost_usd": trace.total_cost_usd, "num_turns": trace.num_turns,
        "is_error": trace.is_error, "final_answer": trace.final_answer,
    }


def plan(tasks: List[Dict[str, Any]], conditions: List[str], reps: int) -> List[tuple]:
    return runner.run_order(tasks, conditions, reps)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--tasks", help="Comma-separated task ids (default: all)")
    p.add_argument("--conditions", default="baseline,nexus")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--model", default=runner.DEFAULT_MODEL)
    p.add_argument("--effort", default=cond.DEFAULT_EFFORT)
    p.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD, help="Max USD per run")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--smoke", action="store_true", help="One task, one rep")
    p.add_argument("--go", action="store_true", help="Spend money: really run the plan")
    args = p.parse_args(argv)

    tasks = score.load_tasks()
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    if args.smoke:
        tasks, args.reps = tasks[:SMOKE_TASKS], 1
    conditions = args.conditions.split(",")
    for name in conditions:
        if cond.split_condition(name)[0] not in cond.KNOWN_CONDITIONS:
            raise SystemExit(f"Unknown condition {name!r}")
    runs = plan(tasks, conditions, args.reps)
    done = runner.done_keys(args.out, args.model, args.effort)
    todo = [(t, c, r) for t, c, r in runs if (t["id"], c, r) not in done]
    print(f"{len(todo)} runs to do ({len(runs) - len(todo)} already done), "
          f"at most ${len(todo) * args.budget:.2f} (budget {args.budget} each).")
    for task, name, rep in todo:
        print(f"  {task['id']:28} {name:10} rep {rep}")
    if not args.go:
        print("Dry run. Add --go to spend money.")
        return 0

    config_dir = runner.BENCH_DIR / ".claude-bench"
    runner.require_login(config_dir)
    version = runner.claude_version()
    for task, name, rep in todo:
        try:
            record = run_once(
                task, name, rep, args.model, config_dir, args.effort, args.budget,
                args.timeout, version,
            )
        except runner.UsageLimitReached:
            print("Usage limit reached. Run the same command later.", file=sys.stderr)
            return 3
        runner.write_record(record, args.out)
        print(f"{task['id']} {name} rep {rep}: safe={record['safe']} "
              f"missed={record['missed_sites']}/{record['sites_total']} "
              f"failed={record['tests_failed']} ${record['total_cost_usd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

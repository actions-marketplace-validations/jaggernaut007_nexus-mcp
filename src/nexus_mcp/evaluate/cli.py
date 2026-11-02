"""Command line for ``nexus-mcp-ci eval`` and ``nexus-mcp-ci data``."""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from nexus_mcp.evaluate import data_cmd, paths, report, source, store
from nexus_mcp.privacy import apply_privacy_defaults

SUBCOMMANDS = ("eval", "data")


def _eval_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci eval",
        description=(
            "Measure Nexus-MCP against Grep and Read on questions made from your own code. "
            "Free, offline, no LLM. Nothing leaves your machine."
        ),
        epilog="Saved runs: `eval list`, `eval show <id>`. Your data: `nexus-mcp-ci data where`.",
    )
    p.add_argument(
        "source",
        nargs="?",
        default=None,
        help="A folder (default: current folder) or a public https:// repo URL, "
        "or `list` / `show <id>`",
    )
    p.add_argument("run_id", nargs="?", default=None, help=argparse.SUPPRESS)
    p.add_argument("--example", action="store_true", help="Use the bundled example project")
    p.add_argument("--questions", type=int, default=30, help="Number of questions (default 30)")
    p.add_argument("--seed", type=int, default=7, help="Seed for the question sample (default 7)")
    p.add_argument(
        "--keep-index",
        action="store_true",
        help="Keep the index in <project>/.nexus so adopting Nexus-MCP needs no re-index",
    )
    p.add_argument(
        "--out", type=Path, default=None, help="Also copy report.md and report.json here"
    )
    p.add_argument("--json", action="store_true", help="Print the result as JSON")
    return p


def _data_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci data",
        description="Show, export and delete the data that Nexus-MCP stores on this machine.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    where = sub.add_parser("where", help="List every place data is kept, with sizes")
    where.add_argument("path", nargs="?", default=".")
    clear = sub.add_parser("clear", help="Delete stored data (dry run unless --yes)")
    clear.add_argument("path", nargs="?", default=".")
    for target in data_cmd.TARGETS:
        clear.add_argument(f"--{target}", action="store_true", help=f"Delete the {target}")
    clear.add_argument("--all", action="store_true", help="All of the above")
    clear.add_argument(
        "--yes", action="store_true", help="Really delete (the default is a dry run)"
    )
    export = sub.add_parser("export-memories", help="Write memories to a JSON file")
    export.add_argument("path", nargs="?", default=".")
    export.add_argument("--out", type=Path, default=Path("nexus-memories.json"))
    return p


def run_eval(argv: List[str]) -> int:
    args = _eval_parser().parse_args(argv)
    if args.source == "list":
        rows = store.history()
        for row in rows:
            print(f"{row['id']}  {row.get('verdict') or '-':<11} {row.get('source') or '-'}")
        if not rows:
            print("No saved runs yet. Run: nexus-mcp-ci eval")
        return 0
    if args.source == "show":
        if not args.run_id:
            print("Usage: nexus-mcp-ci eval show <run-id>", file=sys.stderr)
            return 2
        try:
            record = store.load(args.run_id)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        md = paths.eval_home() / args.run_id / "report.md"
        print(json.dumps(record, indent=2) if args.json else md.read_text())
        return 0

    apply_privacy_defaults()
    say = (lambda msg: None) if args.json else (lambda msg: print(msg, file=sys.stderr))
    try:
        if args.source and "://" in args.source and not args.json:
            say(f"Network: git clone of {args.source} (shallow).")
        src = source.resolve(args.source, args.example)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from nexus_mcp.evaluate.quick import run_quick

    try:
        result = run_quick(
            src.root, count=args.questions, seed=args.seed, keep_index=args.keep_index, progress=say
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if src.cleanup:
            shutil.rmtree(src.cleanup, ignore_errors=True)

    run_id = store.new_run_id()
    public = src.kind in ("url", "example")
    record = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "kind": src.kind,
            "label": src.label if public else src.root.name,
            "label_public": src.label if public else None,
            "sha": src.sha,
        },
        "kept_index": args.keep_index,
        "env": store.environment(),
        **result,
    }
    run_dir = paths.eval_home() / run_id
    markdown = report.render(record, str(run_dir))
    store.save(run_id, record, markdown)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.md").write_text(markdown)
        (args.out / "report.json").write_text(json.dumps({"id": run_id, **record}, indent=2))
    print(json.dumps({"id": run_id, **record}, indent=2) if args.json else markdown)
    if not args.json:
        print(f"Saved: {run_dir}", file=sys.stderr)
    return 0


def run_data(argv: List[str]) -> int:
    args = _data_parser().parse_args(argv)
    project = Path(args.path).resolve()
    if args.action == "where":
        print(data_cmd.where(project))
        return 0
    if args.action == "export-memories":
        try:
            count = data_cmd.export_memories(project, args.out)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {count} memories to {args.out}")
        return 0
    targets = (
        list(data_cmd.TARGETS) if args.all else [t for t in data_cmd.TARGETS if getattr(args, t)]
    )
    if not targets:
        print(
            "Choose what to delete: " + ", ".join(f"--{t}" for t in data_cmd.TARGETS) + ", --all",
            file=sys.stderr,
        )
        return 2
    try:
        outcome = data_cmd.clear(project, targets, args.yes)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not outcome["plan"]:
        print("Nothing to delete.")
        return 0
    verb = "Would delete" if outcome["dry_run"] else "Deleted"
    for item in outcome["plan"]:
        print(f"{verb}: {item['path']}  ({paths.human_size(item['bytes'])}, {item['files']} files)")
    if outcome["dry_run"]:
        print("Dry run. Nothing was deleted. Add --yes to delete. Stop your MCP client first.")
    return 0


def dispatch(argv: List[str]) -> Optional[int]:
    """Run a subcommand and return its exit code, or None when ``argv`` is not one."""
    if not argv or argv[0] not in SUBCOMMANDS:
        return None
    return run_eval(argv[1:]) if argv[0] == "eval" else run_data(argv[1:])

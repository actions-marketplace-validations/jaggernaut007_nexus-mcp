"""Copy the log files of each benchmark run into benchmarks/runs/, so they stay in git.

benchmarks/results/ is git-ignored and holds the index folders too (hundreds of MB). This
script copies only the small log files: live records (*.jsonl), tier results (*.json), the
per-run summaries and the console logs given with --log. It leaves out index folders.

The files in benchmarks/runs/ are raw logs for issues and for checks of a result. They are
not published numbers: scripts/publish_results.py reads benchmarks/published/results.json only.

Run: python scripts/archive_runs.py [--log LABEL=PATH ...]
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
RUNS = ROOT / "benchmarks" / "runs"
SKIP_DIRS = {"retrieval-work", "indexes", "mcp-configs"}
SUFFIXES = {".json", ".jsonl", ".log"}
# A run log must never carry a credential. `set -x` once echoed the OAuth token into one.
SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}"
    r"|oat01-[A-Za-z0-9_-]{8,}"
    r"|Bearer [A-Za-z0-9._-]{20,}"
    r"|gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    # A `NAME=value` line of a shell trace, where the name says it holds a secret.
    r"|[A-Z_]*(?:TOKEN|SECRET|API_KEY|PASSWORD)[A-Z_]*=['\"]?(?!\[REDACTED\])[^\s'\"]{8,}"
)
# The environment variables and the Keychain item that can hold a live credential. Their
# values are compared as text, so a token of any shape is caught.
SECRET_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "HF_TOKEN", "GH_TOKEN",
              "GITHUB_TOKEN")
KEYCHAIN_ITEM = "nexus-bench-oauth"
LABEL = re.compile(r"^[A-Za-z0-9._-]+$")


def live_secrets() -> list[str]:
    """The live credential values of this machine. Never print them."""
    values = [os.environ.get(name, "") for name in SECRET_ENV]
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_ITEM, "-w"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            values.append(proc.stdout.strip() if proc.returncode == 0 else "")
        except (OSError, subprocess.SubprocessError):
            pass
    return [v for v in values if len(v) >= 8]


def scrub_home(text: str, home: str = str(Path.home())) -> str:
    """Replace the home folder (it names the local user) with `~`."""
    return text.replace(home, "~") if home and home != "/" else text


def run_label(rel: Path) -> str:
    """The run a result file belongs to: `languages-final/x.jsonl` -> `languages-final`."""
    first = rel.parts[0]
    if first.startswith("setup_meta-"):
        return "setup"  # one file per pinned install: version, commit, install time
    return first[: -len(".json")] if first.endswith(".json") else first


def log_files(results: Path):
    """Yield the log files under `results`, without index folders."""
    for path in sorted(results.rglob("*")):
        rel = path.relative_to(results)
        if path.is_file() and path.suffix in SUFFIXES and not SKIP_DIRS & set(rel.parts):
            yield path, rel


def check_clean(path: Path, secrets: list[str] | None = None) -> str:
    """The text of a file. Stop when it holds a credential-like string or a live secret."""
    text = path.read_text(errors="ignore")
    if SECRET.search(text) or any(s in text for s in secrets or ()):
        sys.exit(f"Not archived: {path} holds a credential-like string. Redact it first.")
    return text


def check_label(label: str) -> str:
    """A run label becomes a folder name: refuse `/`, `..` and other path parts."""
    if not LABEL.match(label) or label in {".", ".."}:
        sys.exit(f"Not archived: bad run label {label!r}. Use letters, digits, '.', '_', '-'.")
    return label


def _copy(text: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(scrub_home(text))
    return dest


def archive(
    results: Path = RESULTS,
    runs: Path = RUNS,
    logs: dict | None = None,
    secrets: list[str] | None = None,
) -> list[Path]:
    """Copy each log file to runs/<run>/<file>, with the home folder as `~`.

    Returns the written paths. ``secrets`` are the live credential values (default: read
    from the environment and the Keychain); a file that holds one is refused.
    """
    secrets = live_secrets() if secrets is None else secrets
    written = []
    for path, rel in log_files(results):
        text = check_clean(path, secrets)
        label = check_label(run_label(rel))
        dest = runs / label / (rel.name if len(rel.parts) == 1 else Path(*rel.parts[1:]))
        written.append(_copy(text, dest))
    for label, log in (logs or {}).items():
        text = check_clean(Path(log), secrets)
        written.append(_copy(text, runs / check_label(label) / "console.log"))
    return written


def scrub_existing(runs: Path = RUNS) -> int:
    """Replace the home folder with `~` in the files already in runs/. Returns the count."""
    changed = 0
    for path in sorted(runs.rglob("*")):
        if path.is_file() and path.suffix in SUFFIXES:
            text = path.read_text(errors="ignore")
            clean = scrub_home(text)
            if clean != text:
                path.write_text(clean)
                changed += 1
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--log", action="append", default=[], metavar="LABEL=PATH",
                        help="A console log to keep as benchmarks/runs/LABEL/console.log")
    parser.add_argument("--scrub-existing", action="store_true",
                        help="Only replace the home folder with ~ in benchmarks/runs/")
    args = parser.parse_args(argv)
    if args.scrub_existing:
        print(f"scrubbed {scrub_existing()} files in {RUNS.relative_to(ROOT)}")
        return 0
    logs = dict(item.split("=", 1) for item in args.log)
    written = archive(logs=logs)
    print(f"archived {len(written)} files in {RUNS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

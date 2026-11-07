"""Live search engine using ripgrep (rg) with a fallback to standard grep.

Provides 100% code coverage for unindexed or newly created files.
"""

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

REFERENCES_TIMEOUT_S = 10
REFERENCE_TEXT_CHARS = 110
# At most this many class names rank the reference files (a class and its subclasses).
MAX_RELATED_NAMES = 200
# Files whose lines are noise for "where is this name used": translations, prose, maps and
# lock files. They add only to the `non_code` count. Templates, `.vue`, `.toml`, `.yaml` and
# other files that can use a name keep their lines. On Django the word `validate` had 100+
# `.po` files, which took the line budget before the code.
NOISE_SUFFIXES = frozenset({
    ".po", ".pot", ".mo", ".md", ".rst", ".txt", ".adoc", ".svg", ".map", ".lock", ".csv",
    ".tsv", ".log", ".ipynb",
})
# Modifiers in front of a function or method head (PHP, Rust, TypeScript, C#-like).
_DEF_MODIFIERS = (
    r"(?:(?:export|default|public|protected|private|internal|static|abstract|final|async|"
    r"override|virtual|unsafe|const|extern|pub(?:\([\w:]+\))?)\s+)*"
)
# ripgrep skips hidden and git-ignored paths by itself; plain grep needs the list.
_GREP_SKIP_DIRS = (
    ".git", ".nexus*", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
)


def _clean_path(path: str) -> str:
    """A grep path without the leading `./`."""
    return path[2:] if path.startswith("./") else path


def _is_hidden(path: str) -> bool:
    """True for a path with a hidden part (`.env.py`, `.config/x.py`), as ripgrep skips."""
    return any(part.startswith(".") for part in PurePosixPath(path).parts)


class LiveGrepEngine:
    """Live search using ripgrep (rg) with a fallback to standard grep."""

    def __init__(self, workspace_path: Optional[str] = None):
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.rg_path = shutil.which("rg")
        self.grep_path = shutil.which("grep")
        self.git_path = shutil.which("git")

    def _in_git_work_tree(self) -> bool:
        """True when git is available and the workspace is inside a git work tree."""
        if not self.git_path:
            return False
        try:
            proc = subprocess.run(
                [self.git_path, "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, check=False,
                cwd=str(self.workspace_path), timeout=REFERENCES_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for query in workspace. Favors ripgrep, falls back to grep."""
        if not query or not query.strip():
            return []

        if self.rg_path:
            try:
                return self._search_rg(query, limit)
            except Exception as e:
                logger.warning("Ripgrep search failed, trying grep: %s", e)

        if self.grep_path:
            try:
                return self._search_grep(query, limit)
            except Exception as e:
                logger.warning("Grep search failed: %s", e)

        logger.warning("Neither ripgrep nor grep found on system.")
        return []

    def _word_grep(self, names: Sequence[str], list_files: bool) -> Optional[str]:
        """Run a whole-word, fixed-string search for ``names`` from the workspace root.

        With ``list_files`` the output is one path per line, else ``path:line:text``.
        Returns None when neither ripgrep nor grep is available or the search fails.
        """
        patterns: List[str] = []
        for n in names:
            patterns += ["-e", n]
        if self.rg_path:
            cmd = [self.rg_path, "--word-regexp", "--fixed-strings", "--no-messages",
                   "--color", "never"]
            cmd += ["--files-with-matches"] if list_files else ["--no-heading", "--line-number"]
            cmd += patterns + ["."]
        elif self._in_git_work_tree():
            # Like ripgrep, `git grep` skips git-ignored files (a local settings file with a
            # secret must not reach the agent as text).
            cmd = [self.git_path, "grep", "--untracked", "--exclude-standard", "-I", "-w",
                   "-F", "--no-color"]
            cmd += ["-l"] if list_files else ["-n"]
            cmd += patterns + ["--", "."]
        elif self.grep_path:
            # Hidden files are dropped after the search (`_is_hidden`): macOS grep matches an
            # --exclude glob against the whole path, GNU grep against the file name.
            cmd = [self.grep_path, "-rlIwF" if list_files else "-rnIwF"]
            cmd += [f"--exclude-dir={d}" for d in _GREP_SKIP_DIRS]
            cmd += patterns + ["."]
        else:
            return None
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                cwd=str(self.workspace_path), timeout=REFERENCES_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("Reference search failed: %s", e)
            return None
        if self.rg_path:
            return proc.stdout
        # Without ripgrep, drop hidden files as ripgrep would (`git grep` keeps tracked ones).
        return "\n".join(
            line for line in proc.stdout.splitlines()
            if not _is_hidden(_clean_path(line if list_files else line.split(":", 1)[0]))
        )

    def references(
        self,
        name: str,
        max_lines: int = 60,
        max_lines_per_file: int = 8,
        related_names: Optional[Sequence[str]] = None,
        skip_definitions: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Every line where ``name`` appears as a whole word, as a short answer.

        This is the complete text answer to "where is this name used". Call edges in the
        graph are a lower bound (a name with several definitions gets no edge), so an
        agent that only gets edges runs its own grep to check them, and then reads the
        lines around each hit. Returns None when neither ripgrep nor grep is available or
        the search fails.

        Source files come first, each with the text of its lines (``"line: code"``).
        Test files come as a count of lines for each file. A source file past the
        ``max_lines`` budget comes as a count in ``more_files``. Templates and config files
        come after the code. A noise file (``NOISE_SUFFIXES``: translations, prose, lock
        files) adds only to ``non_code`` (files and lines). ``total_files`` and
        ``total_lines`` always count everything found.

        ``related_names`` (the class of a method and its subclasses): source files that
        name one of them come first, so the line budget goes to the code of that class.
        ``skip_definitions``: a ``def name`` or ``function name`` line defines a method of
        the same name in another class. It is not a use, so it only adds to
        ``definitions`` (a line count). On Django 91 of the 283 `as_sql` lines were such
        definitions, and the budget ran out before it reached `db/models/sql`.
        """
        from nexus_mcp.engines.fusion import is_test_path
        from nexus_mcp.parsing.language_registry import get_supported_extensions

        name = re.split(r"\.|::", name)[-1].strip()
        if not name:
            return None
        output = self._word_grep([name], list_files=False)
        if output is None:
            return None

        by_file: Dict[str, List[tuple]] = {}
        for line in output.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            by_file.setdefault(_clean_path(parts[0]), []).append((int(parts[1]), parts[2].strip()))

        near: set = set()
        related = [n for n in (related_names or ()) if n and n != name]
        if related and by_file:
            listed = self._word_grep(related[:MAX_RELATED_NAMES], list_files=True) or ""
            near = {_clean_path(p) for p in listed.splitlines() if p}
        # `def`, `function`, `fn` and `func` heads, a Go method with its receiver included.
        definition = re.compile(
            rf"^{_DEF_MODIFIERS}(?:def|function\s*\*?|fn|func(?:\s*\([^)]*\))?)\s*"
            rf"&?\s*{re.escape(name)}\b"
        )

        files: Dict[str, List[str]] = {}
        more_files: Dict[str, int] = {}
        test_files: Dict[str, int] = {}
        non_code = {"files": 0, "lines": 0}
        definitions = 0
        code_suffixes = get_supported_extensions()
        budget = max_lines
        truncated = False

        def order(p: str) -> tuple:
            # Tests last; then the files of the class family; code before templates/config.
            suffix = PurePosixPath(p).suffix.lower()
            return (is_test_path(p), p not in near, suffix not in code_suffixes, p)

        for path in sorted(by_file, key=order):
            hits = by_file[path]
            if skip_definitions and not is_test_path(path):
                kept = [h for h in hits if not definition.match(h[1])]
                definitions += len(hits) - len(kept)
                hits = kept
                if not hits:
                    continue
            if PurePosixPath(path).suffix.lower() in NOISE_SUFFIXES:
                non_code["files"] += 1
                non_code["lines"] += len(hits)
                continue
            if is_test_path(path):
                test_files[path] = len(hits)
                continue
            take = min(len(hits), max_lines_per_file, budget)
            if take <= 0:
                more_files[path] = len(hits)
                truncated = True
                continue
            files[path] = [f"{n}: {text[:REFERENCE_TEXT_CHARS]}" for n, text in hits[:take]]
            budget -= take
            truncated = truncated or take < len(hits)
        result: Dict[str, Any] = {
            "total_files": len(by_file),
            "total_lines": sum(len(v) for v in by_file.values()),
            "files": files,
            "truncated": truncated,
        }
        if more_files:
            result["more_files"] = more_files
        if test_files:
            result["test_files"] = test_files
        if non_code["files"]:
            result["non_code"] = non_code
        if definitions:
            result["definitions"] = definitions
        return result

    def _search_rg(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Run ripgrep with JSON output."""
        # Use -M for max-columns to avoid huge lines, --json for structured data
        cmd = [
            self.rg_path,
            "--json",
            "--line-number",
            "--max-count",
            str(limit),
            "--smart-case",
            "--heading",
            "-e",  # a query that starts with "-" must not become a flag (rg --pre runs commands)
            query,
            str(self.workspace_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            results = []
            for line in result.stdout.splitlines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        payload = data["data"]
                        path_text = payload["path"]["text"]

                        # Handle both relative and absolute paths from rg
                        if os.path.isabs(path_text):
                            abs_path = path_text
                        else:
                            abs_path = str((self.workspace_path / path_text).absolute())

                        results.append(
                            {
                                "absolute_path": abs_path,
                                "line_start": payload["line_number"],
                                "code_snippet": payload["lines"]["text"].strip(),
                                "score": 0.5,  # Baseline score for live-grep
                                "search_mode": "live_grep_rg",
                            }
                        )
                except (json.JSONDecodeError, KeyError):
                    continue
            return results[:limit]
        except Exception as e:
            logger.error("Ripgrep execution error: %s", e)
            raise

    def _search_grep(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Run standard grep -rn."""
        # Note: grep -rn is recursive, line numbered, and ignores binary files with -I
        # macOS grep supports -m for max-count
        cmd = [
            self.grep_path,
            "-rnI",
            "-m",
            str(limit),
            "-e",  # a query that starts with "-" must not become a flag
            query,
            str(self.workspace_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            results = []
            for line in result.stdout.splitlines():
                if not line:
                    continue
                # Format: file:line:content
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    try:
                        path_text = parts[0]
                        if os.path.isabs(path_text):
                            abs_path = path_text
                        else:
                            abs_path = str((self.workspace_path / path_text).absolute())

                        results.append(
                            {
                                "absolute_path": abs_path,
                                "line_start": int(parts[1]),
                                "code_snippet": parts[2].strip(),
                                "score": 0.4,  # Slightly lower score for grep fallback
                                "search_mode": "live_grep_grep",
                            }
                        )
                    except ValueError:
                        continue
            return results[:limit]
        except Exception as e:
            logger.error("Grep execution error: %s", e)
            raise

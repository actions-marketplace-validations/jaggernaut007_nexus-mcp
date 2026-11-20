"""SQL tables and views as boundary providers (ADR-026).

A Supabase code base reads a table with `.from("orders")` and defines it in a migration. No
call links the two. This module reads the statements of a `.sql` file that name a table or
a view: `create`, `alter` and `drop`. It needs no grammar, because only the statement head
and the column names matter.

A statement is a dict: `action` (`create`, `alter`, `drop`), `name` (lower case, without
schema or quotes), `start_line`, `end_line`, `destructive` (a drop, a rename, or an
alter that drops something) and `columns` (the column names of a `create table`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_NAME = r'((?:"[^"]+"|[\w$]+)(?:\.(?:"[^"]+"|[\w$]+))*)'
_CREATE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?(?:(?:global\s+|local\s+)?(?:temp|temporary)\s+|unlogged\s+)?"
    r"(?:materialized\s+)?(?P<what>table|view)\s+(?:if\s+not\s+exists\s+)?" + _NAME,
    re.IGNORECASE,
)
_ALTER = re.compile(
    r"\balter\s+(?:materialized\s+)?(?P<what>table|view)\s+(?:if\s+exists\s+)?(?:only\s+)?"
    + _NAME,
    re.IGNORECASE,
)
_DROP = re.compile(
    r"\bdrop\s+(?:materialized\s+)?(?P<what>table|view)\s+(?:if\s+exists\s+)?" + _NAME,
    re.IGNORECASE,
)
_DESTRUCTIVE_ALTER = re.compile(r"\b(drop|rename)\b", re.IGNORECASE)
_NOT_A_COLUMN = {
    "primary", "foreign", "unique", "constraint", "check", "like", "exclude", "period",
}
MAX_STATEMENTS = 500


def table_name(raw: str) -> str:
    """`public."Orders"` is `orders`: the last part, unquoted, lower case."""
    return raw.rsplit(".", 1)[-1].strip('"').lower()


def _blank_comments(text: str) -> str:
    """Replace comments by spaces of the same length, so that line numbers stay true."""
    return _COMMENTS.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _statement_end(text: str, start: int) -> int:
    """Index of the `;` that ends the statement at `start`, outside quotes and `$$` bodies."""
    i, size = start, len(text)
    quote = ""
    while i < size:
        ch = text[i]
        if quote:
            if text.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
        elif ch in "'\"":
            quote = ch
        elif ch == "$" and (m := re.match(r"\$[A-Za-z_]*\$", text[i:])):
            quote = m.group(0)
            i += len(quote)
            continue
        elif ch == ";":
            return i
        i += 1
    return size


def _columns(statement: str) -> List[str]:
    """Column names of a `create table (...)` statement, in order."""
    open_at = statement.find("(")
    if open_at < 0:
        return []
    depth, item, items = 0, [], []
    for ch in statement[open_at:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        elif ch == "," and depth == 1:
            items.append("".join(item))
            item = []
            continue
        item.append(ch)
    items.append("".join(item))
    names: List[str] = []
    for part in items:
        match = re.match(r'\s*("[^"]+"|[\w$]+)', part)
        if not match:
            continue
        name = match.group(1).strip('"').lower()
        if name not in _NOT_A_COLUMN and name not in names:
            names.append(name)
    return names


def sql_statements(content: str) -> List[Dict[str, Any]]:
    """The create, alter and drop statements of tables and views in `content`."""
    text = _blank_comments(content)
    found: List[Dict[str, Any]] = []
    for action, pattern in (("create", _CREATE), ("alter", _ALTER), ("drop", _DROP)):
        for match in pattern.finditer(text):
            start = match.start()
            end = _statement_end(text, match.end())
            body = text[start:end]
            tail = body[match.end() - start:]
            statement: Dict[str, Any] = {
                "action": action,
                "what": match.group("what").lower(),
                "name": table_name(match.group(match.lastindex)),
                "start_line": text.count("\n", 0, start) + 1,
                "end_line": text.count("\n", 0, end) + 1,
                "destructive": action == "drop" or (
                    action == "alter" and bool(_DESTRUCTIVE_ALTER.search(tail))
                ),
            }
            if action == "create" and statement["what"] == "table":
                statement["columns"] = _columns(body)
            found.append(statement)
            if len(found) >= MAX_STATEMENTS:
                return sorted(found, key=lambda s: s["start_line"])
    return sorted(found, key=lambda s: s["start_line"])

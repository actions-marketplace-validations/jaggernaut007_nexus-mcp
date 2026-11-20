"""AST-grep based structural parser for code graph building.

Ported from code-graph-mcp's universal_parser.py. Uses ast-grep for
structural analysis across 25+ languages.
"""

import bisect
import logging
import posixpath
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from ast_grep_py import SgRoot  # type: ignore[import-untyped]
except ImportError:
    SgRoot = None

from nexus_mcp.core.graph_models import (
    MODULE_BODY_NAME,
    NodeType,
    RelationshipType,
    UniversalGraph,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.parsing.boundaries import (
    KIND_TABLE,
    collect_facts,
    nest_route_serves,
    python_route_serves,
)
from nexus_mcp.parsing.language_registry import (
    ASTGREP_LANGUAGES,
    get_language_for_file,
)
from nexus_mcp.parsing.sql_boundaries import sql_statements
from nexus_mcp.parsing.treesitter_parser import (
    c_declarator_name,
    is_cpp_header,
    mask_cpp_class_macros,
    split_cpp_scope,
)
from nexus_mcp.parsing.treesitter_parser import (
    default_export_name as _default_export_name,
)

logger = logging.getLogger(__name__)

# A callee field that is not one field: the first named child holds the callee
# (`new User()` in PHP has no field for `User`).
FIRST_NAMED_CHILD = "*"

# Call expressions per language: (node kind, field that holds the callee). A pair of
# fields is (receiver, name), joined with a dot: Java `obj.m()`, PHP `$obj->m()`.
# None is ("object", "name").
CALL_KINDS: Dict[str, tuple] = {
    "python": (("call", "function"),),
    "javascript": (("call_expression", "function"), ("new_expression", "constructor")),
    "typescript": (("call_expression", "function"), ("new_expression", "constructor")),
    "go": (("call_expression", "function"),),
    "java": (("method_invocation", None), ("object_creation_expression", "type")),
    "rust": (("call_expression", "function"),),
    "c": (("call_expression", "function"),),
    "cpp": (("call_expression", "function"), ("new_expression", "type")),
    "csharp": (
        ("invocation_expression", "function"), ("object_creation_expression", "type"),
    ),
    "php": (
        ("function_call_expression", "function"),
        ("member_call_expression", ("object", "name")),
        ("nullsafe_member_call_expression", ("object", "name")),
        ("scoped_call_expression", ("scope", "name")),
        ("object_creation_expression", FIRST_NAMED_CHILD),
    ),
}
# Receivers that mean the own class or its base class, per language.
RECEIVER_ALIASES: Dict[str, Dict[str, str]] = {
    "php": {"static": "self", "parent": "super"},
    "csharp": {"base": "super"},
}

# Function and class definitions per language, by node kind.
FUNCTION_KINDS: Dict[str, tuple] = {
    "python": ("function_definition",),
    "javascript": ("function_declaration", "method_definition"),
    "typescript": ("function_declaration", "method_definition"),
    "go": ("function_declaration", "method_declaration"),
    "java": ("method_declaration",),
    "rust": ("function_item",),
    "c": ("function_definition",),
    "cpp": ("function_definition",),
    "csharp": ("method_declaration", "constructor_declaration", "local_function_statement"),
    "php": ("function_definition", "method_declaration"),
}
CLASS_KINDS: Dict[str, tuple] = {
    "python": ("class_definition",),
    "javascript": ("class_declaration",),
    "typescript": ("class_declaration",),
    "java": ("class_declaration",),
    "rust": ("struct_item",),
    "c": ("struct_specifier", "union_specifier"),
    "cpp": ("class_specifier", "struct_specifier", "union_specifier"),
    "csharp": (
        "class_declaration", "struct_declaration", "interface_declaration",
        "record_declaration",
    ),
    "php": (
        "class_declaration", "interface_declaration", "trait_declaration",
        "enum_declaration",
    ),
}
_C_FAMILY = ("c", "cpp")

# Function-like node kinds. A function that sits inside one of these is part of it and
# is not a node of its own: its calls belong to the function that holds it.
_JS_FUNCTION_LIKE = (
    "function_declaration", "method_definition", "arrow_function", "function_expression",
    "function", "generator_function", "generator_function_declaration",
)
FUNCTION_LIKE_KINDS: Dict[str, tuple] = {
    "python": ("function_definition", "lambda"),
    "javascript": _JS_FUNCTION_LIKE,
    "typescript": _JS_FUNCTION_LIKE,
}
# JS/TS function values that get a node when they are bound to a name.
_JS_FUNCTION_VALUES = ("arrow_function", "function_expression", "function")
# Wrappers that do not change what a value is: `(() => 1)`, `(() => 1) as F`.
_JS_VALUE_WRAPPERS = (
    "parenthesized_expression", "as_expression", "satisfies_expression",
    "non_null_expression", "type_assertion",
)
# Languages whose loose calls at module level get a `<top-level>` node.
MODULE_BODY_LANGUAGES = frozenset({"python", "javascript", "typescript"})
_EXPORTS_TARGET = re.compile(r"^(?:module\.)?exports\.([A-Za-z_$][\w$]*)$")
_PROTOTYPE_TARGET = re.compile(r"^([A-Za-z_$][\w$]*)\.prototype\.([A-Za-z_$][\w$]*)$")

# Enclosing type kinds per language: (node kind, field that holds the name).
CLASS_ANCESTOR_KINDS: Dict[str, tuple] = {
    "python": (("class_definition", "name"),),
    "javascript": (("class_declaration", "name"),),
    "typescript": (("class_declaration", "name"),),
    "java": (
        ("class_declaration", "name"),
        ("interface_declaration", "name"),
        ("enum_declaration", "name"),
    ),
    "rust": (("impl_item", "type"),),
    "cpp": (
        ("class_specifier", "name"),
        ("struct_specifier", "name"),
        ("union_specifier", "name"),
    ),
    "csharp": (
        ("class_declaration", "name"),
        ("struct_declaration", "name"),
        ("interface_declaration", "name"),
        ("record_declaration", "name"),
    ),
    "php": (
        ("class_declaration", "name"),
        ("interface_declaration", "name"),
        ("trait_declaration", "name"),
        ("enum_declaration", "name"),
    ),
}

# Branch points that add one to cyclomatic complexity, per language. `&&`/`||` are
# counted only for Python (`boolean_operator`); the other grammars give them no
# distinct node kind, so those branches are not counted.
COMPLEXITY_KINDS: Dict[str, tuple] = {
    "python": (
        "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
        "conditional_expression", "boolean_operator", "for_in_clause", "if_clause",
        "case_clause",
    ),
    "javascript": (
        "if_statement", "for_statement", "for_in_statement", "while_statement",
        "do_statement", "switch_case", "catch_clause", "ternary_expression",
    ),
    "typescript": (
        "if_statement", "for_statement", "for_in_statement", "while_statement",
        "do_statement", "switch_case", "catch_clause", "ternary_expression",
    ),
    "go": (
        "if_statement", "for_statement", "expression_case", "type_case",
        "communication_case",
    ),
    "java": (
        "if_statement", "for_statement", "enhanced_for_statement", "while_statement",
        "do_statement", "switch_label", "catch_clause", "ternary_expression",
    ),
    "rust": (
        "if_expression", "for_expression", "while_expression", "loop_expression",
        "match_arm",
    ),
    "c": (
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression",
    ),
    "cpp": (
        "if_statement", "for_statement", "for_range_loop", "while_statement",
        "do_statement", "case_statement", "catch_clause", "conditional_expression",
    ),
    "csharp": (
        "if_statement", "for_statement", "foreach_statement", "while_statement",
        "do_statement", "switch_section", "catch_clause", "conditional_expression",
    ),
    "php": (
        "if_statement", "else_if_clause", "for_statement", "foreach_statement",
        "while_statement", "do_statement", "case_statement", "catch_clause",
        "conditional_expression",
    ),
}

MAX_DOCSTRING_CHARS = 500
MAX_CALLS_PER_FUNCTION = 200
MAX_CALL_TEXT = 120
_PAREN_GROUP = re.compile(r"\([^()]*\)")
_GENERIC_GROUP = re.compile(r"<[^<>]*>")
_DOTTED_NAME = re.compile(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*")


def normalize_call_text(text: str) -> str:
    """Reduce a callee expression to dotted names.

    `PaymentGateway().charge` -> `PaymentGateway.charge`; `self.db.execute`
    stays as is; `a?.b` and `a::b` become `a.b`. Returns "" when nothing usable
    remains (for example a call on a subscript or a lambda).
    """
    text = " ".join(text.split())
    for _ in range(4):  # nested argument lists, for example a(b(c)).d
        reduced = _PAREN_GROUP.sub("", text)
        if reduced == text:
            break
        text = reduced
    text = _GENERIC_GROUP.sub("", text).replace("?.", ".").replace("::", ".")
    text = text.replace("->", ".")
    if len(text) > MAX_CALL_TEXT or not _DOTTED_NAME.fullmatch(text):
        return ""
    return text


def _js_require_source(call) -> str:
    """`./x` for `require('./x')` and `import('./x')`, else "" (also for a computed path)."""
    callee = call.field("function")
    if callee is None:
        return ""
    if callee.kind() != "import" and not (
        callee.kind() == "identifier" and callee.text() == "require"
    ):
        return ""
    args = call.field("arguments")
    first = next((c for c in args.children() if c.is_named()), None) if args else None
    if first is None or first.kind() != "string":
        return ""
    return first.text().strip("'\"`")


def _js_import_bindings(sg_node) -> Tuple[Dict[str, List[str]], List[List[str]]]:
    """Local names that a JS/TS file binds to another module, and its re-exports.

    Bindings: {local: [source, imported]}. `imported` is the exported name, "default" for
    a default import, or "*" for a whole module (`import * as ns`, `const m = require(..)`).
    Re-exports: [source, imported, exported] for `export { a as b } from './x'`; `export *
    from './x'` is [source, "*", "*"].
    """
    bindings: Dict[str, List[str]] = {}
    reexports: List[List[str]] = []

    def strip(node) -> str:
        return node.text().strip("'\"`")

    for stmt in sg_node.find_all(kind="import_statement"):
        source_node = stmt.field("source")
        clause = next((c for c in stmt.children() if c.kind() == "import_clause"), None)
        if source_node is None or clause is None:
            continue
        source = strip(source_node)
        for part in clause.children():
            kind = part.kind()
            if kind == "identifier":
                bindings[part.text()] = [source, "default"]
            elif kind == "namespace_import":
                ident = next((c for c in part.children() if c.kind() == "identifier"), None)
                if ident is not None:
                    bindings[ident.text()] = [source, "*"]
            elif kind == "named_imports":
                for spec in part.children():
                    if spec.kind() != "import_specifier":
                        continue
                    name, alias = spec.field("name"), spec.field("alias")
                    if name is not None:
                        bindings[(alias or name).text()] = [source, name.text()]

    for decl in sg_node.find_all(kind="variable_declarator"):
        value = decl.field("value")
        if value is not None and value.kind() == "await_expression":
            value = next((c for c in value.children() if c.is_named()), None)
        if value is None or value.kind() != "call_expression":
            continue
        source = _js_require_source(value)
        target = decl.field("name")
        if not source or target is None:
            continue
        if target.kind() == "identifier":
            bindings[target.text()] = [source, "*"]
        elif target.kind() == "object_pattern":
            for item in target.children():
                if item.kind() == "shorthand_property_identifier_pattern":
                    bindings[item.text()] = [source, item.text()]
                elif item.kind() == "pair_pattern":
                    key, local = item.field("key"), item.field("value")
                    if key is not None and local is not None and local.kind() == "identifier":
                        bindings[local.text()] = [source, key.text()]

    for stmt in sg_node.find_all(kind="export_statement"):
        source_node = stmt.field("source")
        if source_node is None:
            continue
        source = strip(source_node)
        clause = next((c for c in stmt.children() if c.kind() == "export_clause"), None)
        if clause is None:
            reexports.append([source, "*", "*"])
            continue
        for spec in clause.children():
            if spec.kind() != "export_specifier":
                continue
            name, alias = spec.field("name"), spec.field("alias")
            if name is not None:
                reexports.append([source, name.text(), (alias or name).text()])
    return bindings, reexports


class _SgView:
    """The tree-sitter node API over an ast-grep node, for `c_declarator_name`."""

    __slots__ = ("node",)

    def __init__(self, node):
        self.node = node

    @property
    def type(self) -> str:
        return self.node.kind()

    def child_by_field_name(self, name: str) -> Optional["_SgView"]:
        child = self.node.field(name)
        return _SgView(child) if child else None

    @property
    def named_children(self) -> List["_SgView"]:
        return [_SgView(c) for c in self.node.children() if c.is_named()]


def _sg_text(view: "_SgView") -> str:
    return view.node.text()


def _language_callee(text: str, language: str) -> str:
    """Callee text in the common form before normalize_call_text().

    PHP: `$this->m` is `this.m`, `\\App\\fmt` is `fmt`, `static::m` is `self.m`,
    `parent::m` is `super.m`. C#: `base.M` is `super.M`. C++: `::g` is `g`.
    """
    if language == "php":
        text = text.replace("$", "")
        if "(" not in text:  # a namespace prefix, not a backslash in an argument
            text = text.rsplit("\\", 1)[-1]
    elif language == "cpp":
        text = text.lstrip(":").strip()
    aliases = RECEIVER_ALIASES.get(language)
    if aliases:
        head, sep, rest = text.partition(".")
        if sep and head in aliases:
            text = f"{aliases[head]}.{rest}"
    return text


@dataclass(frozen=True)
class AstGrepLanguageConfig:
    """Configuration for ast-grep language parsing."""

    name: str
    extensions: tuple
    ast_grep_id: str
    function_patterns: tuple
    class_patterns: tuple
    variable_patterns: tuple
    import_patterns: tuple


# ast-grep language ID mapping
ASTGREP_LANG_IDS: Dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "c": "c",
    "cpp": "cpp",
    "go": "go",
    "java": "java",
    "kotlin": "kotlin",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "csharp": "csharp",  # ast-grep has no "c_sharp"; that id panics past `except Exception`
    "scala": "scala",
    "lua": "lua",
    # No "dart": ast-grep-py ships no Dart grammar, and an unknown id panics past
    # `except Exception`. parse_file() returns 0 for a language with no id.
}


class AstGrepParser:
    """Structural code parser using ast-grep for graph building."""

    def __init__(self):
        if SgRoot is None:
            raise ImportError(
                "ast-grep-py is required. Install with: pip install ast-grep-py"
            )
        self._node_counter = 0
        self._rel_counter = 0
        # Node ids must be unique across processes: a graph restored from disk holds
        # ids from an earlier parser, and a new parser that restarted its counter would
        # reuse them, so add_node() would silently drop the new nodes.
        self._id_token = uuid.uuid4().hex[:8]

    def can_parse(self, filepath: str) -> bool:
        """True when ast-grep has structure rules for the language of this file."""
        lang = get_language_for_file(filepath)
        return lang is not None and lang in ASTGREP_LANGUAGES

    def parse_file(self, filepath: str, graph: UniversalGraph) -> int:
        """Parse a file and add nodes/relationships to the graph.

        Returns the number of nodes added.
        """
        file_path = Path(filepath)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        language = get_language_for_file(filepath)
        if language is None or language not in ASTGREP_LANGUAGES:
            return 0
        if not ASTGREP_LANG_IDS.get(language) and language != "sql":
            return 0

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return 0
        return self.parse_source(content, filepath, graph)

    def parse_source(self, content: str, filepath: str, graph: UniversalGraph) -> int:
        """Parse source text that is not on disk, as the file `filepath`.

        `parse_file` reads a file and calls this. The post-change check uses it for the
        text of a file at an old commit (`git show <base>:<path>`). `filepath` gives the
        language and goes into every node. Returns the number of nodes added.
        """
        file_path = Path(filepath)
        language = get_language_for_file(filepath)
        if language is None or language not in ASTGREP_LANGUAGES:
            return 0

        if language == "sql":
            return self._parse_sql(content, filepath, graph)
        ast_grep_id = ASTGREP_LANG_IDS.get(language)
        if not ast_grep_id:
            return 0
        if language == "typescript" and str(filepath).lower().endswith(".tsx"):
            ast_grep_id = "tsx"  # JSX needs the tsx grammar; the language stays typescript

        if language in _C_FAMILY:
            raw = content.encode("utf-8")
            if language == "c" and is_cpp_header(filepath, raw):
                language = ast_grep_id = "cpp"
            if language == "cpp":
                # Same mask as the tree-sitter parser: `class BASE_EXPORT Foo {` is a class.
                content = mask_cpp_class_macros(raw).decode("utf-8", errors="replace")

        # Create module node
        module_id = self._make_id("module")
        lines = content.count("\n") + 1
        module_node = UniversalNode(
            id=module_id,
            name=file_path.stem,
            node_type=NodeType.MODULE,
            location=UniversalLocation(
                file_path=str(filepath),
                start_line=1,
                end_line=lines,
                language=language,
            ),
            language=language,
            line_count=lines,
        )
        graph.add_node(module_node)
        nodes_added = 1

        try:
            root = SgRoot(content, ast_grep_id)
            sg_node = root.root()
        except Exception as e:
            logger.warning("ast-grep parse failed for %s: %s", filepath, e)
            return nodes_added

        # Extract functions. `ranges` collects the byte span of each one, so that the
        # calls outside all of them can go to the `<top-level>` node.
        ranges: List[Tuple[int, int]] = []
        nodes_added += self._extract_functions(
            sg_node, filepath, language, module_id, graph, ranges
        )
        if language in ("javascript", "typescript"):
            nodes_added += self._extract_js_bound_functions(
                sg_node, filepath, language, module_id, graph, ranges
            )
        elif language == "python":
            nodes_added += self._extract_py_bound_lambdas(
                sg_node, filepath, language, module_id, graph, ranges
            )
        nodes_added += self._add_module_body_node(
            sg_node, filepath, language, module_id, graph, ranges, lines
        )
        # Extract classes
        nodes_added += self._extract_classes(
            sg_node, filepath, language, module_id, graph
        )
        # Extract imports
        self._extract_imports(sg_node, filepath, language, module_id, graph)

        return nodes_added

    def _parse_sql(self, content: str, filepath: str, graph: UniversalGraph) -> int:
        """A module node and one node per create, alter or drop of a table or view.

        The node is a `CLASS` named after the table. `boundary_defines` marks the `create`
        statement as the provider of the table for `.from("name")` (ADR-026);
        `boundary_touches` marks an `alter` or a `drop`.
        """
        lines = content.count("\n") + 1
        module_id = self._make_id("module")
        graph.add_node(UniversalNode(
            id=module_id, name=Path(filepath).stem, node_type=NodeType.MODULE,
            location=UniversalLocation(
                file_path=str(filepath), start_line=1, end_line=lines, language="sql",
            ),
            language="sql", line_count=lines,
        ))
        added = 1
        for statement in sql_statements(content):
            key = {"kind": KIND_TABLE, "key": statement["name"]}
            meta: Dict[str, Any] = {"sql": statement["action"], "sql_object": statement["what"]}
            if statement["action"] == "create":
                meta["boundary_defines"] = [key]
                meta["columns"] = statement.get("columns", [])
            else:
                meta["boundary_touches"] = [{**key, "destructive": statement["destructive"]}]
            node_id = self._make_id("table")
            graph.add_node(UniversalNode(
                id=node_id, name=statement["name"], node_type=NodeType.CLASS,
                location=UniversalLocation(
                    file_path=str(filepath), start_line=statement["start_line"],
                    end_line=statement["end_line"], language="sql",
                ),
                language="sql",
                line_count=statement["end_line"] - statement["start_line"] + 1,
                metadata=meta,
            ))
            graph.add_relationship(UniversalRelationship(
                id=self._make_rel_id(), source_id=module_id, target_id=node_id,
                relationship_type=RelationshipType.CONTAINS,
            ))
            added += 1
        return added

    def parse_directory(
        self, directory: Path, graph: UniversalGraph, recursive: bool = True
    ) -> int:
        """Parse all supported files in a directory."""
        files_parsed = 0
        pattern = "**/*" if recursive else "*"

        for file_path in directory.glob(pattern):
            if not file_path.is_file():
                continue
            if not self.can_parse(str(file_path)):
                continue
            # Skip hidden/temp files
            if any(part.startswith(".") for part in file_path.parts):
                continue
            try:
                self.parse_file(str(file_path), graph)
                files_parsed += 1
            except Exception as e:
                logger.warning("Failed to parse %s: %s", file_path, e)

        return files_parsed

    def _make_id(self, prefix: str) -> str:
        self._node_counter += 1
        return f"{prefix}:{self._id_token}-{self._node_counter}"

    def _make_rel_id(self) -> str:
        self._rel_counter += 1
        return f"rel:{self._id_token}-{self._rel_counter}"

    def _extract_functions(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph, ranges: Optional[List[Tuple[int, int]]] = None,
    ) -> int:
        """Extract function definitions from ast-grep node.

        Matches by tree-sitter node *kind* rather than a source pattern: a
        pattern like "def $NAME($$$PARAMS): $$$BODY" silently fails to match
        any function carrying a return annotation (`def f() -> int:`), which
        is most of a typed codebase. Kind matching is annotation-agnostic.

        `ranges` receives the byte span of every function that became a node. A
        decorated Python function counts from its first decorator.
        """
        count = 0
        lang_kinds = FUNCTION_KINDS.get(language)
        if not lang_kinds:
            return 0
        is_js = language in ("javascript", "typescript")

        matches = []
        for kind in lang_kinds:
            try:
                matches.extend(sg_node.find_all(kind=kind))
            except Exception as e:
                logger.debug("ast-grep find_all failed for kind %s: %s", kind, e)

        for match in matches:
            try:
                scope: List[str] = []
                extra: Dict[str, Any] = {}
                if language in _C_FAMILY:
                    resolved = c_declarator_name(_SgView(match), _sg_text)
                    if resolved is None:
                        continue  # a name from the return type is worse than none
                    scope, func_name = resolved
                else:
                    name_text = match.field("name")
                    if name_text:
                        func_name = name_text.text()
                    else:
                        func_name = f"anonymous_{self._node_counter}"
                    if is_js and self._is_default_export(match):
                        extra["export"] = "default"
                        if not name_text:
                            func_name = _default_export_name(filepath)
                if is_js and match.kind() == "method_definition":
                    if match.parent() is not None and match.parent().kind() == "object":
                        # `const api = { get() {} }`: a member of a named object at the
                        # top level. A member of any other object is a callback.
                        owner = self._object_literal_owner(match)
                        if owner is None or self._has_function_ancestor(match, language):
                            continue
                        extra["object_literal"] = True
                span_node = match
                parent = match.parent()
                if language == "python" and parent is not None and (
                    parent.kind() == "decorated_definition"
                ):
                    span_node = parent
                span = span_node.range()
                if ranges is not None:
                    ranges.append((span.start.index, span.end.index))

                metadata = self._function_metadata(match, language, scope)
                if extra.get("object_literal"):
                    metadata["parent_class"] = self._object_literal_owner(match)
                metadata.update(extra)
                if span_node is not match:
                    # The decorators sit above the `def` line. The check maps a changed
                    # decorator line to this function.
                    metadata["decorated_from"] = span.start.line + 1
                self._add_function_node(
                    func_name, match.range(), match, filepath, language, module_id,
                    graph, metadata,
                )
                count += 1
            except Exception as e:
                logger.debug("Failed to extract function at %s: %s", filepath, e)
                continue

        return count

    def _add_function_node(
        self, name: str, rng, match, filepath: str, language: str, module_id: str,
        graph: UniversalGraph, metadata: Dict[str, Any],
    ) -> str:
        """Add one FUNCTION node and the CONTAINS edge from its module. Returns its id.

        `rng` is the span that the node reports (for `export const f = () => ...` the
        whole statement); `match` is the function node that complexity and docstring
        are read from.
        """
        func_id = self._make_id("func")
        graph.add_node(UniversalNode(
            id=func_id,
            name=name,
            node_type=NodeType.FUNCTION,
            location=UniversalLocation(
                file_path=filepath,
                start_line=rng.start.line + 1,
                end_line=rng.end.line + 1,
                start_column=rng.start.column,
                end_column=rng.end.column,
                language=language,
            ),
            language=language,
            line_count=rng.end.line - rng.start.line + 1,
            complexity=self._complexity(match, language),
            docstring=self._docstring(match, language),
            metadata=metadata,
        ))
        graph.add_relationship(UniversalRelationship(
            id=self._make_rel_id(),
            source_id=module_id,
            target_id=func_id,
            relationship_type=RelationshipType.CONTAINS,
        ))
        return func_id

    @staticmethod
    def _has_function_ancestor(match, language: str) -> bool:
        """True when a function-like node encloses this one."""
        kinds = FUNCTION_LIKE_KINDS.get(language, ())
        try:
            return any(a.kind() in kinds for a in match.ancestors())
        except Exception as e:
            logger.debug("Ancestor walk failed: %s", e)
            return False

    @staticmethod
    def _is_default_export(match) -> bool:
        """`export default function f() {}`: the function is the default export."""
        parent = match.parent()
        if parent is None or parent.kind() != "export_statement":
            return False
        return any(c.kind() == "default" for c in parent.children())

    @staticmethod
    def _object_literal_owner(match) -> Optional[str]:
        """`api` for a member of `const api = { ... }`, else None."""
        obj = match.parent()
        if obj is None or obj.kind() != "object":
            return None
        return AstGrepParser._object_literal_owner_of_object(obj)

    def _extract_js_bound_functions(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph, ranges: List[Tuple[int, int]],
    ) -> int:
        """Nodes for JS/TS functions that are values bound to a name.

        `export const handler = async (req) => {}`, `const f = function () {}`, a class
        field `handle = () => {}`, an object member `post: () => {}`,
        `module.exports.x = function () {}` and `export default (req) => {}`. The
        declaration forms (`function f() {}`, methods) are found by _extract_functions.
        An anonymous callback, and any function inside another function, gets no node:
        its calls belong to the function or to the `<top-level>` node that holds it.
        """
        count = 0
        for kind in _JS_FUNCTION_VALUES:
            try:
                found = sg_node.find_all(kind=kind)
            except Exception as e:
                logger.debug("ast-grep find_all failed for kind %s: %s", kind, e)
                continue
            for fn in found:
                try:
                    if self._has_function_ancestor(fn, language):
                        continue
                    bound = self._js_binding(fn, filepath, language)
                    if bound is None:
                        continue
                    name, statement, extra = bound
                    span = statement.range()
                    ranges.append((span.start.index, span.end.index))
                    metadata = self._function_metadata(fn, language)
                    owner = extra.pop("parent_class", None)
                    if owner:
                        metadata["parent_class"] = owner
                    metadata.update(extra)
                    self._add_function_node(
                        name, span, fn, filepath, language, module_id, graph, metadata,
                    )
                    count += 1
                except Exception as e:
                    logger.debug("Failed to extract bound function at %s: %s", filepath, e)
        return count

    def _js_binding(
        self, fn, filepath: str, language: str
    ) -> Optional[Tuple[str, Any, Dict[str, Any]]]:
        """(name, statement node, extra metadata) for a function value, or None.

        None means the function is anonymous: a callback or an inline value.
        """
        value, parent = fn, fn.parent()
        while parent is not None and parent.kind() in _JS_VALUE_WRAPPERS:
            value, parent = parent, parent.parent()
        if parent is None:
            return None
        start = value.range().start.index
        kind = parent.kind()

        def holds_value(field: str) -> bool:
            field_node = parent.field(field)
            return field_node is not None and field_node.range().start.index == start

        if kind == "variable_declarator" and holds_value("value"):
            name = parent.field("name")
            if name is None or name.kind() != "identifier":
                return None  # `const { a } = ...` and `const [a] = ...` name no function
            statement = parent.parent() or parent
            outer = statement.parent()
            if outer is not None and outer.kind() == "export_statement":
                statement = outer
            return name.text(), statement, {}
        if kind in ("public_field_definition", "field_definition"):
            name = parent.field("name") or parent.field("property")
            if name is None:
                return None
            owner = self._enclosing_class(fn, language)
            return name.text(), parent, {"parent_class": owner} if owner else {}
        if kind == "pair" and holds_value("value"):
            key = parent.field("key")
            owner_node = parent.parent()
            if key is None or owner_node is None or owner_node.kind() != "object":
                return None
            owner = self._object_literal_owner_of_object(owner_node)
            if owner is None:
                return None
            return key.text().strip("'\""), parent, {
                "parent_class": owner, "object_literal": True,
            }
        if kind == "assignment_expression" and holds_value("right"):
            left = parent.field("left")
            target = "".join(left.text().split()) if left is not None else ""
            statement = parent.parent() if parent.parent() is not None else parent
            if statement.kind() != "expression_statement":
                statement = parent
            if target == "module.exports":
                return _default_export_name(filepath), statement, {"export": "default"}
            found = _EXPORTS_TARGET.match(target)
            if found:
                return found.group(1), statement, {}
            found = _PROTOTYPE_TARGET.match(target)
            if found:
                return found.group(2), statement, {"parent_class": found.group(1)}
            return None
        if kind == "export_statement" and any(c.kind() == "default" for c in parent.children()):
            return _default_export_name(filepath), parent, {"export": "default"}
        return None

    @staticmethod
    def _object_literal_owner_of_object(obj) -> Optional[str]:
        """`api` for the object in `const api = { ... }`, else None."""
        holder = obj.parent()
        if holder is not None and holder.kind() in _JS_VALUE_WRAPPERS:
            holder = holder.parent()
        if holder is None or holder.kind() != "variable_declarator":
            return None
        name = holder.field("name")
        return name.text() if name is not None and name.kind() == "identifier" else None

    def _extract_py_bound_lambdas(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph, ranges: List[Tuple[int, int]],
    ) -> int:
        """Nodes for Python lambdas assigned to a name: `handler = lambda e: run(e)`.

        Only at module or class level. A lambda inside a function belongs to it.
        """
        count = 0
        try:
            found = sg_node.find_all(kind="lambda")
        except Exception as e:
            logger.debug("ast-grep find_all failed for kind lambda: %s", e)
            return 0
        for lam in found:
            try:
                parent = lam.parent()
                if parent is None or parent.kind() != "assignment":
                    continue
                right = parent.field("right")
                left = parent.field("left")
                if (
                    right is None or left is None or left.kind() != "identifier"
                    or right.range().start.index != lam.range().start.index
                    or self._has_function_ancestor(lam, language)
                ):
                    continue
                statement = parent.parent()
                if statement is None or statement.kind() != "expression_statement":
                    statement = parent
                span = statement.range()
                ranges.append((span.start.index, span.end.index))
                metadata = self._function_metadata(lam, language)
                owner = self._enclosing_class(lam, language)
                if owner:
                    metadata["parent_class"] = owner
                self._add_function_node(
                    left.text(), span, lam, filepath, language, module_id, graph, metadata,
                )
                count += 1
            except Exception as e:
                logger.debug("Failed to extract lambda at %s: %s", filepath, e)
        return count

    def _add_module_body_node(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph, ranges: List[Tuple[int, int]], lines: int,
    ) -> int:
        """One `<top-level>` node for the calls that sit outside every function.

        `Deno.serve(async (req) => ...)`, `app = FastAPI()`, `describe(...)` and
        `if __name__ == "__main__": main()` call code, so a change to the callee reaches
        this file. The node spans the whole file and nothing can call it. A file with
        no such call gets no node.
        """
        if language not in MODULE_BODY_LANGUAGES:
            return 0
        merged: List[Tuple[int, int]] = []
        for a, b in sorted(ranges):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        starts = [a for a, _b in merged]

        def inside_a_function(index: int) -> bool:
            slot = bisect.bisect_right(starts, index) - 1
            return slot >= 0 and index < merged[slot][1]

        loose: Dict[str, int] = {}
        for kind, field_name in CALL_KINDS.get(language, ()):
            try:
                found = sg_node.find_all(kind=kind)
            except Exception as e:
                logger.debug("ast-grep call search failed for kind %s: %s", kind, e)
                continue
            for call in found:
                try:
                    index = call.range().start.index
                    if inside_a_function(index):
                        continue
                    name = normalize_call_text(
                        _language_callee(self._callee_text(call, field_name), language)
                    )
                except Exception as e:
                    logger.debug("Failed to read a module-level call: %s", e)
                    continue
                if name and (name not in loose or index < loose[name]):
                    loose[name] = index
        if not loose:
            return 0
        calls = sorted(loose, key=loose.__getitem__)[:MAX_CALLS_PER_FUNCTION]
        meta: Dict[str, Any] = {"calls": calls, "module_body": True}
        self._add_boundary_facts(
            meta, sg_node, language, include=lambda index: not inside_a_function(index)
        )
        func_id = self._make_id("func")
        graph.add_node(UniversalNode(
            id=func_id,
            name=MODULE_BODY_NAME,
            node_type=NodeType.FUNCTION,
            location=UniversalLocation(
                file_path=filepath, start_line=1, end_line=max(lines, 1), language=language,
            ),
            language=language,
            line_count=max(lines, 1),
            metadata=meta,
        ))
        graph.add_relationship(UniversalRelationship(
            id=self._make_rel_id(),
            source_id=module_id,
            target_id=func_id,
            relationship_type=RelationshipType.CONTAINS,
        ))
        return 1

    @staticmethod
    def _complexity(match, language: str) -> int:
        """Cyclomatic complexity: one plus the number of branch points in the body."""
        kinds = COMPLEXITY_KINDS.get(language)
        if not kinds:
            return 0
        try:
            found = match.find_all({"rule": {"any": [{"kind": k} for k in kinds]}})
        except Exception as e:
            logger.debug("Complexity search failed: %s", e)
            return 0
        return 1 + len(found)

    @staticmethod
    def _docstring(match, language: str) -> Optional[str]:
        """The leading string literal of a Python function body, without its quotes."""
        if language != "python":
            return None
        try:
            body = match.field("body")
            children = body.children() if body else []
            first = children[0] if children else None
            if first is None or first.kind() != "expression_statement":
                return None
            inner = first.children()
            if len(inner) != 1 or inner[0].kind() != "string":
                return None
            text = inner[0].text().strip()
        except Exception as e:
            logger.debug("Docstring read failed: %s", e)
            return None
        # f-strings and bytes literals are not docstrings: Python sets __doc__ to None.
        if text[:1] in "fFbB" or text[:2].lower() in ("rf", "fr", "rb", "br"):
            return None
        text = text.lstrip("rRuU")
        for quote in ('"""', "'''", '"', "'"):
            if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
                text = text[len(quote):-len(quote)]
                break
        text = "\n".join(line.strip() for line in text.strip().splitlines())
        return text[:MAX_DOCSTRING_CHARS] or None

    def _function_metadata(
        self, match, language: str, scope: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Raw call names and enclosing class for one function node.

        The call resolver (indexing/call_resolver.py) turns these into CALLS
        edges once every file is in the graph, because a callee can live in a
        file that is parsed later.
        """
        meta: Dict[str, Any] = {}
        calls = self._extract_calls(match, language)
        if language == "python" and match.kind() == "function_definition":
            decorators = self._py_decorators(match)
            if decorators:
                # A decorator calls its callee when the module loads, but the function it
                # decorates is the one that a change to the decorator reaches.
                meta["decorators"] = decorators
                calls = list(dict.fromkeys(decorators + calls))
            if any(d.rsplit(".", 1)[-1] == "fixture" for d in decorators):
                meta["fixture"] = True
            name = match.field("name")
            if meta.get("fixture") or (name is not None and name.text().startswith("test")):
                params = self._py_param_names(match)
                if params:
                    meta["params"] = params  # a test names its fixtures by parameter
        self._add_boundary_facts(meta, match, language)
        if calls:
            meta["calls"] = calls
            if language == "cpp":
                scoped = self._scoped_calls(match)
                if scoped:
                    meta["scoped_calls"] = scoped
        parent = self._enclosing_class(match, language)
        if language == "go" and match.kind() == "method_declaration":
            parent = self._go_receiver_type(match)
        if scope and not parent:
            # `void Widget::Paint() {` out of line: a method of `Widget`. The parser
            # cannot tell a class scope from a namespace scope.
            parent = _GENERIC_GROUP.sub("", scope[-1]).strip() or None
        if parent:
            meta["parent_class"] = parent
        return meta

    @staticmethod
    def _add_boundary_facts(
        meta: Dict[str, Any], sg_node, language: str,
        include: Optional[Callable[[int], bool]] = None,
    ) -> None:
        """Store the boundaries that the code of `sg_node` uses or serves (ADR-026)."""
        uses, serves = collect_facts(sg_node, language, include)
        if language == "python" and sg_node.kind() == "function_definition":
            serves = python_route_serves(sg_node) + serves
        if language in ("javascript", "typescript") and sg_node.kind() == "method_definition":
            serves = nest_route_serves(sg_node) + serves
        if uses:
            meta["boundary_uses"] = uses
        if serves:
            meta["boundary_serves"] = serves

    @staticmethod
    def _py_decorators(match) -> List[str]:
        """Normalised names of the decorators above a Python function, top first.

        `@app.get("/x")` is `app.get`, `@retry(3)` is `retry`, `@cache` is `cache`.
        """
        parent = match.parent()
        if parent is None or parent.kind() != "decorated_definition":
            return []
        names: List[str] = []
        for child in parent.children():
            if child.kind() != "decorator":
                continue
            expr = next((c for c in child.children() if c.is_named()), None)
            if expr is None:
                continue
            if expr.kind() == "call":
                callee = expr.field("function")
                text = callee.text() if callee is not None else ""
            else:
                text = expr.text()
            name = normalize_call_text(text)
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _py_param_names(match) -> List[str]:
        """Parameter names of a Python function, without `self`, `cls`, `*args`, `**kw`."""
        params = match.field("parameters")
        if params is None:
            return []
        names: List[str] = []
        for child in params.children():
            kind = child.kind()
            if kind == "identifier":
                name = child.text()
            elif kind == "default_parameter":
                field = child.field("name")
                name = field.text() if field is not None else ""
            elif kind in ("typed_parameter", "typed_default_parameter"):
                field = child.field("name") or next(
                    (c for c in child.children() if c.kind() == "identifier"), None
                )
                name = field.text() if field is not None else ""
            else:
                continue
            if name and name not in ("self", "cls"):
                names.append(name)
        return names

    def _extract_calls(self, match, language: str) -> List[str]:
        """Distinct normalised callee names inside a function, in source order."""
        seen: Dict[str, None] = {}
        for kind, field_name in CALL_KINDS.get(language, ()):
            try:
                found = match.find_all(kind=kind)
            except Exception as e:
                logger.debug("ast-grep call search failed for kind %s: %s", kind, e)
                continue
            for call in found:
                try:
                    text = self._callee_text(call, field_name)
                except Exception as e:
                    logger.debug("Failed to read callee: %s", e)
                    continue
                name = normalize_call_text(_language_callee(text, language))
                if name:
                    seen.setdefault(name, None)
                if len(seen) >= MAX_CALLS_PER_FUNCTION:
                    return list(seen)
        return list(seen)

    @staticmethod
    def _scoped_calls(match) -> List[str]:
        """C++ calls through `::` (`base::Bind()`, `Widget::Create()`), normalised.

        normalize_call_text() turns `::`, `.` and `->` into dots. The resolver needs to
        know which calls were `ns::f()`: such a call names a free function or a static
        method, never a method on an object of unknown type.
        """
        seen: Dict[str, None] = {}
        try:
            for call in match.find_all(kind="call_expression"):
                callee = call.field("function")
                if callee and callee.kind() == "qualified_identifier":
                    name = normalize_call_text(_language_callee(callee.text(), "cpp"))
                    if name:
                        seen.setdefault(name, None)
                if len(seen) >= MAX_CALLS_PER_FUNCTION:
                    break
        except Exception as e:
            logger.debug("Scoped call search failed: %s", e)
        return list(seen)

    @staticmethod
    def _callee_text(call, field_name) -> str:
        if field_name == FIRST_NAMED_CHILD:
            named = [c for c in call.children() if c.is_named()]
            return named[0].text() if named else ""
        if isinstance(field_name, str):
            callee = call.field(field_name)
            if not callee:
                return ""
            # A long call chain (`a.m0().m1()...`) makes every callee text the whole
            # prefix, so reading it is quadratic. Such a callee is dropped anyway.
            span = callee.range()
            if span.end.index - span.start.index > 4 * MAX_CALL_TEXT:
                return ""
            return callee.text()
        # Java method_invocation, PHP member and scoped calls: receiver (optional) + name
        receiver_field, name_field = field_name or ("object", "name")
        name = call.field(name_field)
        if not name:
            return ""
        obj = call.field(receiver_field)
        return f"{obj.text()}.{name.text()}" if obj else name.text()

    @staticmethod
    def _go_receiver_type(match) -> Optional[str]:
        """`func (s *Server) Run()` -> `Server`."""
        receiver = match.field("receiver")
        if not receiver:
            return None
        text = re.sub(r"\[[^\[\]]*\]", "", receiver.text().strip())  # `Box[T]` -> `Box`
        found = re.findall(r"([A-Za-z_]\w*)\s*\)\s*$", text)
        return found[0] if found else None

    @staticmethod
    def _enclosing_class(match, language: str) -> Optional[str]:
        """Name of the nearest class-like ancestor, or None for a free function."""
        kinds = dict(CLASS_ANCESTOR_KINDS.get(language, ()))
        if not kinds:
            return None
        try:
            for ancestor in match.ancestors():
                field_name = kinds.get(ancestor.kind())
                if field_name:
                    name = ancestor.field(field_name)
                    if name:
                        text = _GENERIC_GROUP.sub("", " ".join(name.text().split()))
                        if language == "cpp":  # `class Outer::Inner {` is `Inner`
                            text = split_cpp_scope(text)[-1].strip()
                        return text[:80]
        except Exception as e:
            logger.debug("Failed to read enclosing class: %s", e)
        return None

    def _extract_classes(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph,
    ) -> int:
        """Extract class definitions from ast-grep node.

        Kind-based for the same reason as _extract_functions: the old
        "class $NAME: $$$BODY" pattern matched only base-less classes, so
        every `class Foo(Base):` was missing from the graph.
        """
        count = 0
        lang_kinds = CLASS_KINDS.get(language)
        if not lang_kinds:
            return 0

        matches = []
        for kind in lang_kinds:
            try:
                matches.extend(sg_node.find_all(kind=kind))
            except Exception as e:
                logger.debug("ast-grep find_all failed for kind %s: %s", kind, e)

        for match in matches:
            try:
                rng = match.range()
                if language in _C_FAMILY:
                    class_name = self._c_class_name(match)
                    if not class_name:
                        continue
                else:
                    name_text = match.field("name")
                    class_name = (
                        name_text.text() if name_text else f"class_{self._node_counter}"
                    )

                class_id = self._make_id("class")
                class_node = UniversalNode(
                    id=class_id,
                    name=class_name,
                    node_type=NodeType.CLASS,
                    location=UniversalLocation(
                        file_path=filepath,
                        start_line=rng.start.line + 1,
                        end_line=rng.end.line + 1,
                        language=language,
                    ),
                    language=language,
                    line_count=rng.end.line - rng.start.line + 1,
                )
                bases = self._class_bases(match, language)
                if bases:
                    class_node.metadata["bases"] = bases
                graph.add_node(class_node)

                graph.add_relationship(UniversalRelationship(
                    id=self._make_rel_id(),
                    source_id=module_id,
                    target_id=class_id,
                    relationship_type=RelationshipType.CONTAINS,
                ))
                count += 1
            except Exception as e:
                logger.debug("Failed to extract class at %s: %s", filepath, e)
                continue

        return count

    @staticmethod
    def _class_bases(match, language: str) -> List[str]:
        """The names of the base classes of a Python, JavaScript or TypeScript class.

        A dotted base keeps its last part (`models.Field` is `Field`), because the graph
        names classes without their module. A keyword argument (`metaclass=Meta`) and a
        computed base (`extends mix(A)`) give no name. TypeScript `implements` names count
        too: a class must define the methods of its interface. Other languages give [].
        """
        if language == "python":
            holder = match.field("superclasses")
            parts = holder.children() if holder else []
        elif language in ("javascript", "typescript", "tsx"):
            heritage = next((c for c in match.children() if c.kind() == "class_heritage"), None)
            if heritage is None:
                return []
            parts = []
            for child in heritage.children():
                if child.kind() in ("extends_clause", "implements_clause"):
                    parts.extend(child.children())
                else:
                    parts.append(child)  # JavaScript: `extends Base` has no clause node
        else:
            return []
        names: List[str] = []
        for part in parts:
            kind = part.kind()
            if kind == "subscript":  # Generic[T]
                part = part.field("value") or part
                kind = part.kind()
            if kind in ("identifier", "type_identifier", "attribute", "member_expression"):
                name = part.text().rsplit(".", 1)[-1].strip()
                if name and name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _c_class_name(match) -> Optional[str]:
        """The name of a C or C++ struct, union or class definition, or None.

        `struct point p;` and `class Foo;` name a type but do not define it: no body, no
        node. `typedef struct { ... } anon_t;` takes the typedef name. `class Outer::Inner`
        is `Inner`, and template arguments are dropped, as for the parent of a method.
        """
        if not match.field("body"):
            return None
        name = match.field("name")
        if name:
            text = split_cpp_scope(" ".join(name.text().split()))[-1]
            return _GENERIC_GROUP.sub("", text).strip() or None
        parent = match.parent()
        if parent is not None and parent.kind() == "type_definition":
            declarator = parent.field("declarator")
            if declarator and declarator.kind() == "type_identifier":
                return declarator.text()
        return None

    def _extract_imports(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph,
    ) -> None:
        """Extract import statements and add relationships."""
        patterns = {
            "python": ["import $NAME", "from $NAME import $$$ITEMS"],
            "javascript": ["import $$$ITEMS from '$NAME'"],
            "typescript": ["import $$$ITEMS from '$NAME'"],
            "go": ['import "$NAME"'],
            "java": ["import $NAME"],
            "rust": ["use $NAME"],
        }

        imported = self._collect_import_names(sg_node, language)

        lang_patterns = patterns.get(language, [])
        for pattern in lang_patterns:
            try:
                matches = sg_node.find_all(pattern=pattern)
                for match in matches:
                    name_text = match.get_match("NAME")
                    if name_text:
                        import_name = name_text.text()
                        imported.setdefault(import_name.strip("'\""), None)
                        target_id = f"module:{import_name}"
                        graph.add_relationship(UniversalRelationship(
                            id=self._make_rel_id(),
                            source_id=module_id,
                            target_id=target_id,
                            relationship_type=RelationshipType.IMPORTS,
                        ))
            except Exception as e:
                logger.debug("Import match failed in %s: %s", filepath, e)
                continue

        module = graph.nodes.get(module_id)
        if module is not None and imported:
            module.metadata["imports"] = list(imported)
        if module is not None and language in ("javascript", "typescript"):
            try:
                bindings, reexports = _js_import_bindings(sg_node)
            except Exception as e:
                logger.debug("Import binding collection failed in %s: %s", filepath, e)
                bindings, reexports = {}, []
            if bindings:
                module.metadata["import_bindings"] = bindings
            if reexports:
                module.metadata["reexports"] = reexports

    @staticmethod
    def _collect_import_names(sg_node, language: str) -> Dict[str, None]:
        """Imported module names by node kind, for the call resolver.

        Kind-based because the string patterns above miss double-quoted
        TypeScript imports and relative Python imports. Order is kept.
        """
        names: Dict[str, None] = {}
        try:
            if language == "python":
                for node in sg_node.find_all(kind="import_statement"):
                    for dotted in node.find_all(kind="dotted_name"):
                        names.setdefault(dotted.text(), None)
                for node in sg_node.find_all(kind="import_from_statement"):
                    module = node.field("module_name")
                    if module:
                        names.setdefault(module.text(), None)
            elif language in ("javascript", "typescript"):
                for node in sg_node.find_all(kind="import_statement"):
                    source = node.field("source")
                    if source:
                        names.setdefault(source.text().strip("'\"`"), None)
                # `export { a } from './x'`, `require('./x')` and `import('./x')` read the
                # module too.
                for node in sg_node.find_all(kind="export_statement"):
                    source = node.field("source")
                    if source:
                        names.setdefault(source.text().strip("'\"`"), None)
                for call in sg_node.find_all(kind="call_expression"):
                    required = _js_require_source(call)
                    if required:
                        names.setdefault(required, None)
            elif language in _C_FAMILY:
                # `#include "base/util.h"` imports the module path `base/util`: the
                # header and its `.c` or `.cc` file. `<stdio.h>` is a system header.
                for node in sg_node.find_all(kind="preproc_include"):
                    path = node.field("path")
                    if path and path.kind() == "string_literal":
                        target = posixpath.splitext(path.text().strip('"'))[0]
                        if target:
                            names.setdefault(target, None)
            elif language == "php":
                # `use App\Support\Helper;` -> `Support/Helper`: the PSR-4 root (`App`) is
                # often not a folder name, so only the last two parts must match the path.
                for clause in sg_node.find_all(kind="namespace_use_clause"):
                    named = [c for c in clause.children() if c.is_named()]
                    if not named:
                        continue
                    parts = [p for p in named[0].text().split("\\") if p]
                    if parts:
                        names.setdefault("/".join(parts[-2:]), None)
        except Exception as e:
            logger.debug("Import name collection failed: %s", e)
        return names

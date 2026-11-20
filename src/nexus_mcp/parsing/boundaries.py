"""Boundary facts: the places where one file talks to another without a call.

A call edge cannot show that `supabase.functions.invoke("send-receipt")` runs the edge
function in `supabase/functions/send-receipt/`, that `fetch("/orders", {method: "POST"})`
reaches the handler of `POST /orders`, or that `.from("orders")` reads a SQL table. This
module reads these facts from the ast-grep tree of one file. The resolver
(`indexing/call_resolver.py`) joins the two sides after the last file is parsed.

A fact is a small dict with a `kind` (`function`, `route`, `table`), a `key` and a `line`.
Precision comes first: a string that is not a literal gives no fact, and the resolver adds
an edge only when exactly one provider exists for the key.

The module has no dependency on the parser, so the parser can import it.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

KIND_FUNCTION = "function"
KIND_ROUTE = "route"
KIND_TABLE = "table"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

CALL_KIND = {
    "python": "call",
    "javascript": "call_expression",
    "typescript": "call_expression",
}

# A receiver is judged by its last name. Only these names hold an HTTP client or a server
# object often enough to be worth a fact. `get("/x")` on any other object gives nothing.
_CLIENT_RECEIVER = re.compile(
    r"(^|\.)(axios|ky|got|http|https|httpx|requests|session|client|api|request|superagent"
    r"|\w*[Cc]lient|\w*[Aa]pi|\w*[Hh]ttp)$"
)
_SERVER_RECEIVER = re.compile(
    r"(^|\.)(app|router|server|route|routes|fastify|hono|\w*[Rr]outer|\w*[Aa]pp)$"
)

_PY_STRING = re.compile(r"""^[A-Za-z]{0,2}(\"\"\"|'''|"|')(.*)\1$""", re.DOTALL)
_TEMPLATE_HOLE = re.compile(r"\$\{[^}]*\}")
_PARAM_SEGMENT = re.compile(r"^(<[^>]+>|\{[^}]*\}|:[A-Za-z_]\w*|\[[^\]]+\])$")
_FUNCTIONS_URL = re.compile(r"/functions/v1/([A-Za-z0-9_-]+)")
_METHOD_OPTION = re.compile(r"""\bmethod\s*:\s*["'`]([A-Za-z]+)["'`]""")
_QUOTED = re.compile(r"""["']([A-Za-z]+)["']""")
# A client URL that starts with a base-URL variable: `${BASE}/orders`, `f"{base}/orders"`.
_OPEN_PATH = re.compile(r"^(?:\{[^}]*\})+(/.*)$", re.DOTALL)
_FETCH_NAMES = ("fetch", "$fetch", "ofetch")

MAX_FACTS_PER_NODE = 20


def string_value(node: Any) -> Optional[str]:
    """The text of a string literal node, or None when the argument is not a literal.

    A JS template string keeps its holes as `{}`; a Python f-string keeps `{name}`.
    """
    if node is None:
        return None
    kind = node.kind()
    text = node.text()
    if kind in ("string", "concatenated_string") and len(text) >= 2:
        if text[0] in "\"'" and text[-1] == text[0]:
            return text[1:-1]
        match = _PY_STRING.match(text)
        return match.group(2) if match else None
    if kind == "template_string" and len(text) >= 2 and text[0] == "`":
        return _TEMPLATE_HOLE.sub("{}", text[1:-1])
    return None


def normalize_path(path: str) -> str:
    """A route path with its parameters named `{}`: `/orders/<int:id>` is `/orders/{}`.

    Returns "" for anything that is not a path of this server (no leading slash, a URL
    with a host). The query string and the trailing slash are dropped.
    """
    path = path.split("?", 1)[0].split("#", 1)[0].strip()
    if not path.startswith("/") or path.startswith("//"):
        return ""
    segments: List[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if _PARAM_SEGMENT.match(segment) or "{" in segment:
            segments.append("{}")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def route_key(method: str, path: str) -> str:
    """`POST /orders/{}`, or "" when the path is not a route."""
    clean = normalize_path(path)
    return f"{method.upper()} {clean}" if clean else ""


def open_route_key(method: str, path: str) -> str:
    """`GET {}/orders` for a client URL that starts with a variable, or "".

    The base URL (`${BASE}/orders`) is not known, so the key does not equal the key of any
    route. Only the join across linked repos reads it (ADR-030): inside one repo it gives no
    edge, as a URL that starts with a host gives none. The rest of the path needs a literal
    segment, because `${base}/${id}` fits every route.
    """
    found = _OPEN_PATH.match(path.split("?", 1)[0].split("#", 1)[0].strip())
    if not found:
        return ""
    rest = normalize_path(found.group(1))
    if not any(segment not in ("", "{}") for segment in rest.split("/")):
        return ""
    return f"{method.upper()} {{}}{rest}"


def _client_key(method: str, path: str) -> str:
    """The route key of a client URL: a path of this server, or one with an open start."""
    return route_key(method, path) or open_route_key(method, path)


def _arguments(call: Any) -> List[Any]:
    args = call.field("arguments")
    if args is None:
        return []
    return [c for c in args.children() if c.is_named()]


def _callee(call: Any) -> str:
    fn = call.field("function")
    return fn.text() if fn is not None else ""


def _dotted(text: str) -> str:
    """`supabase.from("x").select` is not a plain receiver; keep dotted names only."""
    return text if re.fullmatch(r"[\w$.]+", text) else ""


def _fetch_method(args: Sequence[Any]) -> str:
    if len(args) >= 2:
        found = _METHOD_OPTION.search(args[1].text())
        if found and found.group(1).lower() in HTTP_METHODS:
            return found.group(1).upper()
    return "GET"


def _function_from_url(value: str) -> str:
    found = _FUNCTIONS_URL.search(value)
    return found.group(1) if found else ""


def _use_from_call(call: Any, language: str) -> Optional[Tuple[str, str]]:
    """(kind, key) of the boundary that a call crosses, or None."""
    callee = _dotted(_callee(call))
    if not callee:
        return None
    args = _arguments(call)
    first = string_value(args[0]) if args else None
    if first is None:
        return None
    last = callee.rsplit(".", 1)[-1]
    if callee.endswith("functions.invoke") and first:
        return KIND_FUNCTION, first
    if language in ("javascript", "typescript"):
        if callee in _FETCH_NAMES:
            hosted = _function_from_url(first)
            if hosted:
                return KIND_FUNCTION, hosted
            key = _client_key(_fetch_method(args), first)
            return (KIND_ROUTE, key) if key else None
        if last == "from" and callee != "Array.from" and re.fullmatch(r"\w+", first):
            return KIND_TABLE, first
    else:
        if last in ("table", "from_") and re.fullmatch(r"\w+", first):
            return KIND_TABLE, first
    if last in HTTP_METHODS and _CLIENT_RECEIVER.search(callee.rsplit(".", 1)[0] or "-"):
        key = _client_key(last, first)
        return (KIND_ROUTE, key) if key else None
    return None


def _handler_name(args: Sequence[Any]) -> Optional[str]:
    """The identifier that an Express-style route names as its handler, if the last
    argument is one. An inline function has no name: the enclosing node serves."""
    if len(args) < 2:
        return None
    tail = args[-1]
    if tail.kind() == "identifier":
        return tail.text()
    return None


def _serve_from_call(call: Any, language: str) -> Optional[Tuple[str, Optional[str]]]:
    """(route key, handler name) of `app.post("/orders", handler)`, or None."""
    if language not in ("javascript", "typescript"):
        return None
    callee = _dotted(_callee(call))
    if not callee or "." not in callee:
        return None
    receiver, verb = callee.rsplit(".", 1)
    if verb not in HTTP_METHODS or not _SERVER_RECEIVER.search(receiver):
        return None
    args = _arguments(call)
    first = string_value(args[0]) if args else None
    if first is None or len(args) < 2:
        return None
    key = route_key(verb, first)
    return (key, _handler_name(args)) if key else None


def collect_facts(
    sg_node: Any, language: str, include: Optional[Callable[[int], bool]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(uses, serves) found in the calls under `sg_node`.

    `include(index)` keeps only the calls that start at an accepted byte index, so the
    module-body node can take the calls that sit outside every function.
    """
    kind = CALL_KIND.get(language)
    if kind is None:
        return [], []
    uses: Dict[Tuple[str, str], Dict[str, Any]] = {}
    serves: Dict[Tuple[str, str], Dict[str, Any]] = {}
    try:
        calls = sg_node.find_all(kind=kind)
    except Exception:  # noqa: BLE001 - a fact is a bonus, never a parse failure
        return [], []
    for call in calls:
        try:
            if include is not None and not include(call.range().start.index):
                continue
            line = call.range().start.line + 1
            served = _serve_from_call(call, language)
            if served is not None:
                key, handler = served
                serves.setdefault((KIND_ROUTE, key), {
                    "kind": KIND_ROUTE, "key": key, "line": line, "handler": handler,
                })
                continue
            used = _use_from_call(call, language)
            if used is not None:
                uses.setdefault(used, {"kind": used[0], "key": used[1], "line": line})
        except Exception:  # noqa: BLE001
            continue
        if len(uses) + len(serves) >= MAX_FACTS_PER_NODE:
            break
    return list(uses.values()), list(serves.values())


_PY_ROUTER_FACTORIES = ("APIRouter", "Router", "Blueprint")
_PY_MOUNT_METHODS = ("include_router", "register_blueprint")
_PY_PREFIX_KEYWORDS = ("prefix", "url_prefix")


def _keyword_value(call: Any, names: Sequence[str]) -> Optional[Any]:
    """The value node of `name=value` in a Python call, for the first of `names` found."""
    args = call.field("arguments")
    if args is None:
        return None
    for arg in args.children():
        if arg.kind() != "keyword_argument":
            continue
        key = arg.field("name")
        if key is not None and key.text() in names:
            return arg.field("value")
    return None


def _join_paths(*parts: str) -> str:
    """The parts joined with one slash between them: `/api` + `/orders` + `{id}`."""
    kept = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(kept) if kept else ""


def _literal_prefix(call: Any) -> str:
    """The `prefix=` or `url_prefix=` of a router call, or "" when it is no literal.

    A prefix that the file does not give as a literal is not guessed: the route keeps the
    path of its decorator.
    """
    value = _keyword_value(call, _PY_PREFIX_KEYWORDS)
    text = string_value(value) if value is not None else None
    return text or ""


def _module_root(node: Any) -> Any:
    while node.parent() is not None:
        node = node.parent()
    return node


_TABLES: Tuple[int, Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]] = (0, ({}, {}))


def _prefix_tables(root: Any) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    """(own prefix of each router variable, (parent, prefix) of each mounted router).

    The tables belong to a module, and the parser asks for them once for each decorated
    function, so the last module is kept: a file with 1,000 routes reads its tree once.
    """
    global _TABLES
    key = hash(root.text())
    if _TABLES[0] == key:
        return _TABLES[1]
    own: Dict[str, str] = {}
    mounts: Dict[str, Tuple[str, str]] = {}
    for assign in root.find_all(kind="assignment"):
        left, right = assign.field("left"), assign.field("right")
        if left is None or right is None or left.kind() != "identifier":
            continue
        if right.kind() == "call" and _callee(right).rsplit(".", 1)[-1] in _PY_ROUTER_FACTORIES:
            own[left.text()] = _literal_prefix(right)
    for call in root.find_all(kind="call"):
        callee = _dotted(_callee(call))
        if "." not in callee or callee.rsplit(".", 1)[-1] not in _PY_MOUNT_METHODS:
            continue
        args = _arguments(call)
        if args and args[0].kind() == "identifier":
            mounts[args[0].text()] = (callee.rsplit(".", 1)[0], _literal_prefix(call))
    _TABLES = (key, (own, mounts))
    return _TABLES[1]


def _python_prefix(root: Any, variable: str) -> str:
    """The prefix of all routes of a router variable that this file sets.

    The router call (`APIRouter(prefix="/orders")`, `Blueprint(..., url_prefix="/x")`) and a
    mount in the same file (`app.include_router(router, prefix="/api")`) add their prefixes.
    A prefix that another file sets is not seen: the join across repos reads the end of the
    path (ADR-030).
    """
    own, mounts = _prefix_tables(root)
    parts = [own.get(variable, "")]
    seen = {variable}
    for _ in range(4):  # a router that mounts a router; the depth is bounded
        if variable not in mounts:
            break
        parent, prefix = mounts[variable]
        if parent in seen:
            break
        seen.add(parent)
        parts += [prefix, own.get(parent, "")]
        variable = parent
    return _join_paths(*reversed(parts))


def python_route_serves(function_node: Any) -> List[Dict[str, Any]]:
    """Routes that the decorators of a Python function register.

    `@app.get("/orders/{id}")` serves `GET /orders/{}`. `@app.route("/x", methods=["POST"])`
    serves `POST /x`; without `methods`, `route` serves `GET`. The prefix that the same file
    sets on the router counts: `APIRouter(prefix="/orders")` and `@router.get("/{id}")` serve
    `GET /orders/{}`.
    """
    parent = function_node.parent()
    if parent is None or parent.kind() != "decorated_definition":
        return []
    found: Dict[str, Dict[str, Any]] = {}
    root = None
    for decorator in parent.children():
        if decorator.kind() != "decorator":
            continue
        expr = next((c for c in decorator.children() if c.is_named()), None)
        if expr is None or expr.kind() != "call":
            continue
        callee = _dotted(_callee(expr))
        if "." not in callee:
            continue
        verb = callee.rsplit(".", 1)[-1]
        if verb not in HTTP_METHODS and verb not in ("route", "api_route"):
            continue
        args = _arguments(expr)
        path = string_value(args[0]) if args else None
        if path is None:
            continue
        receiver = callee.rsplit(".", 1)[0]
        if "." not in receiver:
            root = root or _module_root(function_node)
            path = _join_paths(_python_prefix(root, receiver), path) or ("/" if path else "")
        methods = [verb.upper()] if verb in HTTP_METHODS else ["GET"]
        if verb in ("route", "api_route"):
            for arg in args[1:]:
                if arg.kind() == "keyword_argument" and arg.text().startswith("methods"):
                    listed = [m.upper() for m in _QUOTED.findall(arg.text())]
                    methods = [m for m in listed if m.lower() in HTTP_METHODS] or methods
        for method in methods:
            key = route_key(method, path)
            if key:
                found.setdefault(key, {
                    "kind": KIND_ROUTE, "key": key,
                    "line": decorator.range().start.line + 1, "handler": None,
                })
    return list(found.values())


_NEST_VERBS = {
    "Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE",
    "Head": "HEAD", "Options": "OPTIONS",
}


def _decorator_call(decorator: Any) -> Optional[Tuple[str, str]]:
    """(name, path) of `@Name("path")`, `@Name()` and `@Name({ path: "x" })`.

    Returns None when the decorator is no call, or when its path is not a literal: a path
    that the file does not give is left out (precision first).
    """
    call = next((c for c in decorator.children() if c.kind() == "call_expression"), None)
    if call is None:
        return None
    fn = call.field("function")
    if fn is None or fn.kind() != "identifier":
        return None
    args = [c for c in (call.field("arguments").children() if call.field("arguments") else [])
            if c.is_named() and c.kind() != "comment"]
    if not args:
        return fn.text(), ""
    first = args[0]
    if first.kind() == "object":
        for pair in first.children():
            key = pair.field("key") if pair.kind() == "pair" else None
            if key is not None and key.text().strip("\"'`") == "path":
                text = string_value(pair.field("value"))
                return (fn.text(), text) if text is not None else None
        return fn.text(), ""
    text = string_value(first)
    return (fn.text(), text) if text is not None else None


def _nest_controller_path(cls: Any) -> Optional[str]:
    """The path of the `@Controller(...)` of a class, or None when it has none."""
    decorators = [c for c in cls.children() if c.kind() == "decorator"]
    owner = cls.parent()
    if owner is not None and owner.kind() == "export_statement":
        decorators += [c for c in owner.children() if c.kind() == "decorator"]
    for decorator in decorators:
        found = _decorator_call(decorator)
        if found is not None and found[0] == "Controller":
            return found[1]
    return None


def nest_route_serves(method_node: Any) -> List[Dict[str, Any]]:
    """Routes of a NestJS controller method: `@Controller("orders")` and `@Get(":id")` serve
    `GET /orders/{}`. A class with no `@Controller` call, or a path that is not a literal,
    serves nothing. `@All` has no method and is left out."""
    body = method_node.parent()
    if body is None or body.kind() != "class_body":
        return []
    cls = body.parent()
    prefix = _nest_controller_path(cls) if cls is not None else None
    if prefix is None:
        return []
    start = method_node.range().start.index
    pending: List[Any] = []
    for child in body.children():
        if child.kind() == "decorator":
            pending.append(child)
        elif child.range().start.index == start and child.kind() == "method_definition":
            break
        else:
            pending = []
    found: Dict[str, Dict[str, Any]] = {}
    for decorator in pending:
        call = _decorator_call(decorator)
        if call is None or call[0] not in _NEST_VERBS:
            continue
        key = route_key(_NEST_VERBS[call[0]], _join_paths(prefix, call[1]) or "/")
        if key:
            found.setdefault(key, {
                "kind": KIND_ROUTE, "key": key,
                "line": decorator.range().start.line + 1, "handler": None,
            })
    return list(found.values())

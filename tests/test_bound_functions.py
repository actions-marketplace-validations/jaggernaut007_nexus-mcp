"""Tests for function coverage in Python and JS/TS beyond `def` and `function`.

Each test parses a real file with the real ast-grep parser. They cover the code that a
call graph usually misses: arrow functions bound to a name, object-literal members,
default exports, calls at module level, Python decorators, assigned lambdas and pytest
fixtures. A top-level `Deno.serve(...)` or `app = FastAPI()` is a call too, so a file
gets one `<top-level>` node that owns the calls outside every function.
"""

from nexus_mcp.core.graph_models import (
    MODULE_BODY_NAME,
    NodeType,
    UniversalGraph,
    is_module_body,
)
from nexus_mcp.core.models import SymbolType
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.parsing.treesitter_parser import TreeSitterParser


def _parse(tmp_path, relpath, source):
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)
    return graph


def _functions(graph):
    out = {}
    for n in graph.nodes.values():
        if n.node_type == NodeType.FUNCTION:
            out.setdefault(n.name, []).append(n)
    return out


def _one(graph, name):
    found = _functions(graph).get(name, [])
    assert len(found) == 1, f"{name}: {len(found)} nodes in {sorted(_functions(graph))}"
    return found[0]


class TestJsBoundFunctions:
    def test_extract_js_bound_functions_export_const_arrow(self, tmp_path):
        graph = _parse(
            tmp_path, "svc.ts",
            "export const handler = async (req: Request) => {\n  send(req);\n};\n",
        )
        node = _one(graph, "handler")
        assert node.metadata["calls"] == ["send"]
        assert "parent_class" not in node.metadata
        assert (node.location.start_line, node.location.end_line) == (1, 3)

    def test_extract_js_bound_functions_function_expression_and_exports_assignment(
        self, tmp_path
    ):
        graph = _parse(
            tmp_path, "m.js",
            "const f = function () { a(); };\n"
            "module.exports.g = function () { b(); };\n"
            "exports.h = () => { c(); };\n",
        )
        assert _one(graph, "f").metadata["calls"] == ["a"]
        assert _one(graph, "g").metadata["calls"] == ["b"]
        assert _one(graph, "h").metadata["calls"] == ["c"]

    def test_extract_js_bound_functions_default_export_named_from_index_dir(self, tmp_path):
        graph = _parse(
            tmp_path, "supabase/functions/send-email/index.ts",
            "export default async (req) => {\n  go(req);\n};\n",
        )
        node = _one(graph, "send-email")
        assert node.metadata["export"] == "default"
        assert node.metadata["calls"] == ["go"]

    def test_extract_js_bound_functions_default_export_named_from_file_stem(self, tmp_path):
        graph = _parse(tmp_path, "pricing.ts", "export default (x) => total(x);\n")
        assert _one(graph, "pricing").metadata["export"] == "default"

    def test_extract_js_bound_functions_anonymous_default_function_gets_a_name(self, tmp_path):
        graph = _parse(tmp_path, "run.ts", "export default function (a) { go(a); }\n")
        node = _one(graph, "run")
        assert node.metadata["export"] == "default"

    def test_extract_js_bound_functions_named_default_function_is_marked(self, tmp_path):
        graph = _parse(tmp_path, "run.ts", "export default function start(a) { go(a); }\n")
        assert _one(graph, "start").metadata["export"] == "default"

    def test_extract_js_bound_functions_class_field_arrow_has_parent(self, tmp_path):
        graph = _parse(
            tmp_path, "a.ts",
            "class Widget {\n  handle = () => { go(); };\n  other() { stop(); }\n}\n",
        )
        assert _one(graph, "handle").metadata["parent_class"] == "Widget"
        assert _one(graph, "other").metadata["parent_class"] == "Widget"

    def test_extract_js_bound_functions_object_literal_members_have_parent(self, tmp_path):
        graph = _parse(
            tmp_path, "api.ts",
            "export const api = {\n  get() { a(); },\n  post: () => { b(); },\n};\n",
        )
        for name, call in (("get", "a"), ("post", "b")):
            node = _one(graph, name)
            assert node.metadata["parent_class"] == "api"
            assert node.metadata["object_literal"] is True
            assert node.metadata["calls"] == [call]

    def test_extract_js_bound_functions_callback_object_member_is_not_a_node(self, tmp_path):
        graph = _parse(tmp_path, "a.ts", "use({ onOpen() { a(); } });\n")
        assert "onOpen" not in _functions(graph)

    def test_extract_js_bound_functions_nested_arrow_is_not_a_node(self, tmp_path):
        graph = _parse(
            tmp_path, "a.ts",
            "function outer() {\n  const inner = () => { deep(); };\n  inner();\n}\n",
        )
        assert "inner" not in _functions(graph)
        assert set(_one(graph, "outer").metadata["calls"]) == {"deep", "inner"}

    def test_extract_js_bound_functions_no_duplicate_for_a_function_declaration(
        self, tmp_path
    ):
        graph = _parse(tmp_path, "a.ts", "export function f() { g(); }\n")
        assert len(_functions(graph)["f"]) == 1

    def test_extract_js_bound_functions_anonymous_callback_is_not_a_node(self, tmp_path):
        graph = _parse(tmp_path, "a.ts", "items.map((x) => go(x));\n")
        names = set(_functions(graph))
        assert names == {MODULE_BODY_NAME}

    def test_parse_file_tsx_with_jsx_finds_the_component_and_its_calls(self, tmp_path):
        graph = _parse(
            tmp_path, "Checkout.tsx",
            "export const Checkout = () => {\n"
            "  const cart = useCart();\n"
            "  return <div>{format(cart)}</div>;\n"
            "};\n",
        )
        assert set(_one(graph, "Checkout").metadata["calls"]) == {"useCart", "format"}


class TestModuleBody:
    def test_add_module_body_node_collects_deno_serve_calls(self, tmp_path):
        graph = _parse(
            tmp_path, "fn/index.ts",
            "import { send } from './send';\n"
            "Deno.serve(async (req) => {\n  return send(req);\n});\n",
        )
        body = _one(graph, MODULE_BODY_NAME)
        assert body.metadata["module_body"] is True
        assert set(body.metadata["calls"]) == {"Deno.serve", "send"}
        assert is_module_body(body)

    def test_add_module_body_node_skips_calls_inside_a_function(self, tmp_path):
        graph = _parse(
            tmp_path, "a.ts",
            "const x = start();\nexport const f = () => { inside(); };\n",
        )
        assert _one(graph, MODULE_BODY_NAME).metadata["calls"] == ["start"]
        assert _one(graph, "f").metadata["calls"] == ["inside"]

    def test_add_module_body_node_none_for_a_file_with_no_loose_calls(self, tmp_path):
        graph = _parse(tmp_path, "a.ts", "export function f() { g(); }\n")
        assert MODULE_BODY_NAME not in _functions(graph)

    def test_add_module_body_node_python_module_level_call(self, tmp_path):
        graph = _parse(
            tmp_path, "app.py",
            "app = FastAPI()\n\n\ndef main():\n    run()\n\n\n"
            "if __name__ == '__main__':\n    main()\n",
        )
        body = _one(graph, MODULE_BODY_NAME)
        assert body.metadata["calls"] == ["FastAPI", "main"]
        assert _one(graph, "main").metadata["calls"] == ["run"]

    def test_add_module_body_node_other_languages_get_none(self, tmp_path):
        graph = _parse(tmp_path, "a.go", "package main\n\nfunc f() { g() }\n")
        assert MODULE_BODY_NAME not in _functions(graph)


class TestPythonBoundFunctions:
    def test_extract_py_bound_lambdas_assigned_lambda_is_a_function(self, tmp_path):
        graph = _parse(tmp_path, "a.py", "handler = lambda e: process(e)\n")
        assert _one(graph, "handler").metadata["calls"] == ["process"]

    def test_extract_py_bound_lambdas_class_level_lambda_has_parent(self, tmp_path):
        graph = _parse(tmp_path, "a.py", "class A:\n    key = lambda self: build(self)\n")
        node = _one(graph, "key")
        assert node.metadata["parent_class"] == "A"

    def test_extract_py_bound_lambdas_nested_lambda_is_not_a_node(self, tmp_path):
        graph = _parse(tmp_path, "a.py", "def f(xs):\n    g = lambda x: inner(x)\n    return g\n")
        assert "g" not in _functions(graph)
        assert "inner" in _one(graph, "f").metadata["calls"]

    def test_function_metadata_decorator_calls_belong_to_the_decorated_function(
        self, tmp_path
    ):
        graph = _parse(
            tmp_path, "api.py",
            "@rate_limit(max_calls=20)\n@app.get('/x')\ndef post_order():\n    return create()\n",
        )
        node = _one(graph, "post_order")
        assert node.metadata["decorators"] == ["rate_limit", "app.get"]
        assert node.metadata["calls"] == ["rate_limit", "app.get", "create"]
        # The decorators run at import, but their owner is the function, not the module.
        assert MODULE_BODY_NAME not in _functions(graph)

    def test_function_metadata_plain_decorator_is_a_call_of_the_function(self, tmp_path):
        graph = _parse(tmp_path, "a.py", "@cache\ndef f():\n    pass\n")
        assert _one(graph, "f").metadata["calls"] == ["cache"]

    def test_function_metadata_fixture_flag_and_params(self, tmp_path):
        graph = _parse(
            tmp_path, "conftest.py",
            "import pytest\n\n\n@pytest.fixture\ndef db(tmp_path):\n    return connect(tmp_path)\n",
        )
        node = _one(graph, "db")
        assert node.metadata["fixture"] is True
        assert node.metadata["params"] == ["tmp_path"]

    def test_function_metadata_test_function_params(self, tmp_path):
        graph = _parse(
            tmp_path, "test_a.py",
            "def test_x(db, tmp_path: str, n=1, *args, **kw):\n    assert db\n\n\n"
            "class TestY:\n    def test_z(self, db):\n        pass\n",
        )
        assert _one(graph, "test_x").metadata["params"] == ["db", "tmp_path", "n"]
        assert _one(graph, "test_z").metadata["params"] == ["db"]

    def test_function_metadata_plain_function_has_no_params_key(self, tmp_path):
        graph = _parse(tmp_path, "a.py", "def helper(a, b):\n    pass\n")
        assert "params" not in _one(graph, "helper").metadata
        assert "fixture" not in _one(graph, "helper").metadata


def _symbols(tmp_path, relpath, source):
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    parsed = TreeSitterParser().parse_file(str(path))
    assert parsed.error is None, parsed.error
    return {(s.name, s.parent): s for s in parsed.symbols if s.type != SymbolType.MODULE}


class TestTreeSitterSymbols:
    """Search finds a function only when it is a symbol, so the same rules apply here."""

    def test_extract_symbols_ts_export_const_arrow(self, tmp_path):
        symbols = _symbols(
            tmp_path, "svc.ts", "export const handler = async (req) => {\n  send(req);\n};\n"
        )
        sym = symbols[("handler", None)]
        assert sym.type == SymbolType.FUNCTION
        assert sym.signature.startswith("export const handler")
        assert (sym.line_start, sym.line_end) == (1, 3)
        assert "send" in sym.calls

    def test_extract_symbols_default_export_named_from_index_dir(self, tmp_path):
        symbols = _symbols(
            tmp_path, "supabase/functions/send-email/index.ts",
            "export default async (req) => {\n  go(req);\n};\n",
        )
        assert ("send-email", None) in symbols

    def test_extract_symbols_class_callback_not_named_after_a_parameter(self, tmp_path):
        symbols = _symbols(
            tmp_path, "a.ts",
            "class A {\n  run() {\n    items.map((item) => go(item));\n  }\n}\n",
        )
        assert set(symbols) == {("A", None), ("run", "A")}

    def test_extract_symbols_class_field_arrow_is_a_method(self, tmp_path):
        symbols = _symbols(tmp_path, "a.ts", "class A {\n  handle = () => go();\n}\n")
        sym = symbols[("handle", "A")]
        assert sym.type == SymbolType.METHOD

    def test_extract_symbols_object_literal_methods_are_qualified(self, tmp_path):
        symbols = _symbols(
            tmp_path, "api.ts",
            "export const api = {\n  get() { a(); },\n  post: () => { b(); },\n};\n",
        )
        assert {("get", "api"), ("post", "api")} <= set(symbols)

    def test_extract_symbols_callback_object_member_is_not_a_symbol(self, tmp_path):
        symbols = _symbols(tmp_path, "a.ts", "use({ onOpen() { a(); } });\n")
        assert ("onOpen", None) not in symbols and not symbols

    def test_extract_symbols_tsx_component(self, tmp_path):
        symbols = _symbols(
            tmp_path, "Checkout.tsx",
            "export const Checkout = () => {\n  return <div>{format(x)}</div>;\n};\n",
        )
        assert ("Checkout", None) in symbols
        assert "format" in symbols[("Checkout", None)].calls

    def test_extract_symbols_python_assigned_lambda(self, tmp_path):
        symbols = _symbols(
            tmp_path, "a.py",
            "handler = lambda e: process(e)\n\n\ndef f(xs):\n    g = lambda x: 1\n",
        )
        assert ("handler", None) in symbols
        assert ("g", None) not in symbols

    def test_extract_symbols_function_in_a_function_is_not_a_symbol(self, tmp_path):
        symbols = _symbols(
            tmp_path, "a.js",
            "function outer() {\n  const inner = () => 1;\n  return inner;\n}\n",
        )
        assert ("inner", None) not in symbols and ("outer", None) in symbols


def test_bound_function_rules_agree_between_the_graph_and_the_search_parser(tmp_path):
    """One naming rule: the same functions are graph nodes and search symbols."""
    source = (
        "export const handler = async (req) => { send(req); };\n"
        "export default (x) => x;\n"
        "const api = { get() { a(); }, post: () => { b(); } };\n"
        "class W { field = () => 1; real() {} }\n"
        "module.exports.g = function () {};\n"
        "X.prototype.proto = function () {};\n"
        "items.map((x) => go(x));\n"
        "use({ onOpen() { a(); } });\n"
        "function outer() { const inner = () => 1; return inner; }\n"
        "const {a} = obj;\n"
    )
    graph = _parse(tmp_path, "mix.ts", source)
    nodes = {
        (n.name, n.metadata.get("parent_class"))
        for n in graph.nodes.values()
        if n.node_type == NodeType.FUNCTION and not is_module_body(n)
    }
    symbols = {
        key for key, sym in _symbols(tmp_path, "mix.ts", source).items()
        if sym.type in (SymbolType.FUNCTION, SymbolType.METHOD)
    }
    assert nodes == symbols
    assert ("mix", None) in nodes and ("proto", "X") in nodes

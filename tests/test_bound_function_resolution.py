"""CALLS and USES edges for the code that bound functions, imports and fixtures reach.

Each test writes a small project to tmp_path, parses it with the real parsers and builds
the edges the way the pipeline does. A name from an import binds to that import: a
same-named function somewhere else never gets the edge.
"""

from nexus_mcp.core.graph_models import (
    MODULE_BODY_NAME,
    NodeType,
    RelationshipType,
    UniversalGraph,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _build(tmp_path, files):
    universal = UniversalGraph()
    parser = AstGrepParser()
    for relpath, source in files.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for relpath in sorted(files):
        parser.parse_file(str(tmp_path / relpath), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def _node(graph, name, file_hint=""):
    found = [
        n for n in graph.find_nodes_by_name(name)
        if n.node_type in (NodeType.FUNCTION, NodeType.CLASS)
        and file_hint in n.location.file_path
    ]
    assert len(found) == 1, f"{name} in {file_hint!r}: {len(found)} nodes"
    return found[0]


def _callers(graph, name, file_hint=""):
    return {
        (c.name, c.location.file_path.rsplit("/", 1)[-1])
        for c in graph.get_callers(_node(graph, name, file_hint).id)
    }


class TestJsImportBindings:
    def test_resolve_binding_default_import(self, tmp_path):
        graph = _build(tmp_path, {
            "a.ts": "export default function notify(x) { return x; }\n",
            "b.ts": "import send from './a';\nexport function run() { send(1); }\n",
        })
        assert _callers(graph, "notify") == {("run", "b.ts")}

    def test_resolve_binding_default_import_of_an_anonymous_arrow(self, tmp_path):
        graph = _build(tmp_path, {
            "pricing.ts": "export default (x) => x * 2;\n",
            "b.ts": "import total from './pricing';\nexport const run = () => total(1);\n",
        })
        assert _callers(graph, "pricing") == {("run", "b.ts")}

    def test_resolve_binding_aliased_named_import(self, tmp_path):
        graph = _build(tmp_path, {
            "a.ts": "export const format = (x) => x;\nexport const other = (x) => x;\n",
            "b.ts": "import { format as fmt } from './a';\nexport const run = () => fmt(1);\n",
        })
        assert _callers(graph, "format") == {("run", "b.ts")}
        assert _callers(graph, "other") == set()

    def test_resolve_binding_require_destructure_and_alias(self, tmp_path):
        graph = _build(tmp_path, {
            "a.js": "exports.first = () => 1;\nexports.second = () => 2;\n",
            "b.js": (
                "const { first, second: two } = require('./a');\n"
                "function run() { first(); two(); }\n"
            ),
        })
        assert _callers(graph, "first") == {("run", "b.js")}
        assert _callers(graph, "second") == {("run", "b.js")}

    def test_resolve_binding_import_from_a_wrong_module_gets_no_edge(self, tmp_path):
        graph = _build(tmp_path, {
            "a.ts": "export function parse() {}\n",
            "other.ts": "export function parse() {}\n",
            "b.ts": "import { parse } from './other';\nexport function run() { parse(); }\n",
        })
        assert _callers(graph, "parse", "a.ts") == set()
        assert _callers(graph, "parse", "other.ts") == {("run", "b.ts")}

    def test_resolve_binding_unresolved_import_does_not_fall_back_to_a_name(self, tmp_path):
        graph = _build(tmp_path, {
            "lib.ts": "export function parse() {}\n",
            "b.ts": "import { parse } from 'external-pkg';\nexport function run() { parse(); }\n",
        })
        assert _callers(graph, "parse") == set()

    def test_resolve_namespace_import_and_require(self, tmp_path):
        graph = _build(tmp_path, {
            "cart.ts": "export const empty = () => 1;\nexport const add = () => 2;\n",
            "b.ts": "import * as cart from './cart';\nexport const run = () => cart.empty();\n",
            "c.js": "const c = require('./cart');\nfunction go() { c.add(); }\n",
        })
        assert _callers(graph, "empty") == {("run", "b.ts")}
        assert _callers(graph, "add") == {("go", "c.js")}

    def test_resolve_plain_reexport_one_hop(self, tmp_path):
        graph = _build(tmp_path, {
            "impl.ts": "export const build = () => 1;\n",
            "index.ts": "export { build } from './impl';\n",
            "b.ts": "import { build } from './index';\nexport const run = () => build();\n",
        })
        assert _callers(graph, "build") == {("run", "b.ts")}

    def test_resolve_plain_reexport_star_and_alias(self, tmp_path):
        graph = _build(tmp_path, {
            "impl.ts": "export const build = () => 1;\nexport const make = () => 2;\n",
            "mid.ts": "export * from './impl';\n",
            "top.ts": "export { make as create } from './mid';\n",
            "b.ts": (
                "import { build } from './mid';\nimport { create } from './top';\n"
                "export const run = () => { build(); create(); };\n"
            ),
        })
        assert _callers(graph, "build") == {("run", "b.ts")}
        assert _callers(graph, "make") == {("run", "b.ts")}

    def test_resolve_plain_reexport_cycle_ends(self, tmp_path):
        graph = _build(tmp_path, {
            "a.ts": "export * from './b';\n",
            "b.ts": "export * from './a';\n",
            "c.ts": "import { gone } from './a';\nexport const run = () => gone();\n",
        })
        assert [n.name for n in graph.get_callees(_node(graph, "run").id)] == []

    def test_resolve_class_qualified_object_literal_member(self, tmp_path):
        graph = _build(tmp_path, {
            "api.ts": "export const api = { get() { return 1; }, post: () => 2 };\n",
            "b.ts": (
                "import { api } from './api';\n"
                "export const run = () => { api.get(); api.post(); };\n"
            ),
        })
        assert _callers(graph, "get") == {("run", "b.ts")}
        assert _callers(graph, "post") == {("run", "b.ts")}

    def test_resolve_plain_object_member_is_not_a_free_function(self, tmp_path):
        graph = _build(tmp_path, {
            "api.ts": "export const api = { get() { return 1; } };\n",
            "b.ts": "import { api } from './api';\nexport const run = () => get();\n",
        })
        assert _callers(graph, "get") == set()


class TestModuleBodyCallers:
    def test_resolve_calls_ts_module_body_is_a_caller(self, tmp_path):
        graph = _build(tmp_path, {
            "send.ts": "export const send = (x) => x;\n",
            "fn/index.ts": (
                "import { send } from '../send';\n"
                "Deno.serve(async (req) => { return send(req); });\n"
            ),
        })
        assert _callers(graph, "send") == {(MODULE_BODY_NAME, "index.ts")}

    def test_resolve_calls_python_module_body_is_a_caller(self, tmp_path):
        graph = _build(tmp_path, {
            "shop/__init__.py": "",
            "shop/db.py": "def connect():\n    return 1\n",
            "shop/app.py": "from shop.db import connect\n\nconn = connect()\n",
        })
        assert _callers(graph, "connect") == {(MODULE_BODY_NAME, "app.py")}

    def test_resolve_calls_python_decorator_links_the_decorated_function(self, tmp_path):
        graph = _build(tmp_path, {
            "shop/__init__.py": "",
            "shop/limits.py": "def rate_limit(n):\n    return lambda f: f\n",
            "shop/api.py": (
                "from shop.limits import rate_limit\n\n\n"
                "@rate_limit(5)\ndef post_order():\n    return 1\n"
            ),
        })
        assert _callers(graph, "rate_limit") == {("post_order", "api.py")}


class TestFixtureEdges:
    def _uses(self, graph, user_name):
        user = _node(graph, user_name)
        return {
            graph.nodes[r.target_id].location.file_path.rsplit("/", 1)[-1]
            for r in graph.get_relationships_from(user.id)
            if r.relationship_type == RelationshipType.USES
        }

    def test_resolve_fixtures_links_a_test_to_a_conftest_fixture(self, tmp_path):
        graph = _build(tmp_path, {
            "tests/conftest.py": (
                "import pytest\n\n\n@pytest.fixture\ndef db():\n    return make_db()\n"
            ),
            "tests/test_a.py": "def test_x(db, tmp_path):\n    assert db\n",
        })
        assert self._uses(graph, "test_x") == {"conftest.py"}

    def test_resolve_fixtures_same_file_fixture_wins(self, tmp_path):
        graph = _build(tmp_path, {
            "tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n",
            "tests/test_a.py": (
                "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 2\n\n\n"
                "def test_x(db):\n    assert db\n"
            ),
        })
        assert self._uses(graph, "test_x") == {"test_a.py"}

    def test_resolve_fixtures_fixture_in_another_folder_is_not_linked(self, tmp_path):
        graph = _build(tmp_path, {
            "other/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n",
            "tests/test_a.py": "def test_x(db):\n    assert db\n",
        })
        assert self._uses(graph, "test_x") == set()

    def test_resolve_fixtures_parent_conftest_and_fixture_chain(self, tmp_path):
        graph = _build(tmp_path, {
            "conftest.py": "import pytest\n\n\n@pytest.fixture\ndef engine():\n    return 1\n",
            "tests/conftest.py": (
                "import pytest\n\n\n@pytest.fixture\ndef db(engine):\n    return engine\n"
            ),
            "tests/sub/test_a.py": "def test_x(db):\n    assert db\n",
        })
        assert self._uses(graph, "test_x") == {"conftest.py"}
        assert self._uses(graph, "db") == {"conftest.py"}

    def test_resolve_fixtures_override_does_not_link_to_itself(self, tmp_path):
        graph = _build(tmp_path, {
            "conftest.py": "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n",
            "tests/conftest.py": (
                "import pytest\n\n\n@pytest.fixture\ndef db(db):\n    return db\n"
            ),
        })
        inner = _node(graph, "db", "tests/conftest.py")
        targets = [
            graph.nodes[r.target_id].location.file_path
            for r in graph.get_relationships_from(inner.id)
            if r.relationship_type == RelationshipType.USES
        ]
        assert targets and all(t.endswith("/conftest.py") and "tests" not in t for t in targets)

    def test_get_transitive_callers_follows_uses_edges_when_asked(self, tmp_path):
        graph = _build(tmp_path, {
            "shop/__init__.py": "",
            "shop/db.py": "def connect():\n    return 1\n",
            "tests/conftest.py": (
                "import pytest\nfrom shop.db import connect\n\n\n"
                "@pytest.fixture\ndef db():\n    return connect()\n"
            ),
            "tests/test_a.py": "def test_x(db):\n    assert db\n",
        })
        connect = _node(graph, "connect")
        calls_only = {n.name for n in graph.get_transitive_callers(connect.id)}
        with_uses = {
            n.name for n in graph.get_transitive_callers(
                connect.id, edge_types={RelationshipType.CALLS, RelationshipType.USES}
            )
        }
        assert calls_only == {"db"}
        assert with_uses == {"db", "test_x"}

"""Boundary facts and edges: invoke, HTTP routes (ADR-026).

Each test writes a small project to tmp_path, parses it with the real parsers and builds the
edges the way the pipeline does. A boundary edge runs from the node that uses a key to the
node that serves it. A key that two nodes serve gets no edge.
"""

import pytest

from nexus_mcp.core.graph_models import NodeType, RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.boundary_resolver import edge_function_name
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.parsing.boundaries import normalize_path, route_key

EDGE_FUNCTION = """\
export default async function handler(req: Request): Promise<Response> {
  return new Response("sent");
}
"""

INLINE_EDGE_FUNCTION = """\
Deno.serve(async (req) => {
  return new Response("sent");
});
"""


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


def _edges(graph):
    """{(user name, user file, target name, target file)} of the boundary edges."""
    found = set()
    for rel in graph.get_relationships_by_type(RelationshipType.DEPENDS_ON):
        source = graph.nodes[rel.source_id]
        target = graph.nodes[rel.target_id]
        found.add((
            source.name, source.location.file_path.rsplit("/", 1)[-1],
            target.name, target.location.file_path.rsplit("/", 1)[-1],
        ))
    return found


def _node(graph, name):
    found = [
        n for n in graph.find_nodes_by_name(name) if n.node_type == NodeType.FUNCTION
    ]
    assert len(found) == 1, f"{name}: {len(found)} nodes"
    return found[0]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/orders", "/orders"),
        ("/orders/", "/orders"),
        ("/orders/<int:order_id>", "/orders/{}"),
        ("/orders/{order_id}/lines", "/orders/{}/lines"),
        ("/orders/:id", "/orders/{}"),
        ("/orders/{}?expand=lines", "/orders/{}"),
        ("https://api.example.com/orders", ""),
        ("//cdn.example.com/x", ""),
        ("orders", ""),
    ],
)
def test_normalize_path_parameters_and_hosts(path, expected):
    assert normalize_path(path) == expected


def test_route_key_upper_cases_the_method():
    assert route_key("post", "/orders") == "POST /orders"
    assert route_key("get", "orders") == ""


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("supabase/functions/send-receipt/index.ts", "send-receipt"),
        ("repo/supabase/functions/send-receipt/index.tsx", "send-receipt"),
        ("supabase/functions/_shared/index.ts", ""),
        ("supabase/functions/send-receipt/helper.ts", ""),
        ("src/functions/send-receipt/index.ts", ""),
    ],
)
def test_edge_function_name_reads_the_folder(path, name):
    assert edge_function_name(path) == name


def test_resolve_boundaries_invoke_reaches_the_default_export(tmp_path):
    graph = _build(tmp_path, {
        "supabase/functions/send-receipt/index.ts": EDGE_FUNCTION,
        "web/src/checkout.ts": (
            "export async function pay(order) {\n"
            "  await supabase.functions.invoke('send-receipt', { body: order });\n"
            "}\n"
        ),
    })
    assert _edges(graph) == {("pay", "checkout.ts", "handler", "index.ts")}


def test_resolve_boundaries_invoke_reaches_the_top_level_of_a_deno_serve_file(tmp_path):
    graph = _build(tmp_path, {
        "supabase/functions/send-receipt/index.ts": INLINE_EDGE_FUNCTION,
        "web/src/checkout.ts": (
            "export function pay(o) { return supabase.functions.invoke('send-receipt'); }\n"
        ),
    })
    assert _edges(graph) == {("pay", "checkout.ts", "<top-level>", "index.ts")}


def test_resolve_boundaries_invoke_of_an_unknown_function_has_no_edge(tmp_path):
    graph = _build(tmp_path, {
        "supabase/functions/send-receipt/index.ts": EDGE_FUNCTION,
        "web/src/checkout.ts": (
            "export function pay(o) { return supabase.functions.invoke('send-invoice'); }\n"
        ),
    })
    assert _edges(graph) == set()


def test_resolve_boundaries_fetch_of_the_functions_url_reaches_the_function(tmp_path):
    graph = _build(tmp_path, {
        "supabase/functions/send-receipt/index.ts": EDGE_FUNCTION,
        "web/src/checkout.ts": (
            "export function pay(o) {\n"
            "  return fetch(`${BASE}/functions/v1/send-receipt`, { method: 'POST' });\n"
            "}\n"
        ),
    })
    assert _edges(graph) == {("pay", "checkout.ts", "handler", "index.ts")}


def test_resolve_boundaries_fetch_reaches_a_python_route(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": (
            "@app.post('/orders')\n"
            "def create_order(payload):\n"
            "    return payload\n"
        ),
        "web/src/api.ts": (
            "export async function submit(cart) {\n"
            "  return fetch('/orders', { method: 'POST', body: cart });\n"
            "}\n"
        ),
    })
    assert _edges(graph) == {("submit", "api.ts", "create_order", "orders.py")}


def test_resolve_boundaries_method_must_match(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": "@app.post('/orders')\ndef create_order(payload):\n    return 1\n",
        "web/src/api.ts": "export function list() { return fetch('/orders'); }\n",
    })
    assert _edges(graph) == set()


def test_resolve_boundaries_path_parameters_match(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": (
            "@router.get('/orders/{order_id}')\n"
            "def read_order(order_id):\n"
            "    return order_id\n"
        ),
        "web/src/api.ts": (
            "export function read(id) { return fetch(`/orders/${id}`); }\n"
        ),
    })
    assert _edges(graph) == {("read", "api.ts", "read_order", "orders.py")}


def test_resolve_boundaries_flask_route_methods(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": (
            "@app.route('/orders', methods=['GET', 'POST'])\n"
            "def orders():\n"
            "    return 1\n"
        ),
        "web/src/api.ts": (
            "export function list() { return fetch('/orders'); }\n"
            "export function add() { return fetch('/orders', { method: 'POST' }); }\n"
            "export function drop() { return fetch('/orders', { method: 'DELETE' }); }\n"
        ),
    })
    assert _edges(graph) == {
        ("list", "api.ts", "orders", "orders.py"),
        ("add", "api.ts", "orders", "orders.py"),
    }


def test_resolve_boundaries_python_client_reaches_a_ts_route(tmp_path):
    graph = _build(tmp_path, {
        "server/routes.ts": (
            "export function createOrder(req, res) { res.send('ok'); }\n"
            "app.post('/orders', createOrder);\n"
        ),
        "tests/test_orders.py": (
            "def test_create(client):\n"
            "    assert client.post('/orders', json={}).status_code == 200\n"
        ),
    })
    assert _edges(graph) == {("test_create", "test_orders.py", "createOrder", "routes.ts")}


def test_resolve_boundaries_inline_handler_serves_from_the_top_level(tmp_path):
    graph = _build(tmp_path, {
        "server/routes.ts": "app.get('/health', (req, res) => res.send('ok'));\n",
        "web/src/ping.ts": "export function ping() { return fetch('/health'); }\n",
    })
    assert _edges(graph) == {("ping", "ping.ts", "<top-level>", "routes.ts")}


def test_resolve_boundaries_two_providers_give_no_edge(tmp_path):
    graph = _build(tmp_path, {
        "a/orders.py": "@app.get('/orders')\ndef list_a():\n    return 1\n",
        "b/orders.py": "@app.get('/orders')\ndef list_b():\n    return 2\n",
        "web/src/api.ts": "export function list() { return fetch('/orders'); }\n",
    })
    assert _edges(graph) == set()


def test_resolve_boundaries_absolute_url_is_not_a_route(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": "@app.get('/orders')\ndef list_orders():\n    return 1\n",
        "web/src/api.ts": (
            "export function list() { return fetch('https://other.example.com/orders'); }\n"
        ),
    })
    assert _edges(graph) == set()


def test_resolve_boundaries_unrelated_get_call_is_not_a_use(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": "@app.get('/orders')\ndef list_orders():\n    return 1\n",
        "web/src/cache.ts": "export function read(m) { return m.get('/orders'); }\n",
    })
    assert _edges(graph) == set()


def test_resolve_boundaries_rebuild_drops_stale_edges(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": "@app.get('/orders')\ndef list_orders():\n    return 1\n",
        "web/src/api.ts": "export function list() { return fetch('/orders'); }\n",
    })
    assert len(_edges(graph)) == 1
    graph.remove_file_nodes(_node(graph, "list_orders").location.file_path)
    resolve_calls(graph)
    assert _edges(graph) == set()


def test_boundary_facts_are_stored_on_the_nodes(tmp_path):
    graph = _build(tmp_path, {
        "api/orders.py": "@app.get('/orders/<id>')\ndef read(id):\n    return 1\n",
        "web/src/api.ts": (
            "export function list() { return fetch('/orders', { method: 'POST' }); }\n"
        ),
    })
    serves = _node(graph, "read").metadata["boundary_serves"]
    assert [(f["kind"], f["key"]) for f in serves] == [("route", "GET /orders/{}")]
    uses = _node(graph, "list").metadata["boundary_uses"]
    assert [(f["kind"], f["key"]) for f in uses] == [("route", "POST /orders")]

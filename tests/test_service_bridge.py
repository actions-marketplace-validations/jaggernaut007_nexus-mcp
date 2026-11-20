"""The join of boundary facts across linked repos (ADR-030, rework of phase 2.4).

Inside one repo the boundary edges of ADR-026 are the only edges. `resolve_services` joins
the `boundary_uses` of one graph with the routes that another graph serves, and holds the
result in memory (a `ServiceBridge`). Each test parses small repos in `tmp_path` with the real
parser. Precision comes first: an edge needs exactly one fitting route.
"""

from nexus_mcp.core.graph_models import RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.indexing.service_resolver import (
    PRIMARY,
    TIER_EXACT,
    TIER_SUFFIX,
    ServiceBridge,
    resolve_services,
)
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _repo(tmp_path, name, files):
    root = tmp_path / name
    universal = UniversalGraph()
    parser = AstGrepParser()
    for relpath, source in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for relpath in sorted(files):
        parser.parse_file(str(root / relpath), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph, root


def _join(tmp_path, client_files, server_files, server_name="backend"):
    client, client_root = _repo(tmp_path, "frontend", client_files)
    server, server_root = _repo(tmp_path, server_name, server_files)
    bridge = resolve_services(
        {PRIMARY: client, server_name: server},
        {PRIMARY: [client_root], server_name: [server_root]},
    )
    return bridge, client, server


def _pairs(bridge, client, server):
    """{(client name, route handler name, tier, via)} of the edges."""
    out = set()
    for e in bridge.edges:
        c = (client if e.client_repo == PRIMARY else server).nodes[e.client_id]
        graph = client if e.route_repo == PRIMARY else server
        out.add((c.name, graph.nodes[e.route_id].name, e.tier, e.via))
    return out


ORDERS_ROUTER = """\
from fastapi import APIRouter

router = APIRouter(prefix="/orders")


@router.post("")
def create_order():
    return 1


@router.get("/{order_id}")
def get_order(order_id: int):
    return order_id
"""


def test_exact_match_across_repos(tmp_path):
    bridge, client, server = _join(
        tmp_path,
        {"api.ts": 'export function createOrder(b: object) { return axios.post("/orders", b); }\n'},
        {"orders.py": ORDERS_ROUTER},
    )
    assert _pairs(bridge, client, server) == {
        ("createOrder", "create_order", TIER_EXACT, "POST /orders")
    }


def test_the_edge_has_a_strength_and_both_ends(tmp_path):
    bridge, client, server = _join(
        tmp_path,
        {"api.ts": 'export function createOrder(b: object) { return axios.post("/orders", b); }\n'},
        {"orders.py": ORDERS_ROUTER},
    )
    (edge,) = bridge.edges
    assert (edge.client_repo, edge.route_repo) == (PRIMARY, "backend")
    assert edge.strength == 1.0
    assert bridge.callers_of("backend", edge.route_id) == [edge]
    assert bridge.callees_of(PRIMARY, edge.client_id) == [edge]
    assert bridge.callers_of(PRIMARY, edge.route_id) == []


def test_suffix_match_for_a_prefix_that_another_file_sets(tmp_path):
    bridge, client, server = _join(
        tmp_path,
        {"api.ts": "export function one(id: string) { return axios.get(`/api/orders/${id}`); }\n"},
        {"orders.py": ORDERS_ROUTER},
    )
    pairs = _pairs(bridge, client, server)
    assert pairs == {("one", "get_order", TIER_SUFFIX, "GET /api/orders/{}")}
    assert bridge.edges[0].strength == 0.8


def test_open_client_key_matches_after_the_base_variable(tmp_path):
    bridge, client, server = _join(
        tmp_path,
        {"api.ts": (
            "export function list(id: string) { return fetch(`${BASE}/orders/${id}`); }\n"
            "export function mine(id: string) { return fetch(`${BASE}/api/orders/${id}`); }\n"
        )},
        {"orders.py": ORDERS_ROUTER},
    )
    assert _pairs(bridge, client, server) == {
        ("list", "get_order", TIER_EXACT, "GET /orders/{}"),
        ("mine", "get_order", TIER_SUFFIX, "GET /api/orders/{}"),
    }


def test_the_method_must_fit(tmp_path):
    bridge, *_ = _join(
        tmp_path,
        {"api.ts": 'export function a() { return axios.delete("/orders"); }\n'},
        {"orders.py": ORDERS_ROUTER},
    )
    assert len(bridge) == 0


def test_two_routes_of_one_key_give_no_edge(tmp_path):
    graph_a, root_a = _repo(tmp_path, "frontend", {
        "api.ts": 'export function a() { return axios.post("/orders", {}); }\n',
    })
    graph_b, root_b = _repo(tmp_path, "one", {"orders.py": ORDERS_ROUTER})
    graph_c, root_c = _repo(tmp_path, "two", {"orders.py": ORDERS_ROUTER})
    bridge = resolve_services(
        {PRIMARY: graph_a, "one": graph_b, "two": graph_c},
        {PRIMARY: [root_a], "one": [root_b], "two": [root_c]},
    )
    assert len(bridge) == 0


def test_a_route_of_the_own_repo_keeps_the_edge_inside_the_repo(tmp_path):
    """Inside one repo only the boundary edge of ADR-026 exists. The bridge adds none, and
    the route of the other repo, with the same key, does not make the join guess."""
    client, client_root = _repo(tmp_path, "frontend", {
        "api.ts": 'export function a() { return axios.post("/orders", {}); }\n',
        "server.ts": (
            "app.post('/orders', save);\nfunction save() { return 1; }\n"
        ),
    })
    server, server_root = _repo(tmp_path, "backend", {"orders.py": ORDERS_ROUTER})
    bridge = resolve_services(
        {PRIMARY: client, "backend": server}, {PRIMARY: [client_root], "backend": [server_root]}
    )
    assert len(bridge) == 0
    assert {
        (client.nodes[r.source_id].name, client.nodes[r.target_id].name)
        for r in client.get_relationships_by_type(RelationshipType.DEPENDS_ON)
    } == {("a", "save")}


def test_a_prefix_with_a_parameter_is_not_cut_off(tmp_path):
    """`/users/{}/orders` is a nested resource: the route `/orders` is not its mount."""
    bridge, *_ = _join(
        tmp_path,
        {"api.ts": (
            "export function a(u: string) { return axios.post(`/users/${u}/orders`, {}); }\n"
        )},
        {"orders.py": ORDERS_ROUTER},
    )
    assert len(bridge) == 0


def test_a_route_with_no_literal_segment_never_fits_the_suffix_tier(tmp_path):
    bridge, *_ = _join(
        tmp_path,
        {"api.ts": "export function a(i: string) { return axios.get(`/api/v1/${i}`); }\n"},
        {"x.py": 'from fastapi import FastAPI\napp = FastAPI()\n\n\n'
                 '@app.get("/{item}")\ndef item(item: str):\n    return item\n'},
    )
    assert len(bridge) == 0


def test_calls_and_routes_in_test_files_are_left_out(tmp_path):
    bridge, *_ = _join(
        tmp_path,
        {"src/__tests__/api.test.ts": (
            'export function t() { return axios.post("/orders", {}); }\n'
        )},
        {"orders.py": ORDERS_ROUTER},
    )
    assert len(bridge) == 0


def test_a_folder_above_the_repo_root_does_not_make_a_test_file(tmp_path):
    """The repos live under a folder named `tests`. The rule reads the path inside the repo."""
    base = tmp_path / "tests" / "work"
    base.mkdir(parents=True)
    bridge, client, server = _join(
        base,
        {"api.ts": 'export function createOrder(b: object) { return axios.post("/orders", b); }\n'},
        {"orders.py": ORDERS_ROUTER},
    )
    assert len(bridge) == 1


def test_a_url_with_a_host_is_no_client_of_a_route(tmp_path):
    bridge, *_ = _join(
        tmp_path,
        {"api.ts": 'export function a() { return axios.post("https://api.stripe.com/orders"); }\n'},
        {"orders.py": ORDERS_ROUTER},
    )
    assert len(bridge) == 0


def test_the_join_writes_nothing_to_a_graph(tmp_path):
    client, client_root = _repo(tmp_path, "frontend", {
        "api.ts": 'export function createOrder(b: object) { return axios.post("/orders", b); }\n',
    })
    server, server_root = _repo(tmp_path, "backend", {"orders.py": ORDERS_ROUTER})
    before = (len(client.relationships), len(server.relationships))
    bridge = resolve_services(
        {PRIMARY: client, "backend": server}, {PRIMARY: [client_root], "backend": [server_root]}
    )
    assert len(bridge) == 1
    assert (len(client.relationships), len(server.relationships)) == before


def test_function_and_table_facts_make_no_service_edge(tmp_path):
    client, client_root = _repo(tmp_path, "frontend", {
        "api.ts": (
            'export function a() { return supabase.functions.invoke("send-receipt"); }\n'
            'export function b() { return supabase.from("orders").select(); }\n'
        ),
    })
    server, server_root = _repo(tmp_path, "backend", {
        "supabase/functions/send-receipt/index.ts": (
            "export default async function handler() { return 1; }\n"
        ),
        "db/001.sql": "create table orders (id int);\n",
    })
    bridge = resolve_services(
        {PRIMARY: client, "backend": server}, {PRIMARY: [client_root], "backend": [server_root]}
    )
    assert len(bridge) == 0


def test_an_empty_bridge_is_false(tmp_path):
    assert not ServiceBridge()
    assert len(ServiceBridge()) == 0


def test_a_next_route_in_the_other_repo_is_a_provider(tmp_path):
    bridge, client, server = _join(
        tmp_path,
        {"api.ts": "export function ping() { return fetch('/api/ping'); }\n"},
        {"app/api/ping/route.ts": "export async function GET() { return new Response('x'); }\n"},
    )
    assert _pairs(bridge, client, server) == {("ping", "GET", TIER_EXACT, "GET /api/ping")}

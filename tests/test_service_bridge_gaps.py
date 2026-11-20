"""Gaps in the join across repos: tier rules, ambiguity, dedupe, bad facts, repo roots."""

import pytest

from nexus_mcp.core.graph_models import UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.indexing.service_resolver import (
    PRIMARY,
    TIER_EXACT,
    ServiceBridge,
    ServiceEdge,
    _client_path,
    _suffixes,
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


def _router(prefix, name="create"):
    return (
        "from fastapi import APIRouter\n\n"
        f'router = APIRouter(prefix="{prefix}")\n\n\n'
        f'@router.post("")\ndef {name}():\n    return 1\n'
    )


def _resolve(repos):
    """repos: {key: (graph, root)}."""
    return resolve_services(
        {k: g for k, (g, _) in repos.items()}, {k: [r] for k, (_, r) in repos.items()}
    )


# --- helpers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, want",
    [
        ("/api/v1/orders", ["/v1/orders", "/orders"]),
        ("/orders", []),
        # `/orders` is never offered: the cut-off part `/users/{}` has a parameter.
        ("/users/{}/orders", []),
        ("/api/users/{}/orders", ["/users/{}/orders"]),
        ("", []),
    ],
)
def test_suffixes(path, want):
    assert _suffixes(path) == want


@pytest.mark.parametrize(
    "raw, want",
    [("{}/orders", "/orders"), ("{}{}/orders", "/orders"), ("/orders", "/orders"),
     ("{}", ""), ("orders", ""), ("", "")],
)
def test_client_path(raw, want):
    assert _client_path(raw) == want


def test_bridge_lookups_for_an_unknown_node_are_empty():
    bridge = ServiceBridge()
    assert bridge.callers_of("x", "n") == [] and bridge.callees_of("x", "n") == []
    edge = ServiceEdge("", "c", "r", "h", TIER_EXACT, "GET /a", "GET /a")
    bridge.add(edge)
    assert bridge.callers_of("r", "h") == [edge] and bridge.callees_of("", "c") == [edge]
    # The lookups return copies: a caller cannot change the bridge through them.
    bridge.callers_of("r", "h").clear()
    assert len(bridge.callers_of("r", "h")) == 1


# --- matching ------------------------------------------------------------------------------


def test_a_client_that_is_the_only_exact_route_stops_the_suffix_tier(tmp_path):
    """A proxy serves `POST /api/orders` and calls it. Another repo serves `POST /orders`.
    The exact tier has a candidate (the client itself), so no suffix edge is made."""
    front = _repo(tmp_path, "front", {
        "proxy.ts": (
            "app.post('/api/orders', proxy);\n"
            "function proxy() { return axios.post('/api/orders', {}); }\n"
        ),
    })
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    assert len(_resolve({PRIMARY: front, "back": back})) == 0


def test_two_suffix_candidates_give_no_edge(tmp_path):
    front = _repo(tmp_path, "front", {
        "api.ts": "export function a() { return axios.post('/api/v1/orders', {}); }\n",
    })
    one = _repo(tmp_path, "one", {"o.py": _router("/orders")})
    two = _repo(tmp_path, "two", {"o.py": _router("/v1/orders")})
    assert len(_resolve({PRIMARY: front, "one": one, "two": two})) == 0


def test_two_facts_of_one_client_for_one_route_make_one_edge(tmp_path):
    front = _repo(tmp_path, "front", {
        "api.ts": (
            "export function a(b: object) {\n"
            "  axios.post('/orders', b);\n"
            "  return fetch(`${BASE}/orders`, { method: 'POST' });\n"
            "}\n"
        ),
    })
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    assert len(_resolve({PRIMARY: front, "back": back})) == 1


def test_many_clients_of_one_route_all_get_an_edge(tmp_path):
    front = _repo(tmp_path, "front", {
        "a.ts": "export function a() { return axios.post('/orders', {}); }\n",
        "b.ts": "export function b() { return axios.post('/orders', {}); }\n",
    })
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    bridge = _resolve({PRIMARY: front, "back": back})
    assert len(bridge) == 2
    (route_id,) = {e.route_id for e in bridge.edges}
    assert len(bridge.callers_of("back", route_id)) == 2


def test_a_linked_repo_can_be_the_client_of_the_primary_project(tmp_path):
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    front = _repo(tmp_path, "front", {
        "api.ts": "export function a() { return axios.post('/orders', {}); }\n",
    })
    (edge,) = _resolve({PRIMARY: back, "front": front}).edges
    assert (edge.client_repo, edge.route_repo) == ("front", PRIMARY)


def test_a_route_in_a_test_file_of_the_other_repo_is_no_provider(tmp_path):
    front = _repo(tmp_path, "front", {
        "api.ts": "export function a() { return axios.post('/orders', {}); }\n",
    })
    back = _repo(tmp_path, "back", {"tests/orders_api.py": _router("/orders")})
    assert len(_resolve({PRIMARY: front, "back": back})) == 0


def test_a_client_in_a_test_folder_of_its_repo_is_left_out(tmp_path):
    front = _repo(tmp_path, "front", {
        "tests/api.ts": "export function a() { return axios.post('/orders', {}); }\n",
    })
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    assert len(_resolve({PRIMARY: front, "back": back})) == 0


def test_a_repo_with_no_root_given_is_still_joined(tmp_path):
    """`roots` is optional. A repo that is not in it keeps its paths as the graph holds them."""
    front = _repo(tmp_path, "front", {
        "api.ts": "export function a() { return axios.post('/orders', {}); }\n",
    })
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    bridge = resolve_services({PRIMARY: front[0], "back": back[0]})
    # The tmp folder is named `test_...`: only the name of a file or a test folder counts.
    assert len(bridge) == 1


def test_no_graphs_give_an_empty_bridge():
    assert len(resolve_services({})) == 0


def test_a_malformed_fact_is_skipped(tmp_path):
    front = _repo(tmp_path, "front", {
        "api.ts": "export function a() { return axios.post('/orders', {}); }\n",
    })
    back = _repo(tmp_path, "back", {"o.py": _router("/orders")})
    client = next(n for n in front[0].nodes.values() if n.name == "a")
    client.metadata["boundary_uses"] = [
        {"kind": "route"},                      # no key
        {"kind": "route", "key": "GET"},        # no path
        {"kind": "route", "key": "GET {}"},     # an open key with no path
        {"kind": "route", "key": "GET /{}"},    # no literal segment
        {"kind": "route", "key": "GET /orders"},  # the one good fact: no route for GET
    ]
    assert len(_resolve({PRIMARY: front, "back": back})) == 0

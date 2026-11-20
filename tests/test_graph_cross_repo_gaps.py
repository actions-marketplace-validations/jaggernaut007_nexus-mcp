"""Gaps in `graph` across linked repos: linked-only start, detail=full, caps, cycles, depth."""

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp.config import reset_settings
from nexus_mcp.state import get_state, reset_state
from tests.test_graph_cross_repo import BACKEND, FRONTEND, _world

pytestmark = pytest.mark.usefixtures("fake_embeddings")


@pytest.fixture
def front_world(tmp_path, monkeypatch):
    yield _world(tmp_path, monkeypatch, FRONTEND, BACKEND, "backend")
    reset_settings()


@pytest.fixture
def back_world(tmp_path, monkeypatch):
    yield _world(tmp_path, monkeypatch, BACKEND, FRONTEND, "frontend")
    reset_settings()


# --- a symbol that only the linked repo defines --------------------------------------------


def test_linked_only_start_transitive_lists_the_clients_and_their_callers(front_world):
    result = core_api.graph("create_order", direction="callers", transitive=True)
    assert result["defined_in"] == {"backend/app/orders.py": ["create_order:7"]}
    assert result["total_impacted"] == 2
    assert result["impacted_files"] == {
        "src/api.ts": ["createOrder via POST /orders"],
        "src/page.ts": ["orderPage"],
    }
    assert "impacted_symbols" not in result


def test_linked_only_start_transitive_full_has_the_symbols(front_world):
    result = core_api.graph(
        "create_order", direction="callers", transitive=True, detail="full"
    )
    names = {s["name"] for s in result["impacted_symbols"]}
    assert names == {"createOrder", "orderPage"}
    via = {s["name"]: s.get("via") for s in result["impacted_symbols"]}
    assert via == {"createOrder": "POST /orders", "orderPage": None}
    assert all("repo" not in s for s in result["impacted_symbols"])  # the primary project


def test_linked_only_start_transitive_depth_one_stops_at_the_client(front_world):
    result = core_api.graph(
        "create_order", direction="callers", transitive=True, max_depth=1
    )
    assert result["total_impacted"] == 1
    assert result["impacted_files"] == {"src/api.ts": ["createOrder via POST /orders"]}


def test_linked_only_start_full_detail_callers(front_world):
    result = core_api.graph("create_order", direction="callers", detail="full")
    (entry,) = result["callers"]
    assert entry["name"] == "createOrder" and entry["resolution"] == "exact"
    assert "repo" not in entry  # the client is in the project of the session
    assert result["total"] == 1


def test_linked_only_start_full_detail_callees_names_the_repo(front_world):
    result = core_api.graph("create_order", direction="callees", detail="full")
    (entry,) = result["callees"]
    assert entry["name"] == "save"
    assert entry["repo"] == "backend"
    assert entry["location"]["file"] == "backend/app/orders.py"
    assert "via" not in entry


def test_a_symbol_in_neither_repo_is_not_found_and_has_no_linked_field(front_world):
    for kwargs in ({}, {"transitive": True}):
        result = core_api.graph("nothing_defines_this", **kwargs)
        assert not [k for k in result if k.startswith("linked_")]
        assert not result.get("callers") and "defined_in" not in result


def test_a_transitive_walk_ends_on_a_cycle_across_the_repos(tmp_path, monkeypatch):
    backend = dict(BACKEND)
    backend["app/orders.py"] = BACKEND["app/orders.py"].replace(
        "def save():\n    return 1", "def save():\n    return create_order()"
    )
    _world(tmp_path, monkeypatch, backend, FRONTEND, "frontend")
    try:
        result = core_api.graph("save", direction="callers", transitive=True)
        assert result["linked_impacted"]["frontend/src/api.ts"] == [
            "createOrder via POST /orders"
        ]
        assert "app/orders.py" in result["impacted_files"]
    finally:
        reset_settings()


# --- a symbol of the primary project -------------------------------------------------------


def test_transitive_depth_one_does_not_reach_the_linked_client(back_world):
    """`save` is called by `create_order` (depth 1); the client is at depth 2."""
    near = core_api.graph("save", direction="callers", transitive=True, max_depth=1)
    assert "linked_impacted" not in near
    far = core_api.graph("save", direction="callers", transitive=True, max_depth=2)
    assert "frontend/src/api.ts" in far["linked_impacted"]
    assert "frontend/src/page.ts" not in far["linked_impacted"]  # depth 3


def test_transitive_full_detail_keeps_the_linked_field_compact(back_world):
    result = core_api.graph("save", direction="callers", transitive=True, detail="full")
    assert "frontend/src/api.ts" in result["linked_impacted"]


def test_callees_of_a_primary_function_show_the_linked_callee_in_full_detail(front_world):
    result = core_api.graph("createOrder", direction="callees", detail="full")
    (entry,) = result["linked_callees"]
    assert entry["name"] == "create_order" and entry["repo"] == "backend"
    assert entry["location"]["file"] == "backend/app/orders.py"
    assert entry["via"] == "POST /orders" and entry["resolution"] == "exact"


def test_a_name_defined_in_both_repos_lists_each_side(tmp_path, monkeypatch):
    primary = {"src/own.py": "def create_order():\n    return helper()\n\n\ndef helper():\n"
                             "    return 1\n"}
    _world(tmp_path, monkeypatch, primary, BACKEND, "backend")
    try:
        result = core_api.graph("create_order", direction="callees")
        assert result["callees"] == {"src/own.py": ["helper:5"]}
        assert result["linked_callees"] == {"backend/app/orders.py": ["save:11"]}
    finally:
        reset_settings()


def test_a_detached_repo_in_transitive_mode_does_not_crash(back_world):
    get_state().linked = []
    result = core_api.graph("save", direction="callers", transitive=True)
    assert "error" not in result and "linked_impacted" not in result


# --- caps ----------------------------------------------------------------------------------


def _client(i):
    return f"export function call{i:02d}() {{ return axios.post('/orders', {{}}); }}\n"


@pytest.fixture
def crowd_world(tmp_path, monkeypatch):
    """One route in the project, and 62 client files in the linked repo."""
    clients = {
        f"src/c{i:02d}.ts": _client(i) for i in range(62)
    }
    yield _world(tmp_path, monkeypatch, BACKEND, clients, "frontend")
    reset_settings()


def test_linked_callers_are_capped_by_file_and_the_rest_is_counted(crowd_world):
    result = core_api.graph("create_order", direction="callers")
    assert len(result["linked_callers"]) == core_api.MAX_GRAPH_SOURCE_FILES
    assert result["linked_callers_not_listed"]["files"] == 2
    assert result["linked_callers_not_listed"]["symbols"] == 2


def test_linked_callers_in_full_detail_are_capped_by_node(crowd_world):
    result = core_api.graph("create_order", direction="callers", detail="full")
    assert len(result["linked_callers"]) == core_api.MAX_FULL_NODES
    assert "linked_callers_not_listed" not in result


def test_linked_impact_is_capped_by_file(crowd_world):
    result = core_api.graph("create_order", direction="callers", transitive=True)
    assert len(result["linked_impacted"]) == core_api.MAX_GRAPH_SOURCE_FILES
    assert result["linked_impacted_not_listed"]["files"] == 2


def test_a_linked_only_symbol_with_many_callers_is_capped(tmp_path, monkeypatch):
    clients = {
        f"src/c{i:02d}.ts": _client(i) for i in range(62)
    }
    _world(tmp_path, monkeypatch, clients, BACKEND, "backend")
    try:
        result = core_api.graph("create_order", direction="callers")
        assert result["total"] == 62
        assert len(result["callers"]) == core_api.MAX_GRAPH_SOURCE_FILES
        assert result["callers_not_listed"]["files"] == 2
        full = core_api.graph("create_order", direction="callers", detail="full")
        assert len(full["callers"]) == core_api.MAX_FULL_NODES
        deep = core_api.graph("create_order", direction="callers", transitive=True)
        assert deep["total_impacted"] == 62
        assert deep["impacted_not_listed"]["files"] == 2
    finally:
        reset_settings()


def test_defined_in_is_left_out_when_the_linked_repo_has_too_many_definitions(
    tmp_path, monkeypatch
):
    many = {
        f"app/m{i}.py": "def dup():\n    return 1\n" for i in range(core_api.MAX_DEFINED_IN + 1)
    }
    _world(tmp_path, monkeypatch, {"src/x.py": "def other():\n    return 1\n"}, many, "backend")
    try:
        result = core_api.graph("dup", direction="callers")
        assert "defined_in" not in result
    finally:
        reset_settings()


# --- helpers -------------------------------------------------------------------------------


def test_repo_path_prefixes_only_a_linked_relative_path():
    assert core_api._repo_path("", "src/a.py") == "src/a.py"
    assert core_api._repo_path("backend", "app/a.py") == "backend/app/a.py"
    assert core_api._repo_path("backend", "/abs/a.py") == "/abs/a.py"


def test_rebuild_bridge_with_no_link_clears_the_bridge():
    reset_state()
    state = get_state()
    state.bridge = object()
    core_api._rebuild_bridge(state)
    assert state.bridge is None

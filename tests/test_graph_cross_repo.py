"""`graph` follows the edges between linked repos (ADR-030, rework of phase 2.4).

Real stores in `tmp_path`: a frontend that calls the route of a backend. One of the two repos
is the project of the session and the other is linked. Inside one repo the boundary edges of
ADR-026 stay the only edges. The cross-repo edges live in `state.bridge`, in memory.
"""

import threading

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import links
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state

pytestmark = pytest.mark.usefixtures("fake_embeddings")

FRONTEND = {
    "src/api.ts": (
        'export function createOrder(body: object) { return axios.post("/orders", body); }\n'
        'export function noRoute() { return axios.get("/nothing-serves-this"); }\n'
    ),
    "src/page.ts": (
        "import { createOrder } from './api';\n"
        "export function orderPage() { return createOrder({}); }\n"
    ),
}
BACKEND = {
    "app/orders.py": (
        "from fastapi import APIRouter\n\n"
        'router = APIRouter(prefix="/orders")\n\n\n'
        '@router.post("")\n'
        "def create_order():\n"
        "    return save()\n\n\n"
        "def save():\n"
        "    return 1\n"
    ),
}


def _write(root, files):
    for relpath, source in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)


def _world(tmp_path, monkeypatch, primary_files, linked_files, linked_name):
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    primary = tmp_path / "primary"
    linked = tmp_path / linked_name
    _write(primary, primary_files)
    _write(linked, linked_files)
    monkeypatch.chdir(primary)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(primary / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(linked)]) == 0
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    monkeypatch.setenv("NEXUS_STALENESS_CHECK_INTERVAL", "0")
    reset_settings()
    assert core_api.restore_session() is True
    return primary, linked


@pytest.fixture
def front_world(tmp_path, monkeypatch):
    """The frontend is the project of the session; the backend is linked."""
    yield _world(tmp_path, monkeypatch, FRONTEND, BACKEND, "backend")
    reset_settings()


@pytest.fixture
def back_world(tmp_path, monkeypatch):
    """The backend is the project of the session; the frontend is linked."""
    yield _world(tmp_path, monkeypatch, BACKEND, FRONTEND, "frontend")
    reset_settings()


def test_attach_builds_the_bridge(front_world):
    bridge = get_state().bridge
    assert bridge is not None and len(bridge) == 1
    (edge,) = bridge.edges
    assert edge.via == "POST /orders"


def test_callees_of_a_client_name_the_route_in_the_linked_repo(front_world):
    result = core_api.graph("createOrder", direction="callees")
    assert result["linked_callees"] == {
        "backend/app/orders.py": ["create_order:7 via POST /orders"]
    }
    assert result["total"] == 0  # the CALLS callees of the project are counted as before


def test_a_client_with_no_route_has_no_linked_field(front_world):
    result = core_api.graph("noRoute", direction="callees")
    assert "linked_callees" not in result


def test_callers_of_a_route_name_the_client_in_the_linked_repo(back_world):
    result = core_api.graph("create_order", direction="callers")
    assert result["linked_callers"] == {
        "frontend/src/api.ts": ["createOrder:1 via POST /orders"]
    }


def test_full_detail_gives_the_repo_the_edge_and_the_tier(back_world):
    result = core_api.graph("create_order", direction="callers", detail="full")
    (entry,) = result["linked_callers"]
    assert entry["name"] == "createOrder"
    assert entry["repo"] == "frontend"
    assert entry["via"] == "POST /orders"
    assert entry["resolution"] == "exact"
    assert entry["location"]["file"] == "frontend/src/api.ts"


def test_transitive_impact_crosses_into_the_linked_repo(back_world):
    result = core_api.graph("save", direction="callers", transitive=True)
    assert result["linked_impacted"] == {
        "frontend/src/api.ts": ["createOrder via POST /orders"],
        "frontend/src/page.ts": ["orderPage"],
    }
    # The own files of the project stay as they were.
    assert "app/orders.py" in result["impacted_files"]
    assert not any(f.startswith("frontend/") for f in result["impacted_files"])


def test_a_symbol_that_only_the_linked_repo_defines_is_found(front_world):
    result = core_api.graph("create_order", direction="callers")
    assert result["defined_in"] == {"backend/app/orders.py": ["create_order:7"]}
    assert result["callers"] == {"src/api.ts": ["createOrder:1 via POST /orders"]}


def test_a_symbol_that_only_the_linked_repo_defines_has_its_own_callees(front_world):
    result = core_api.graph("create_order", direction="callees")
    assert result["callees"] == {"backend/app/orders.py": ["save:11"]}


def test_without_a_link_the_answer_has_no_linked_field(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    root = tmp_path / "solo"
    _write(root, FRONTEND)
    monkeypatch.chdir(root)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(root / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(root)
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    reset_settings()
    assert core_api.restore_session() is True
    for call in (
        core_api.graph("createOrder", direction="callees"),
        core_api.graph("createOrder", direction="callers"),
        core_api.graph("createOrder", direction="callers", transitive=True),
    ):
        assert not [k for k in call if k.startswith("linked_")]
    assert get_state().bridge is None or not get_state().bridge
    reset_settings()


def test_unlink_removes_the_cross_repo_fields(front_world):
    primary, _ = front_world
    links.remove_link(primary, "backend")
    reset_state()
    core_api._pipeline = None
    reset_settings()
    assert core_api.restore_session() is True
    assert not get_state().bridge
    result = core_api.graph("createOrder", direction="callees")
    assert "linked_callees" not in result


def test_a_repo_that_is_detached_while_graph_runs_does_not_crash(front_world):
    """A parallel `index()` replaces `state.linked`. A hop that was found a moment ago can
    then name a repo that is gone; its paths are shown as the graph holds them."""
    state = get_state()
    assert len(state.bridge) == 1
    state.linked = []
    result = core_api.graph("createOrder", direction="callees")
    assert "error" not in result


def test_a_failing_bridge_leaves_graph_working(front_world, monkeypatch):
    import nexus_mcp.indexing.service_resolver as resolver

    def boom(*args, **kwargs):
        raise RuntimeError("no join today")

    monkeypatch.setattr(resolver, "resolve_services", boom)
    core_api._rebuild_bridge(get_state())
    assert get_state().bridge is None
    result = core_api.graph("createOrder", direction="callees")
    assert "linked_callees" not in result and "error" not in result


def test_a_background_reindex_builds_the_bridge_again(front_world, tmp_path):
    primary, _ = front_world
    state = get_state()
    state.bridge = None
    (primary / "src" / "extra.ts").write_text("export function extra() { return 1; }\n")
    core_api._trigger_background_reindex(primary, None)
    for thread in threading.enumerate():
        if thread.name == "nexus-bg-reindex":
            thread.join(timeout=60)
    assert get_state().bridge is not None and len(get_state().bridge) == 1


def test_next_route_key_uses_the_path_below_the_repo_root():
    from nexus_mcp.indexing.boundary_resolver import next_route_key

    root = ["/home/u/app/web"]
    assert next_route_key("/home/u/app/web/app/api/orders/route.ts", "GET", root) == (
        "GET /api/orders"
    )
    # A folder named `app` above the root is no part of the URL.
    away = "/home/u/app/server/src/health/route.ts"
    assert next_route_key(away, "GET", ["/home/u/app/server"]) == ""
    # Two `app` folders below the root: the URL is not certain, so no key.
    assert next_route_key("/r/app/api/app/route.ts", "GET", ["/r"]) == ""

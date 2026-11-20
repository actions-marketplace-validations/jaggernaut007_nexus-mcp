"""Boundary facts for the frameworks that ADR-026 did not read (ADR-030, rework of phase 2.3).

The parser leaves `boundary_serves` and `boundary_uses` on graph nodes. These tests cover:
a router prefix that the file sets, NestJS controllers, Next.js route files and a client URL
that starts with a base-URL variable. Each test parses a small project in `tmp_path` with the
real parser. A fact that is not certain is left out: precision first.
"""

from nexus_mcp.core.graph_models import RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _graph(tmp_path, files):
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


def _keys(graph, field):
    """{node name: [keys]} for the nodes that hold `field` facts."""
    return {
        node.name: sorted(f["key"] for f in node.metadata[field])
        for node in graph.nodes.values()
        if node.metadata.get(field)
    }


def _serves(tmp_path, relpath, source):
    return _keys(_graph(tmp_path, {relpath: source}), "boundary_serves")


def _uses(tmp_path, relpath, source):
    return _keys(_graph(tmp_path, {relpath: source}), "boundary_uses")


def _edges(graph):
    return {
        (graph.nodes[r.source_id].name, graph.nodes[r.target_id].name)
        for r in graph.get_relationships_by_type(RelationshipType.DEPENDS_ON)
    }


# --- router prefixes (FastAPI, Flask) ------------------------------------------------------


def test_router_prefix_is_part_of_the_route_key(tmp_path):
    source = """\
from fastapi import APIRouter

router = APIRouter(prefix="/orders")


@router.get("/{order_id}")
def get_order(order_id: int):
    return order_id


@router.post("")
def create_order():
    return 1
"""
    assert _serves(tmp_path, "orders.py", source) == {
        "get_order": ["GET /orders/{}"],
        "create_order": ["POST /orders"],
    }


def test_blueprint_url_prefix_is_part_of_the_route_key(tmp_path):
    source = """\
from flask import Blueprint

bp = Blueprint("users", __name__, url_prefix="/users")


@bp.route("/me", methods=["GET"])
def me():
    return "me"
"""
    assert _serves(tmp_path, "users.py", source) == {"me": ["GET /users/me"]}


def test_include_router_prefix_in_the_same_file_is_added(tmp_path):
    source = """\
from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter(prefix="/orders")
app.include_router(router, prefix="/api")


@router.get("/{order_id}")
def get_order(order_id: int):
    return order_id
"""
    assert _serves(tmp_path, "main.py", source) == {"get_order": ["GET /api/orders/{}"]}


def test_a_prefix_that_is_not_a_literal_leaves_the_path_as_written(tmp_path):
    """Main's rule: a route path is the string of the decorator. A prefix that the file
    does not give as a literal is not guessed."""
    source = """\
from fastapi import APIRouter

router = APIRouter(prefix=settings.PREFIX)


@router.get("/items")
def items():
    return []
"""
    assert _serves(tmp_path, "items.py", source) == {"items": ["GET /items"]}


def test_decorator_on_app_without_prefix_is_unchanged(tmp_path):
    source = """\
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/health")
def health():
    return {}
"""
    assert _serves(tmp_path, "main.py", source) == {"health": ["GET /api/health"]}


# --- NestJS --------------------------------------------------------------------------------

NEST = """\
import { Controller, Get, Post, Delete } from "@nestjs/common";

@Controller("orders")
export class OrdersController {
  @Get()
  list() { return []; }

  @Get(":id")
  findOne() { return 1; }

  @Post()
  create() { return 2; }

  @Delete(":id")
  remove() { return 3; }

  helper() { return 4; }
}
"""


def test_nest_controller_methods_serve_routes(tmp_path):
    assert _serves(tmp_path, "orders.controller.ts", NEST) == {
        "list": ["GET /orders"],
        "findOne": ["GET /orders/{}"],
        "create": ["POST /orders"],
        "remove": ["DELETE /orders/{}"],
    }


def test_nest_controller_with_object_path(tmp_path):
    source = """\
@Controller({ path: "users" })
export class UsersController {
  @Get("me")
  me() { return 1; }
}
"""
    assert _serves(tmp_path, "users.controller.ts", source) == {"me": ["GET /users/me"]}


def test_nest_controller_with_no_path_serves_the_method_path(tmp_path):
    source = """\
@Controller()
export class AppController {
  @Get("health")
  health() { return 1; }
}
"""
    assert _serves(tmp_path, "app.controller.ts", source) == {"health": ["GET /health"]}


def test_nest_path_that_is_not_a_literal_gives_no_route(tmp_path):
    source = """\
@Controller(PREFIX)
export class XController {
  @Get("a")
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {}


def test_a_class_without_controller_decorator_serves_nothing(tmp_path):
    source = """\
export class Cache {
  @Get("a")
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "cache.ts", source) == {}


# --- Next.js route files -------------------------------------------------------------------


def test_next_route_file_serves_its_path_to_a_fetch_in_the_same_repo(tmp_path):
    graph = _graph(tmp_path, {
        "app/api/orders/[id]/route.ts": (
            "export async function GET(req: Request) { return new Response('x'); }\n"
        ),
        "src/client.ts": (
            "export function loadOrder(id: string) { return fetch(`/api/orders/${id}`); }\n"
        ),
    })
    assert _edges(graph) == {("loadOrder", "GET")}


def test_next_route_file_ignores_route_groups_and_other_names(tmp_path):
    graph = _graph(tmp_path, {
        "src/app/(shop)/api/items/route.ts": (
            "export async function POST(req: Request) { return new Response('x'); }\n"
            "export async function helper() { return 1; }\n"
        ),
        "src/client.ts": (
            "export function add() { return fetch('/api/items', { method: 'POST' }); }\n"
            "export function list() { return fetch('/api/items'); }\n"
        ),
    })
    assert _edges(graph) == {("add", "POST")}


def test_next_catch_all_segment_gives_no_provider(tmp_path):
    graph = _graph(tmp_path, {
        "app/api/[...slug]/route.ts": (
            "export async function GET(req: Request) { return new Response('x'); }\n"
        ),
        "src/client.ts": "export function a() { return fetch('/api/anything'); }\n",
    })
    assert _edges(graph) == set()


def test_a_file_named_route_outside_an_app_folder_is_not_a_next_route(tmp_path):
    graph = _graph(tmp_path, {
        "lib/api/route.ts": "export async function GET() { return 1; }\n",
        "src/client.ts": "export function a() { return fetch('/api'); }\n",
    })
    assert _edges(graph) == set()


# --- client URLs that start with a variable ------------------------------------------------


def test_base_url_variable_gives_an_open_key(tmp_path):
    source = """\
const BASE = import.meta.env.VITE_API_URL;

export async function listOrders() {
  return fetch(`${BASE}/api/orders`);
}

export async function one(id: string) {
  return fetch(`${BASE}/api/orders/${id}`);
}
"""
    assert _uses(tmp_path, "client.ts", source) == {
        "listOrders": ["GET {}/api/orders"],
        "one": ["GET {}/api/orders/{}"],
    }


def test_open_key_with_no_literal_segment_is_left_out(tmp_path):
    source = """\
export async function a(base: string, id: string) {
  return fetch(`${base}/${id}`);
}

export async function b(base: string) {
  return fetch(`${base}`);
}
"""
    assert _uses(tmp_path, "client.ts", source) == {}


def test_a_url_with_a_literal_host_is_still_left_out(tmp_path):
    source = "export async function a() { return fetch('https://api.stripe.com/v1/x'); }\n"
    assert _uses(tmp_path, "client.ts", source) == {}


def test_open_key_makes_no_edge_in_the_same_repo(tmp_path):
    """An open key is for the join across repos. Inside one repo only the keys that are
    equal give an edge, as before."""
    graph = _graph(tmp_path, {
        "server.ts": "app.get('/api/orders', listOrders);\nfunction listOrders() { return 1; }\n",
        "client.ts": "export function call() { return fetch(`${BASE}/api/orders`); }\n",
    })
    assert _edges(graph) == set()


def test_python_f_string_with_a_base_variable_gives_an_open_key(tmp_path):
    source = """\
import requests


def load(base):
    return requests.get(f"{base}/api/orders")
"""
    assert _uses(tmp_path, "client.py", source) == {"load": ["GET {}/api/orders"]}


def test_fetch_helpers_are_clients(tmp_path):
    source = """\
export async function a() { return $fetch("/api/a"); }
export async function b() { return ofetch("/api/b", { method: "POST" }); }
export async function c() { return ky.get("/api/c"); }
"""
    assert _uses(tmp_path, "client.ts", source) == {
        "a": ["GET /api/a"],
        "b": ["POST /api/b"],
        "c": ["GET /api/c"],
    }


def test_next_route_exported_as_a_const_arrow_function_serves(tmp_path):
    graph = _graph(tmp_path, {
        "app/api/ping/route.ts": "export const GET = async () => new Response('pong');\n",
        "src/client.ts": "export function ping() { return fetch('/api/ping'); }\n",
    })
    assert _edges(graph) == {("ping", "GET")}


def test_root_route_still_serves_slash(tmp_path):
    source = """\
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {}
"""
    assert _serves(tmp_path, "main.py", source) == {"root": ["GET /"]}


def test_many_routes_in_one_file_index_fast(tmp_path):
    """The prefix tables are built once for a file, not once for each decorator."""
    import time

    body = "".join(
        f'@router.get("/r{i}")\ndef h{i}():\n    return {i}\n\n\n' for i in range(600)
    )
    source = "from fastapi import APIRouter\n\nrouter = APIRouter(prefix='/p')\n\n\n" + body
    start = time.perf_counter()
    found = _serves(tmp_path, "big.py", source)
    assert len(found) == 600 and found["h5"] == ["GET /p/r5"]
    assert time.perf_counter() - start < 5.0

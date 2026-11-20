"""Gaps in the boundary facts: prefixes, NestJS decorators, open keys, Next.js keys.

The unit tests call the pure helpers. The parser tests use the real parser in `tmp_path`.
"""

import pytest

from nexus_mcp.core.graph_models import UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.boundary_resolver import next_route_key
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.parsing.boundaries import (
    normalize_path,
    open_route_key,
    route_key,
)


def _facts(tmp_path, relpath, source, field):
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    universal = UniversalGraph()
    AstGrepParser().parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    return {
        node.name: sorted(f["key"] for f in node.metadata[field])
        for node in graph.nodes.values()
        if node.metadata.get(field)
    }


def _serves(tmp_path, relpath, source):
    return _facts(tmp_path, relpath, source, "boundary_serves")


# --- normalize_path, route_key, open_route_key ---------------------------------------------


@pytest.mark.parametrize(
    "raw, want",
    [
        ("/orders/", "/orders"),
        ("/orders?x=1", "/orders"),
        ("/orders#top", "/orders"),
        ("/orders/<int:id>", "/orders/{}"),
        ("/orders/:id", "/orders/{}"),
        ("/orders/[id]", "/orders/{}"),
        ("/orders/{id}/items", "/orders/{}/items"),
        ("/a//b", "/a/b"),
        ("/", "/"),
        ("orders", ""),
        ("", ""),
        ("//host/x", ""),
        ("https://h/x", ""),
    ],
)
def test_normalize_path(raw, want):
    assert normalize_path(raw) == want


def test_route_key_is_empty_for_a_path_that_is_not_a_route():
    assert route_key("get", "https://h/x") == ""
    assert route_key("get", "/x") == "GET /x"


@pytest.mark.parametrize(
    "path, want",
    [
        ("{}/orders", "GET {}/orders"),
        ("{base}/orders?x=1", "GET {}/orders"),
        ("{a}{b}/orders", "GET {}/orders"),
        ("{}/{}", ""),
        ("{}", ""),
        ("/orders", ""),  # a plain path is a route key, not an open key
        ("orders", ""),
    ],
)
def test_open_route_key(path, want):
    assert open_route_key("get", path) == want


# --- next_route_key ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, name, want",
    [
        ("app/api/orders/route.ts", "GET", "GET /api/orders"),
        ("app/route.ts", "GET", "GET /"),
        ("src/app/api/[id]/route.tsx", "DELETE", "DELETE /api/{}"),
        ("app\\api\\x\\route.ts", "POST", "POST /api/x"),  # a Windows path
        ("a/app/b/app/api/route.js", "GET", ""),  # two `app` folders: the URL is not certain
        ("app/(shop)/(eu)/items/route.mjs", "GET", "GET /items"),
        ("app/api/orders/route.ts", "get", ""),  # a verb is upper case
        ("app/api/orders/route.ts", "Get", ""),
        ("app/api/orders/route.ts", "helper", ""),
        ("app/api/orders/page.ts", "GET", ""),
        ("app/api/orders/route.py", "GET", ""),
        ("lib/api/route.ts", "GET", ""),
        ("route.ts", "GET", ""),
        ("app", "GET", ""),
        ("", "GET", ""),
        ("app/api/[...slug]/route.ts", "GET", ""),
        ("app/api/[[...slug]]/route.ts", "GET", ""),
        ("app/_private/route.ts", "GET", ""),
        ("app/@slot/route.ts", "GET", ""),
    ],
)
def test_next_route_key(path, name, want):
    assert next_route_key(path, name) == want


# --- Python prefixes -----------------------------------------------------------------------


def test_a_router_mounted_on_a_router_adds_both_prefixes(tmp_path):
    source = """\
from fastapi import APIRouter, FastAPI

app = FastAPI()
api = APIRouter(prefix="/api")
orders = APIRouter(prefix="/orders")
api.include_router(orders, prefix="/v1")
app.include_router(api)


@orders.get("/{id}")
def one(id: int):
    return id
"""
    assert _serves(tmp_path, "m.py", source) == {"one": ["GET /api/v1/orders/{}"]}


def test_a_mount_cycle_ends(tmp_path):
    source = """\
from fastapi import APIRouter

a = APIRouter(prefix="/a")
b = APIRouter(prefix="/b")
a.include_router(b)
b.include_router(a)


@a.get("/x")
def x():
    return 1
"""
    got = _serves(tmp_path, "m.py", source)
    assert list(got) == ["x"] and got["x"][0].endswith("/x")


def test_a_mount_with_a_prefix_that_is_not_a_literal_adds_nothing(tmp_path):
    source = """\
from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter(prefix="/orders")
app.include_router(router, prefix=PREFIX)


@router.get("/x")
def x():
    return 1
"""
    assert _serves(tmp_path, "m.py", source) == {"x": ["GET /orders/x"]}


def test_register_blueprint_url_prefix_is_added(tmp_path):
    source = """\
from flask import Blueprint, Flask

app = Flask(__name__)
bp = Blueprint("u", __name__)
app.register_blueprint(bp, url_prefix="/users")


@bp.route("/me")
def me():
    return "x"
"""
    assert _serves(tmp_path, "m.py", source) == {"me": ["GET /users/me"]}


def test_a_dotted_receiver_gets_no_prefix(tmp_path):
    source = """\
from fastapi import APIRouter

router = APIRouter(prefix="/orders")


class Api:
    pass


@Api.router.get("/x")
def x():
    return 1
"""
    assert _serves(tmp_path, "m.py", source) == {"x": ["GET /x"]}


def test_route_with_several_methods_serves_each(tmp_path):
    source = """\
from flask import Flask

app = Flask(__name__)


@app.route("/x", methods=["GET", "POST"])
def x():
    return 1


@app.route("/y", methods=["TRACE"])
def y():
    return 1


@app.api_route("/z")
def z():
    return 1
"""
    assert _serves(tmp_path, "m.py", source) == {
        "x": ["GET /x", "POST /x"],
        "y": ["GET /y"],  # a method that is no HTTP verb of ours falls back to GET
        "z": ["GET /z"],
    }


def test_a_decorator_path_that_is_not_a_literal_gives_no_route(tmp_path):
    source = """\
from fastapi import FastAPI

app = FastAPI()


@app.get(PATH)
def a():
    return 1


@app.get()
def b():
    return 1
"""
    assert _serves(tmp_path, "m.py", source) == {}


def test_a_decorator_path_with_no_leading_slash_on_a_plain_receiver_gets_one(tmp_path):
    """Pins the current rule: the prefix join adds the slash (a dotted receiver does not)."""
    source = (
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        '@app.get("no-slash")\ndef c():\n    return 1\n'
    )
    assert _serves(tmp_path, "m.py", source) == {"c": ["GET /no-slash"]}


def test_a_decorator_that_is_not_a_call_gives_no_route(tmp_path):
    source = "@cache\ndef a():\n    return 1\n\n\n@app.get\ndef b():\n    return 1\n"
    assert _serves(tmp_path, "m.py", source) == {}


# --- NestJS --------------------------------------------------------------------------------


def test_nest_method_with_two_verbs_serves_both(tmp_path):
    source = """\
@Controller("x")
export class XController {
  @Get("a")
  @Post("a")
  both() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {
        "both": ["GET /x/a", "POST /x/a"],
    }


def test_nest_verb_with_object_path(tmp_path):
    source = """\
@Controller("x")
export class XController {
  @Get({ path: "a" })
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {"a": ["GET /x/a"]}


def test_nest_verb_with_object_and_no_path_key_serves_the_controller_path(tmp_path):
    source = """\
@Controller("x")
export class XController {
  @Get({ other: 1 })
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {"a": ["GET /x"]}


def test_nest_verb_path_that_is_not_a_literal_leaves_the_method_out(tmp_path):
    source = """\
@Controller("x")
export class XController {
  @Get(PATH)
  a() { return 1; }

  @Get({ path: PATH })
  b() { return 1; }

  @Get("ok")
  c() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {"c": ["GET /x/ok"]}


def test_nest_controller_object_path_that_is_not_a_literal_serves_nothing(tmp_path):
    source = """\
@Controller({ path: PREFIX })
export class XController {
  @Get("a")
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {}


def test_nest_decorators_that_are_no_verb_serve_nothing(tmp_path):
    source = """\
@Controller("x")
export class XController {
  @UseGuards(AuthGuard)
  @All("a")
  a() { return 1; }

  @Injectable
  b() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {}


def test_nest_decorator_of_a_neighbour_method_does_not_leak(tmp_path):
    source = """\
@Controller("x")
export class XController {
  @Get("a")
  a() { return 1; }

  plain() { return 2; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {"a": ["GET /x/a"]}


def test_nest_controller_on_a_class_that_is_not_exported(tmp_path):
    source = """\
@Controller("x")
class XController {
  @Get("a")
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {"a": ["GET /x/a"]}


def test_nest_slashes_in_paths_are_joined_once(tmp_path):
    source = """\
@Controller("/x/")
export class XController {
  @Get("/a/")
  a() { return 1; }
}
"""
    assert _serves(tmp_path, "x.controller.ts", source) == {"a": ["GET /x/a"]}


# --- client facts --------------------------------------------------------------------------


def test_fetch_method_option_that_is_not_a_verb_falls_back_to_get(tmp_path):
    source = """\
export function a() { return fetch("/a", { method: "BREW" }); }
export function b() { return fetch("/b", { method: "delete" }); }
export function c() { return fetch(url, { method: "POST" }); }
"""
    got = _facts(tmp_path, "c.ts", source, "boundary_uses")
    assert got == {"a": ["GET /a"], "b": ["DELETE /b"]}


def test_a_get_on_an_object_that_is_no_client_gives_no_use(tmp_path):
    source = "export function a(map: Map<string, string>) { return map.get('/x'); }\n"
    assert _facts(tmp_path, "c.ts", source, "boundary_uses") == {}


def test_a_server_receiver_with_one_argument_is_no_route(tmp_path):
    source = "export function a() { return app.get('/x'); }\n"
    assert _serves(tmp_path, "s.ts", source) == {}

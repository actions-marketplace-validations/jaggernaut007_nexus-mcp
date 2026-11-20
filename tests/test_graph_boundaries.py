"""`graph` lists the code that reaches a symbol through a boundary (ADR-026).

The repository below goes through the real indexing pipeline: an edge function, a route and
a table, each used by code in another file with no call to it.
"""

import asyncio

import pytest

from nexus_mcp import core_api


@pytest.fixture
def stack_repo(tmp_path):
    root = tmp_path / "repo"
    files = {
        "supabase/functions/send-receipt/index.ts": (
            "export default async function sendReceipt(req: Request) {\n"
            "  return new Response('sent');\n"
            "}\n"
        ),
        "supabase/migrations/001_orders.sql": (
            "create table orders (\n  id uuid primary key,\n  total numeric\n);\n"
        ),
        "api/orders.py": (
            "@app.post('/orders')\n"
            "def create_order(payload):\n"
            "    return payload\n"
        ),
        "web/src/checkout.ts": (
            "export async function pay(order) {\n"
            "  await supabase.functions.invoke('send-receipt', { body: order });\n"
            "  return supabase.from('orders').insert(order);\n"
            "}\n"
            "export async function submit(cart) {\n"
            "  return fetch('/orders', { method: 'POST', body: cart });\n"
            "}\n"
        ),
        "web/src/report.ts": (
            "import { pay } from './checkout';\n"
            "export function monthEnd(orders) { return orders.map(pay); }\n"
            "export function close(o) { return pay(o); }\n"
        ),
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _index(root, tmp_path):
    from tests.conftest import _setup_indexed

    asyncio.run(_setup_indexed(root, tmp_path / ".nexus"))


def test_graph_callers_of_an_edge_function_lists_the_invoking_code(stack_repo, tmp_path):
    _index(stack_repo, tmp_path)
    out = core_api.graph("sendReceipt")
    assert out["boundary_callers"] == {
        "function send-receipt": {"web/src/checkout.ts": ["pay:1"]}
    }
    assert "boundary_note" in out


def test_graph_callers_of_a_route_lists_the_fetching_code(stack_repo, tmp_path):
    _index(stack_repo, tmp_path)
    out = core_api.graph("create_order")
    assert out["boundary_callers"] == {"route POST /orders": {"web/src/checkout.ts": ["submit:5"]}}


def test_graph_callers_of_a_table_lists_the_reading_code(stack_repo, tmp_path):
    _index(stack_repo, tmp_path)
    out = core_api.graph("orders")
    assert out["boundary_callers"] == {"table orders": {"web/src/checkout.ts": ["pay:1"]}}


def test_graph_transitive_follows_a_boundary_to_the_callers_of_the_user(stack_repo, tmp_path):
    _index(stack_repo, tmp_path)
    out = core_api.graph("sendReceipt", transitive=True)
    assert out["impacted_files"]["web/src/checkout.ts"] == ["pay"]
    # `orders.map(pay)` passes `pay` as a value: no call edge, so only `close` is listed.
    assert out["impacted_files"]["web/src/report.ts"] == ["close"]


def test_graph_callers_without_a_boundary_has_no_boundary_fields(stack_repo, tmp_path):
    _index(stack_repo, tmp_path)
    out = core_api.graph("pay")
    assert "boundary_callers" not in out
    assert "boundary_note" not in out

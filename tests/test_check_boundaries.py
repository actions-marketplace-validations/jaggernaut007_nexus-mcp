"""The post-change check on boundaries: routes, edge functions and tables (ADR-026).

Each test makes a real git repository, saves the graph that the real parsers build, edits
one side of a boundary and runs the check. The other side has no call edge, so only the
boundary facts can find it. A Python provider and a TypeScript provider each get a case.
"""

from tests.test_check import _commit_and_index, _git, _kinds, _run, _write, repo  # noqa: F401

PY_ROUTE = {
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
}
EDGE_FUNCTION = {
    "supabase/functions/send-receipt/index.ts": (
        "export default async function handler(req: Request) {\n"
        "  return new Response('sent');\n"
        "}\n"
    ),
    "web/src/checkout.ts": (
        "export async function pay(order) {\n"
        "  return supabase.functions.invoke('send-receipt', { body: order });\n"
        "}\n"
    ),
    "tests/checkout.test.ts": (
        "import { pay } from '../web/src/checkout';\n"
        "describe('pay', () => { pay({}); });\n"
    ),
}
TABLE = {
    "supabase/migrations/001_orders.sql": (
        "create table orders (\n"
        "  id uuid primary key,\n"
        "  total numeric,\n"
        "  note text\n"
        ");\n"
    ),
    "web/src/orders.ts": (
        "export async function load() {\n"
        "  return supabase.from('orders').select('*');\n"
        "}\n"
    ),
}


def test_check_python_route_body_change_is_medium_for_the_ts_user(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_ROUTE)
    body = PY_ROUTE["api/orders.py"].replace("payload\n", "dict(payload)\n")
    _write(repo, {"api/orders.py": body})
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/api.ts", "medium") in _kinds(report)


def test_check_python_route_signature_change_is_high_for_the_ts_user(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_ROUTE)
    _write(repo, {"api/orders.py": (
        "@app.post('/orders')\ndef create_order(payload, tenant):\n    return payload\n"
    )})
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/api.ts", "high") in _kinds(report)


def test_check_python_route_path_change_is_high(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_ROUTE)
    _write(repo, {"api/orders.py": PY_ROUTE["api/orders.py"].replace("/orders", "/v2/orders")})
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/api.ts", "high") in _kinds(report)
    assert "no longer serves it" in next(
        f.reason for f in report.findings if f.kind == "boundary_untouched"
    )


def test_check_python_route_removed_is_high(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_ROUTE)
    _write(repo, {"api/orders.py": "def helper():\n    return 1\n"})
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/api.ts", "high") in _kinds(report)


def test_check_route_user_changed_in_the_same_diff_is_not_a_finding(repo):  # noqa: F811
    store = _commit_and_index(repo, PY_ROUTE)
    _write(repo, {
        "api/orders.py": PY_ROUTE["api/orders.py"].replace("payload\n", "dict(payload)\n"),
        "web/src/api.ts": PY_ROUTE["web/src/api.ts"].replace("cart }", "cart, retry: 1 }"),
    })
    report = _run(repo, store)
    assert not [f for f in report.findings if f.kind.startswith("boundary")]


def test_check_ts_route_provider_change_reaches_a_python_test(repo):  # noqa: F811
    files = {
        "server/routes.ts": (
            "export function createOrder(req, res) { res.send('ok'); }\n"
            "app.post('/orders', createOrder);\n"
        ),
        "tests/test_orders.py": (
            "def test_create(client):\n"
            "    assert client.post('/orders', json={}).status_code == 200\n"
        ),
    }
    store = _commit_and_index(repo, files)
    _write(repo, {"server/routes.ts": files["server/routes.ts"].replace("'ok'", "'done'")})
    report = _run(repo, store)
    assert ("boundary_untouched", "tests/test_orders.py", "medium") in _kinds(report)
    assert "tests/test_orders.py" in report.affected_tests


def test_check_edge_function_change_finds_the_invoking_code_and_its_test(repo):  # noqa: F811
    store = _commit_and_index(repo, EDGE_FUNCTION)
    path = "supabase/functions/send-receipt/index.ts"
    _write(repo, {path: EDGE_FUNCTION[path].replace("'sent'", "'queued'")})
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/checkout.ts", "medium") in _kinds(report)
    assert "tests/checkout.test.ts" in report.affected_tests


def test_check_deno_serve_edge_function_change_finds_the_invoking_code(repo):  # noqa: F811
    files = {
        "supabase/functions/send-receipt/index.ts": (
            "Deno.serve(async (req) => {\n  return new Response('sent');\n});\n"
        ),
        "web/src/checkout.ts": EDGE_FUNCTION["web/src/checkout.ts"],
    }
    store = _commit_and_index(repo, files)
    path = "supabase/functions/send-receipt/index.ts"
    _write(repo, {path: files[path].replace("'sent'", "'queued'")})
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/checkout.ts", "medium") in _kinds(report)


def test_check_table_column_drop_in_a_new_migration_is_high(repo):  # noqa: F811
    store = _commit_and_index(repo, TABLE)
    _write(repo, {
        "supabase/migrations/002_drop_note.sql": "alter table orders drop column note;\n"
    })
    _git(repo, "add", "-A")
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/orders.ts", "high") in _kinds(report)


def test_check_table_add_column_in_a_new_migration_is_medium(repo):  # noqa: F811
    store = _commit_and_index(repo, TABLE)
    _write(repo, {
        "supabase/migrations/002_paid.sql": "alter table orders add column paid boolean;\n"
    })
    _git(repo, "add", "-A")
    report = _run(repo, store)
    assert ("boundary_untouched", "web/src/orders.ts", "medium") in _kinds(report)


def test_check_table_column_removed_from_create_is_high(repo):  # noqa: F811
    store = _commit_and_index(repo, TABLE)
    path = "supabase/migrations/001_orders.sql"
    _write(repo, {path: TABLE[path].replace("  total numeric,\n", "")})
    report = _run(repo, store)
    finding = next(f for f in report.findings if f.kind == "boundary_untouched")
    assert finding.severity == "high"
    assert "total" in finding.reason


def test_check_unrelated_table_migration_has_no_boundary_finding(repo):  # noqa: F811
    store = _commit_and_index(repo, TABLE)
    _write(repo, {"supabase/migrations/002_other.sql": "alter table invoices drop column x;\n"})
    _git(repo, "add", "-A")
    report = _run(repo, store)
    assert not [f for f in report.findings if f.kind.startswith("boundary")]

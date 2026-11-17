"""SQL tables and views as boundary providers (ADR-026).

The SQL reader needs no grammar. The graph holds one `CLASS` node for each create, alter or
drop of a table or view. Only a `create` provides the table to `.from("name")`.
"""

from nexus_mcp.core.graph_models import NodeType, RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.parsing.sql_boundaries import sql_statements

MIGRATION = """\
-- orders and their lines
create table if not exists public.orders (
  id uuid primary key,
  total numeric not null,
  note text,
  constraint total_positive check (total > 0)
);

create view order_totals as select id, total from orders;
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
    found = set()
    for rel in graph.get_relationships_by_type(RelationshipType.DEPENDS_ON):
        source = graph.nodes[rel.source_id]
        target = graph.nodes[rel.target_id]
        found.add((
            source.name, source.location.file_path.rsplit("/", 1)[-1],
            target.name, target.location.file_path.rsplit("/", 1)[-1],
        ))
    return found


def test_sql_statements_read_tables_views_and_columns():
    found = sql_statements(MIGRATION)
    assert [(s["action"], s["what"], s["name"]) for s in found] == [
        ("create", "table", "orders"),
        ("create", "view", "order_totals"),
    ]
    assert found[0]["columns"] == ["id", "total", "note"]
    assert (found[0]["start_line"], found[0]["end_line"]) == (2, 7)


def test_sql_statements_alter_and_drop_are_marked_destructive():
    found = sql_statements(
        "ALTER TABLE orders ADD COLUMN paid boolean;\n"
        'alter table "Orders" drop column note;\n'
        "drop table if exists old_orders;\n"
    )
    assert [(s["action"], s["name"], s["destructive"]) for s in found] == [
        ("alter", "orders", False),
        ("alter", "orders", True),
        ("drop", "old_orders", True),
    ]


def test_sql_statements_ignore_comments_and_dollar_bodies():
    found = sql_statements(
        "-- create table ghost (id int);\n"
        "/* create table phantom (id int); */\n"
        "create function f() returns int as $$ select 1; $$ language sql;\n"
        "create table real_one (id int);\n"
    )
    assert [s["name"] for s in found] == ["real_one"]
    assert found[0]["start_line"] == 4


def test_resolve_boundaries_from_reaches_the_create_table(tmp_path):
    graph = _build(tmp_path, {
        "supabase/migrations/001_orders.sql": MIGRATION,
        "web/src/orders.ts": (
            "export async function loadOrders() {\n"
            "  return supabase.from('orders').select('*');\n"
            "}\n"
        ),
    })
    assert _edges(graph) == {("loadOrders", "orders.ts", "orders", "001_orders.sql")}


def test_resolve_boundaries_python_table_call_reaches_the_view(tmp_path):
    graph = _build(tmp_path, {
        "migrations/001.sql": MIGRATION,
        "svc/report.py": "def totals(db):\n    return db.table('order_totals').select('*')\n",
    })
    assert _edges(graph) == {("totals", "report.py", "order_totals", "001.sql")}


def test_resolve_boundaries_from_without_a_table_has_no_edge(tmp_path):
    graph = _build(tmp_path, {
        "migrations/001.sql": MIGRATION,
        "web/src/list.ts": (
            "export function letters() { return Array.from('abc').concat(rows.from('x')); }\n"
        ),
    })
    assert _edges(graph) == set()


def test_resolve_boundaries_alter_statement_is_not_a_second_provider(tmp_path):
    graph = _build(tmp_path, {
        "migrations/001.sql": MIGRATION,
        "migrations/002.sql": "alter table orders add column paid boolean;\n",
        "web/src/orders.ts": "export function load() { return supabase.from('orders'); }\n",
    })
    assert _edges(graph) == {("load", "orders.ts", "orders", "001.sql")}


def test_sql_file_nodes_carry_the_table_facts(tmp_path):
    graph = _build(tmp_path, {"migrations/001.sql": MIGRATION})
    table = next(
        n for n in graph.nodes.values()
        if n.node_type == NodeType.CLASS and n.name == "orders"
    )
    assert table.metadata["boundary_defines"] == [{"kind": "table", "key": "orders"}]
    assert table.metadata["columns"] == ["id", "total", "note"]
    assert (table.location.start_line, table.location.end_line) == (2, 7)

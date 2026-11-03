"""Tests for question generation in nexus_mcp.evaluate.questions."""

from pathlib import Path

from nexus_mcp.evaluate import questions


def _row(name, rel, root, symbol_type="function", doc="", parent=""):
    return {
        "filepath": str(root / rel),
        "symbol_name": name,
        "symbol_type": symbol_type,
        "parent": parent,
        "docstring": doc,
        "line_start": 1,
        "language": "python",
    }


def test_split_words_snake_and_camel():
    assert questions.split_words("parse_config_file") == ["parse", "config", "file"]
    assert questions.split_words("parseConfigFile") == ["parse", "config", "file"]
    assert questions.split_words("HTTPServer") == ["http", "server"]


def test_first_sentence_stops_at_period():
    assert questions.first_sentence("Merge ranked lists.\nMore text here.") == "Merge ranked lists."
    assert questions.first_sentence("") == ""


def test_generate_makes_all_three_kinds(tmp_path):
    rows = [
        _row(
            "merge_ranked_lists",
            "a/fuse.py",
            tmp_path,
            doc="Merge several ranked result lists into one ordering.",
        ),
        _row(
            "expire_old_notes",
            "a/ttl.py",
            tmp_path,
            doc="Delete notes that passed their time to live.",
        ),
        _row(
            "split_code_chunks",
            "a/chunk.py",
            tmp_path,
            doc="Cut a function into pieces with stable identifiers.",
        ),
    ]
    qs = questions.generate(rows, tmp_path, count=9, seed=1)
    assert {q.kind for q in qs} == {"concept", "described", "lookup"}
    lookup = next(q for q in qs if q.kind == "lookup" and q.symbol == "merge_ranked_lists")
    assert lookup.query == "merge_ranked_lists"
    assert lookup.relevant == str(Path("a/fuse.py"))


def test_generate_concept_removes_the_symbol_name(tmp_path):
    rows = [
        _row(
            "merge_ranked_lists",
            "fuse.py",
            tmp_path,
            doc="merge_ranked_lists merges several ranked lists into one.",
        )
    ]
    qs = [q for q in questions.generate(rows, tmp_path, count=5) if q.kind == "concept"]
    assert qs and "merge_ranked_lists" not in qs[0].query


def test_generate_skips_tests_dunders_and_duplicated_names(tmp_path):
    rows = [
        _row("test_login_flow", "tests/test_auth.py", tmp_path),
        _row("__init__", "pkg/a.py", tmp_path),
        _row("shared_helper_name", "pkg/a.py", tmp_path),
        _row("shared_helper_name", "pkg/b.py", tmp_path),
        _row("unique_function_name", "pkg/c.py", tmp_path),
    ]
    symbols = {q.symbol for q in questions.generate(rows, tmp_path, count=20)}
    assert symbols == {"unique_function_name"}


def test_generate_skips_a_method_whose_bare_name_is_in_two_files(tmp_path):
    # Regression (2026-11-03): UniversalGraph.get_relationships_to and the graph engine's
    # method of the same name had near-identical docstrings, so a question had two right files.
    doc = "Return every relationship that points to the given node in the graph."
    rows = [
        _row("Graph.get_relationships_to", "core/models.py", tmp_path, "method", doc, "Graph"),
        _row("Engine.get_relationships_to", "engines/graph.py", tmp_path, "method", doc, "Engine"),
        _row("Engine.add_weighted_edge", "engines/graph.py", tmp_path, "method", doc, "Engine"),
    ]
    symbols = {q.symbol for q in questions.generate(rows, tmp_path, count=20)}
    assert symbols == {"Engine.add_weighted_edge"}


def test_generate_skips_variables(tmp_path):
    rows = [_row("MAX_RETRY_COUNT", "cfg.py", tmp_path, symbol_type="variable")]
    assert questions.generate(rows, tmp_path) == []


def test_generate_is_seeded(tmp_path):
    rows = [_row(f"do_thing_number_{i}", f"m{i}.py", tmp_path) for i in range(40)]
    first = questions.generate(rows, tmp_path, count=10, seed=3)
    again = questions.generate(rows, tmp_path, count=10, seed=3)
    other = questions.generate(rows, tmp_path, count=10, seed=4)
    assert first == again
    assert first != other


def test_generate_described_uses_parent_words(tmp_path):
    rows = [
        _row("refund_order", "shop/pay.py", tmp_path, symbol_type="method", parent="PaymentGateway")
    ]
    described = [q for q in questions.generate(rows, tmp_path) if q.kind == "described"]
    assert described[0].query == "payment gateway refund order"


def test_generate_returns_nothing_for_empty_rows(tmp_path):
    assert questions.generate([], tmp_path) == []

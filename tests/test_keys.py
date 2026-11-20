"""Tests for the stable symbol keys."""

from nexus_mcp.core.graph_models import NodeType, UniversalGraph
from nexus_mcp.core.keys import fingerprint, natural_key, natural_keys, relative_path
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _nodes(tmp_path, relpath, source):
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)  # a new parser: new random ids
    return [n for n in graph.nodes.values() if n.node_type != NodeType.MODULE]


class TestNaturalKey:
    def test_natural_keys_same_file_gives_the_same_keys_on_two_parses(self, tmp_path):
        source = "class A:\n    def m(self):\n        pass\n\n\ndef f():\n    pass\n"
        first = _nodes(tmp_path, "a.py", source)
        second = _nodes(tmp_path, "a.py", source)
        assert {n.id for n in first}.isdisjoint({n.id for n in second})
        keys_first = set(natural_keys(first, str(tmp_path)).values())
        keys_second = set(natural_keys(second, str(tmp_path)).values())
        assert keys_first == keys_second == {
            "a.py::class::A", "a.py::function::A.m", "a.py::function::f",
        }

    def test_natural_key_method_is_qualified_with_its_class(self, tmp_path):
        nodes = _nodes(tmp_path, "a.ts", "class W {\n  real() {}\n}\n")
        method = next(n for n in nodes if n.name == "real")
        assert natural_key(method, str(tmp_path)) == "a.ts::function::W.real"

    def test_natural_key_ts_arrow_function_and_object_member(self, tmp_path):
        nodes = _nodes(
            tmp_path, "svc.ts",
            "export const handler = () => 1;\nexport const api = { get() { return 2; } };\n",
        )
        keys = set(natural_keys(nodes, str(tmp_path)).values())
        assert {"svc.ts::function::handler", "svc.ts::function::api.get"} <= keys

    def test_natural_keys_a_repeated_key_gets_ordinals_by_line(self, tmp_path):
        nodes = _nodes(tmp_path, "a.py", "def f():\n    pass\n\n\ndef f():\n    pass\n")
        keys = natural_keys(nodes, str(tmp_path))
        by_line = {n.location.start_line: keys[n.id] for n in nodes}
        assert by_line == {1: "a.py::function::f#1", 5: "a.py::function::f#2"}

    def test_natural_keys_a_rename_is_a_new_key(self, tmp_path):
        before = natural_keys(_nodes(tmp_path, "a.py", "def f():\n    pass\n"), str(tmp_path))
        after = natural_keys(_nodes(tmp_path, "a.py", "def g():\n    pass\n"), str(tmp_path))
        assert set(before.values()).isdisjoint(set(after.values()))

    def test_natural_keys_a_move_between_files_is_a_new_key(self, tmp_path):
        one = natural_keys(_nodes(tmp_path, "a.py", "def f():\n    pass\n"), str(tmp_path))
        two = natural_keys(_nodes(tmp_path, "b.py", "def f():\n    pass\n"), str(tmp_path))
        assert set(one.values()).isdisjoint(set(two.values()))

    def test_natural_keys_same_name_in_two_files_needs_no_ordinal(self, tmp_path):
        nodes = _nodes(tmp_path, "a.py", "def f():\n    pass\n")
        nodes += _nodes(tmp_path, "b.py", "def f():\n    pass\n")
        assert sorted(natural_keys(nodes, str(tmp_path)).values()) == [
            "a.py::function::f", "b.py::function::f",
        ]


class TestRelativePath:
    def test_relative_path_under_the_root(self):
        assert relative_path("/p/src/a.py", "/p") == "src/a.py"

    def test_relative_path_outside_the_root_stays_whole(self):
        assert relative_path("/q/a.py", "/p") == "/q/a.py"

    def test_relative_path_backslashes_become_slashes(self):
        assert relative_path("src\\a.py") == "src/a.py"


class TestFingerprint:
    def test_fingerprint_ignores_trailing_whitespace(self):
        assert fingerprint("def f():  \n    pass\n\n") == fingerprint("def f():\n    pass")

    def test_fingerprint_changes_with_the_code(self):
        assert fingerprint("def f():\n    pass") != fingerprint("def f():\n    return 1")

    def test_fingerprint_has_a_scheme_prefix(self):
        assert fingerprint("x").startswith("sha256:")

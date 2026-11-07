"""Class bases, `Class.method` lookup and subclass overrides in `graph` (issue #31).

On Django, `graph("Field.validate")` gave no callers and `find_symbol("Field.validate")`
gave "not found": a method is in the graph by its bare name. The graph had no bases, so
nothing listed the subclasses that override the method, and the agent ran 5 to 8 greps.
"""

import asyncio

import pytest

from nexus_mcp import core_api
from nexus_mcp.core.graph_models import NodeType, UniversalGraph
from nexus_mcp.engines.live_grep import LiveGrepEngine
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _bases(tmp_path, filename, source):
    path = tmp_path / filename
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)
    return {
        n.name: n.metadata.get("bases", [])
        for n in graph.nodes.values() if n.node_type == NodeType.CLASS
    }


def test_class_bases_python_keeps_last_part_and_skips_keywords(tmp_path):
    source = (
        "class A(models.Field, Mixin, metaclass=Meta):\n    pass\n\n"
        "class B(Generic[T]):\n    pass\n\nclass C:\n    pass\n"
    )
    assert _bases(tmp_path, "m.py", source) == {
        "A": ["Field", "Mixin"], "B": ["Generic"], "C": [],
    }


def test_class_bases_typescript_extends_and_implements(tmp_path):
    source = "class A extends Base<T> implements I { }\nclass B extends ns.Other {}\n"
    assert _bases(tmp_path, "m.ts", source) == {"A": ["Base", "I"], "B": ["Other"]}


def test_class_bases_javascript_skips_a_computed_base(tmp_path):
    source = "class A extends Base { }\nclass B extends mix(C) {}\nclass D {}\n"
    assert _bases(tmp_path, "m.js", source) == {"A": ["Base"], "B": [], "D": []}


@pytest.fixture
def fields_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "fields.py").write_text(
        "class Field:\n"
        "    def validate(self, value):\n        return value\n\n"
        "    def clean(self, value):\n        return self.validate(value)\n\n\n"
        "class CharField(Field):\n"
        "    def validate(self, value):\n        return super().validate(value)\n\n\n"
        "class SlugField(CharField):\n"
        "    def validate(self, value):\n        return value\n\n\n"
        "class IntegerField(Field):\n"
        "    def to_python(self, value):\n        return int(value)\n\n\n"
        "class Form:\n"
        "    def validate(self, data):\n        return data\n"
    )
    (root / "pkg" / "array.py").write_text(
        "from pkg.fields import Field\n\n\n"
        "class ArrayField(Field):\n"
        "    def validate(self, value):\n        return value\n"
    )
    (root / "docs").mkdir()
    (root / "docs" / "fields.txt").write_text("Call validate to check a value.\n")
    return root


def _index(root, tmp_path):
    from tests.conftest import _setup_indexed

    asyncio.run(_setup_indexed(root, tmp_path / ".nexus"))


def test_graph_qualified_method_lists_callers_and_overrides(fields_repo, tmp_path):
    _index(fields_repo, tmp_path)
    out = core_api.graph("Field.validate")
    assert out["callers"] == {"pkg/fields.py": ["clean:5"]}
    assert out["overrides"]["files"] == {
        "pkg/array.py": ["ArrayField.validate:5"],
        "pkg/fields.py": ["CharField.validate:10", "SlugField.validate:15"],
    }
    assert out["overrides"]["total"] == 3
    # IntegerField inherits validate without its own: a subclass, not an override.
    assert out["overrides"]["subclasses"] == 4
    assert "Form.validate" not in str(out["overrides"])


def test_graph_overrides_of_a_subclass_method_and_transitive(fields_repo, tmp_path):
    _index(fields_repo, tmp_path)
    out = core_api.graph("CharField.validate")
    assert out["overrides"]["files"] == {"pkg/fields.py": ["SlugField.validate:15"]}
    deep = core_api.graph("Field.validate", transitive=True)
    assert deep["overrides"]["total"] == 3


def test_graph_bare_name_with_many_owners_has_no_overrides(fields_repo, tmp_path):
    _index(fields_repo, tmp_path)
    out = core_api.graph("validate")
    assert "overrides" not in out
    assert out["total"] >= 1


def test_graph_method_without_subclass_override_has_no_overrides(fields_repo, tmp_path):
    _index(fields_repo, tmp_path)
    assert "overrides" not in core_api.graph("Form.validate")
    assert "overrides" not in core_api.graph("IntegerField.to_python")


def test_find_symbol_qualified_method_returns_its_code(fields_repo, tmp_path):
    _index(fields_repo, tmp_path)
    out = core_api.find_symbol("Field.validate, SlugField::validate")
    places = [(s["file"], s["start_line"]) for s in out["symbols"]]
    assert places == [("pkg/fields.py", 2), ("pkg/fields.py", 15)]
    assert "def validate(self, value)" in out["symbols"][0]["code"]


def test_qualified_methods_needs_two_parts(fields_repo, tmp_path):
    _index(fields_repo, tmp_path)
    from nexus_mcp.state import get_state

    graph = get_state().graph_engine
    assert core_api._qualified_methods(graph, "validate") == []
    assert core_api._qualified_methods(graph, "Nope.validate") == []
    assert len(core_api._qualified_methods(graph, "pkg.Field.validate")) == 1


def test_references_count_non_code_files_without_their_lines(fields_repo):
    refs = LiveGrepEngine(str(fields_repo)).references("validate")
    assert "docs/fields.txt" not in refs["files"]
    assert refs["non_code"] == {"files": 1, "lines": 1}
    assert refs["total_files"] == 3  # the totals still count every file

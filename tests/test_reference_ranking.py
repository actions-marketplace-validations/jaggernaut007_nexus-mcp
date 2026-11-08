"""`graph` references: class files first, other `def` lines only counted (issue #31).

On Django `graph("SQLCompiler.as_sql")` listed `contrib/gis` first, and 91 of its 283 lines
were `def as_sql` of other classes. The line budget ran out before `db/models/sql`.
"""

import asyncio

import pytest

from nexus_mcp import core_api
from nexus_mcp.engines.live_grep import LiveGrepEngine


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    # `aaa.py` sorts first by path but does not name the class.
    (root / "pkg" / "aaa.py").write_text(
        "class Other:\n    def run(self):\n        return 1\n\n\n"
        "def other(x):\n    return x.run()\n"
    )
    (root / "pkg" / "base.py").write_text(
        "class Base:\n    def run(self):\n        return 0\n\n"
        "    def go(self):\n        return self.run()\n\n\n"
        "class Child(Base):\n    def run(self):\n        return 2\n"
    )
    (root / "pkg" / "zzz.py").write_text(
        "from pkg.base import Child\n\n\ndef start():\n    return Child().run()\n"
    )
    return root


def test_references_related_names_rank_class_files_first(repo):
    refs = LiveGrepEngine(str(repo)).references("Base.run", related_names=["Base", "Child"])
    assert list(refs["files"]) == ["pkg/base.py", "pkg/zzz.py", "pkg/aaa.py"]


def test_references_skip_definitions_counts_them(repo):
    refs = LiveGrepEngine(str(repo)).references("run", skip_definitions=True)
    assert refs["definitions"] == 3
    assert refs["files"]["pkg/base.py"] == ["6: return self.run()"]
    assert refs["files"]["pkg/aaa.py"] == ["7: return x.run()"]
    assert refs["total_lines"] == 6  # the totals still count every line


def test_references_without_options_keep_path_order_and_definitions(repo):
    refs = LiveGrepEngine(str(repo)).references("run")
    assert list(refs["files"]) == ["pkg/aaa.py", "pkg/base.py", "pkg/zzz.py"]
    assert "definitions" not in refs
    assert "2: def run(self):" in refs["files"]["pkg/aaa.py"]


def test_references_definition_pattern_matches_js_function(tmp_path):
    (tmp_path / "a.js").write_text(
        "export async function load(x) {\n  return x;\n}\nload(1);\nconst load2 = load;\n"
    )
    refs = LiveGrepEngine(str(tmp_path)).references("load", skip_definitions=True)
    assert refs["definitions"] == 1
    assert refs["files"]["a.js"] == ["4: load(1);", "5: const load2 = load;"]


@pytest.mark.parametrize("source,name", [
    ("<?php\nclass A {\n  public static function load($x) {}\n}\n", "a.php"),
    ("impl A {\n    pub(crate) fn load(&self) {}\n}\n", "a.rs"),
    ("func (r *A) load() {}\n", "a.go"),
    ("export default async function load() {}\n", "a.ts"),
])
def test_references_skip_definitions_knows_other_languages(tmp_path, source, name):
    (tmp_path / name).write_text(source + "load();\n")
    refs = LiveGrepEngine(str(tmp_path)).references("load", skip_definitions=True)
    assert refs["definitions"] == 1
    assert list(refs["files"].values()) == [[f"{source.count(chr(10)) + 1}: load();"]]


def test_references_keep_templates_and_config_after_code(tmp_path):
    (tmp_path / "views.py").write_text("def render_total(cart):\n    return 1\n")
    (tmp_path / "cart.html").write_text("{{ render_total(cart) }}\n")
    (tmp_path / "Cart.vue").write_text("<p>{{ render_total(cart) }}</p>\n")
    (tmp_path / "pyproject.toml").write_text('tool = "app.views:render_total"\n')
    (tmp_path / "NOTES.md").write_text("render_total sums the cart\n")
    (tmp_path / "fr.po").write_text('msgid "render_total"\n')
    refs = LiveGrepEngine(str(tmp_path)).references("render_total")
    assert list(refs["files"]) == ["views.py", "Cart.vue", "cart.html", "pyproject.toml"]
    assert refs["non_code"] == {"files": 2, "lines": 2}


def test_graph_method_references_rank_family_and_skip_defs(repo, tmp_path):
    from tests.conftest import _setup_indexed

    asyncio.run(_setup_indexed(repo, tmp_path / ".nexus"))
    refs = core_api.graph("Base.run")["references"]
    assert list(refs["files"])[:2] == ["pkg/base.py", "pkg/zzz.py"]
    assert refs["definitions"] == 3
    assert not any("def run" in line for lines in refs["files"].values() for line in lines)


def test_graph_bare_name_of_many_classes_keeps_path_order(repo, tmp_path):
    from tests.conftest import _setup_indexed

    asyncio.run(_setup_indexed(repo, tmp_path / ".nexus"))
    refs = core_api.graph("run")["references"]
    # `run` is a method of Other, Base and Child: no one class to rank by, and no
    # `overrides` list, so the `def` lines stay: they are the only list of the methods.
    assert list(refs["files"]) == ["pkg/aaa.py", "pkg/base.py", "pkg/zzz.py"]
    assert "definitions" not in refs
    assert "2: def run(self):" in refs["files"]["pkg/aaa.py"]


def test_graph_php_method_references_keep_override_definitions(tmp_path):
    # PHP class nodes carry no base classes, so `graph` has no `overrides` list for a PHP
    # method. The `function save` line of the subclass must then stay in the references.
    from tests.conftest import _setup_indexed

    root = tmp_path / "repo"
    root.mkdir()
    (root / "Model.php").write_text(
        "<?php\nclass Model {\n    public function save() {\n        return 1;\n    }\n}\n"
    )
    (root / "User.php").write_text(
        "<?php\nclass User extends Model {\n    public function save() {\n"
        "        return parent::save();\n    }\n}\n"
    )
    asyncio.run(_setup_indexed(root, tmp_path / ".nexus"))
    refs = core_api.graph("Model.save")["references"]
    lines = [line for file_lines in refs["files"].values() for line in file_lines]
    assert "definitions" not in refs
    assert any("function save" in line for line in refs["files"].get("User.php", []))
    assert len(lines) == 3


def test_class_family_needs_one_owner(repo, tmp_path):
    from nexus_mcp.state import get_state
    from tests.conftest import _setup_indexed

    asyncio.run(_setup_indexed(repo, tmp_path / ".nexus"))
    state = get_state()
    base_run = core_api._qualified_methods(state.graph_engine, "Base.run")
    assert core_api._class_family(state, base_run) == ["Base", "Child"]
    all_run = state.graph_engine.find_nodes_by_name("run", exact=True)
    assert core_api._class_family(state, all_run) == []

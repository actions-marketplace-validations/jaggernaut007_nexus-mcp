"""C++ symbol names: out-of-line methods, nested classes, `::` lookups (Chromium `base/`).

The fixtures are cut down from `base/memory/weak_ptr.h`, `weak_ptr.cc` and
`base/at_exit.h`. A benchmark run on Chromium showed the defects: `GetRef` was indexed
under its return type `WeakReference`, and `WeakReference::Flag` was not found.
"""

import asyncio

import pytest

from nexus_mcp import core_api
from nexus_mcp.core.models import SymbolType
from nexus_mcp.parsing.treesitter_parser import TreeSitterParser
from tests.conftest import _setup_indexed

WEAK_PTR_H = """\
// Weak pointers are pointers to an object that do not affect its lifetime.
namespace base {
class SequencedTaskRunner;
namespace internal {
class WeakReference {
 public:
  class Flag {
   public:
    Flag();
    void Invalidate();
    bool IsValid() const { return valid_marker_ != 0; }
   private:
    int valid_marker_ = 1;
  };
  WeakReference();
  explicit WeakReference(const scoped_refptr<Flag>& flag);
  ~WeakReference();
  WeakReference& operator=(WeakReference&& other) { return *this; }
  bool IsValid() const;
  template <typename T>
  T* Cast() const { return static_cast<T*>(nullptr); }
 private:
  scoped_refptr<const Flag> flag_;
};
class WeakReferenceOwner {
 public:
  WeakReference GetRef() const;
};
}  // namespace internal
}  // namespace base
"""

WEAK_PTR_CC = """\
namespace base {
namespace internal {
WeakReference::Flag::Flag() = default;
void WeakReference::Flag::Invalidate() { valid_marker_ = 0; }
WeakReference::WeakReference() = default;
WeakReference::WeakReference(const scoped_refptr<Flag>& flag) : flag_(flag) {}
WeakReference::~WeakReference() = default;
bool WeakReference::IsValid() const { return flag_ && flag_->IsValid(); }
WeakReference WeakReferenceOwner::GetRef() const {
  return WeakReference(flag_for_get_ref_);
}
bool operator==(const WeakReference& a, const WeakReference& b) { return true; }
template <typename T>
T* MakeRaw(T* p) { return p; }
template <typename T>
void Holder<T>::Reset() { held_ = nullptr; }
const char* FreeName(int x) { return "free"; }
}  // namespace internal
}  // namespace base
"""

AT_EXIT_H = """\
namespace base {
class AtExitManager {
 public:
  static void RegisterCallback(AtExitCallbackType func, void* param) {
    RegisterTask(BindOnce(func, param));
  }
};
}  // namespace base
"""

PLAIN_C_H = """\
#ifndef PLAIN_H
#define PLAIN_H
typedef unsigned long size_type;
size_type count_items(const char* s);
#endif
"""

PLAIN_C = """\
typedef unsigned long size_type;
size_type count_items(const char* s) { return 0; }
"""


@pytest.fixture
def parser():
    return TreeSitterParser()


@pytest.fixture
def cpp_tree(tmp_path):
    (tmp_path / "weak_ptr.h").write_text(WEAK_PTR_H)
    (tmp_path / "weak_ptr.cc").write_text(WEAK_PTR_CC)
    (tmp_path / "at_exit.h").write_text(AT_EXIT_H)
    return tmp_path


def _symbols(parser, path):
    """(qualified name, type) of each symbol of a file, without the module symbol."""
    parsed = parser.parse_file(str(path))
    assert parsed.error is None, parsed.error
    return [(s.qualified_name, s.type) for s in parsed.symbols if s.type != SymbolType.MODULE]


def _by_line(parser, path, line):
    return [
        s for s in parser.parse_file(str(path)).symbols
        if s.line_start == line and s.type != SymbolType.MODULE
    ]


class TestOutOfLineDefinitions:
    def test_parse_file_out_of_line_method_named_after_its_function_not_its_return_type(
        self, parser, cpp_tree
    ):
        (sym,) = _by_line(parser, cpp_tree / "weak_ptr.cc", 9)
        assert sym.name == "GetRef"
        assert sym.parent == "WeakReferenceOwner"
        assert sym.type == SymbolType.METHOD

    def test_parse_file_out_of_line_methods_are_all_indexed(self, parser, cpp_tree):
        names = [n for n, _ in _symbols(parser, cpp_tree / "weak_ptr.cc")]
        for expected in (
            "WeakReference.Flag.Flag",
            "WeakReference.Flag.Invalidate",
            "WeakReference.WeakReference",
            "WeakReference.~WeakReference",
            "WeakReference.IsValid",
            "WeakReferenceOwner.GetRef",
        ):
            assert expected in names, names

    def test_parse_file_constructor_and_destructor_are_methods(self, parser, cpp_tree):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.cc"))
        assert syms["WeakReference.WeakReference"] == SymbolType.METHOD
        assert syms["WeakReference.~WeakReference"] == SymbolType.METHOD

    def test_parse_file_free_operator_template_and_pointer_functions_keep_their_names(
        self, parser, cpp_tree
    ):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.cc"))
        assert syms.get("operator==") == SymbolType.FUNCTION, syms
        assert syms.get("MakeRaw") == SymbolType.FUNCTION, syms
        assert syms.get("FreeName") == SymbolType.FUNCTION, syms
        assert "Holder.Reset" in syms, syms  # template arguments leave the scope
        assert "T" not in syms and "WeakReference" not in syms, syms


class TestHeaders:
    def test_parse_file_cpp_header_nested_class_has_its_outer_class_as_parent(
        self, parser, cpp_tree
    ):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.h"))
        assert syms.get("WeakReference") == SymbolType.CLASS, syms
        assert syms.get("WeakReference.Flag") == SymbolType.CLASS, syms
        assert syms.get("WeakReferenceOwner") == SymbolType.CLASS, syms
        assert syms.get("WeakReference.Flag.IsValid") == SymbolType.METHOD, syms

    def test_parse_file_cpp_header_inline_operator_and_template_methods(self, parser, cpp_tree):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.h"))
        assert syms.get("WeakReference.operator=") == SymbolType.METHOD, syms
        assert syms.get("WeakReference.Cast") == SymbolType.METHOD, syms

    def test_parse_file_cpp_header_has_no_keyword_or_forward_declaration_symbols(
        self, parser, cpp_tree
    ):
        names = [n for n, _ in _symbols(parser, cpp_tree / "weak_ptr.h")]
        assert "namespace" not in names and "class" not in names, names
        assert "SequencedTaskRunner" not in names, names  # `class X;` declares, not defines
        assert names.count("WeakReference") == 1, names

    def test_parse_file_cpp_header_is_parsed_as_cpp(self, parser, cpp_tree):
        assert parser.parse_file(str(cpp_tree / "weak_ptr.h")).language == "cpp"

    def test_parse_file_plain_c_header_stays_c(self, parser, tmp_path):
        path = tmp_path / "plain.h"
        path.write_text(PLAIN_C_H)
        assert parser.parse_file(str(path)).language == "c"

    def test_parse_file_c_function_named_after_its_function_not_its_typedef_return(
        self, parser, tmp_path
    ):
        path = tmp_path / "plain.c"
        path.write_text(PLAIN_C)
        names = [n for n, _ in _symbols(parser, path)]
        assert "count_items" in names and "size_type" not in names, names


class TestQualifiedLookup:
    def _index(self, codebase, tmp_path):
        asyncio.run(_setup_indexed(codebase, tmp_path / ".nexus"))

    def test_find_symbol_cpp_nested_class_by_scope_operator(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("WeakReference::Flag")
        assert "error" not in result, result
        assert result["symbols"][0]["type"] == "class"
        assert "void Invalidate();" in result["symbols"][0]["code"]

    def test_find_symbol_cpp_method_by_scope_operator(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("AtExitManager::RegisterCallback, WeakReferenceOwner::GetRef")
        assert "not_found" not in result, result
        codes = [s["code"] for s in result["symbols"]]
        assert any("RegisterTask(BindOnce(func, param))" in c for c in codes), codes
        assert any("flag_for_get_ref_" in c for c in codes), codes

    def test_find_symbol_cpp_bare_class_name_gives_the_class_first(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("WeakReference")
        assert "error" not in result, result
        first = result["symbols"][0]
        assert first["type"] == "class" and first["file"].endswith("weak_ptr.h"), result
        assert not any("flag_for_get_ref_" in s.get("code", "") for s in result["symbols"])

    def test_find_symbol_cpp_inner_scope_suffix_matches(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("Flag::Invalidate")
        assert "error" not in result, result
        assert "valid_marker_ = 0" in result["symbols"][0]["code"]

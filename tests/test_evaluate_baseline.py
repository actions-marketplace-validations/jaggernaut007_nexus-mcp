"""Tests for the Grep and Read baseline in nexus_mcp.evaluate.baseline."""

from nexus_mcp.evaluate import baseline


def test_keywords_drop_stopwords_short_words_and_repeats():
    assert baseline.keywords("Merge the ranked lists, and merge them with ranked") == [
        "merge",
        "ranked",
        "lists",
    ]


def _project(tmp_path):
    (tmp_path / "fuse.py").write_text("def merge_ranked_lists():\n    return 1\n")
    (tmp_path / "other.py").write_text("def unrelated():\n    return merge\n")
    return tmp_path


def test_rank_orders_by_distinct_words_matched(tmp_path):
    root = _project(tmp_path)
    ranked, listing = baseline.rank(root, "merge ranked lists")
    assert ranked[0] == "fuse.py"
    assert "other.py" in ranked
    assert listing.splitlines()[0] == "fuse.py"


def test_rank_skips_the_nexus_folder(tmp_path):
    root = _project(tmp_path)
    (root / ".nexus").mkdir()
    (root / ".nexus" / "dump.txt").write_text("merge ranked lists")
    ranked, _ = baseline.rank(root, "merge ranked lists")
    assert all(not p.startswith(".nexus") for p in ranked)


def test_read_cost_counts_chars_over_four(tmp_path):
    (tmp_path / "a.py").write_text("x" * 400)
    assert baseline.read_cost(tmp_path, ["a.py"]) == 100
    assert baseline.read_cost(tmp_path, ["missing.py"]) == 0


def test_read_cost_caps_lines(tmp_path):
    (tmp_path / "big.py").write_text("abcd\n" * (baseline.MAX_LINES_PER_READ + 500))
    assert baseline.read_cost(tmp_path, ["big.py"]) == baseline.MAX_LINES_PER_READ * 5 // 4


def test_tokens_to_answer_stops_at_the_right_file(tmp_path):
    (tmp_path / "a.py").write_text("x" * 400)
    (tmp_path / "b.py").write_text("x" * 400)
    (tmp_path / "c.py").write_text("x" * 400)
    early = baseline.tokens_to_answer(tmp_path, ["a.py", "b.py", "c.py"], "", "a.py")
    miss = baseline.tokens_to_answer(tmp_path, ["a.py", "b.py", "c.py"], "", "zzz.py")
    assert early == 100
    assert miss == 300

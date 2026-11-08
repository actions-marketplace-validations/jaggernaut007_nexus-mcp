"""Optional tools: names are case-insensitive, and stored memories keep the `memory` tool.

2.4.0 hides `status`, `analyze` and `memory` by default. A user of 2.2.x who stored
memories must still reach them after the upgrade.
"""

import asyncio

import pytest

from nexus_mcp.config import Settings, reset_settings


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("NEXUS_EXTRA_TOOLS", "")
    reset_settings()
    yield tmp_path / "store"
    reset_settings()


def _store_one_memory(storage):
    import lancedb

    db = lancedb.connect(str(storage / "lancedb"))
    db.create_table("memories", data=[{"id": "m1", "content": "use tabs"}])


def test_enabled_optional_tools_is_case_insensitive(storage, monkeypatch):
    monkeypatch.setenv("NEXUS_EXTRA_TOOLS", "Memory, ANALYZE")
    assert Settings().enabled_optional_tools() == {"memory", "analyze"}


def test_has_stored_memories_false_without_a_table(storage):
    assert Settings().has_stored_memories() is False


def test_has_stored_memories_false_for_an_empty_table(storage):
    import lancedb
    import pyarrow as pa

    db = lancedb.connect(str(storage / "lancedb"))
    db.create_table("memories", schema=pa.schema([pa.field("id", pa.string())]))
    assert Settings().has_stored_memories() is False


def test_has_stored_memories_true_with_a_row(storage):
    _store_one_memory(storage)
    assert Settings().has_stored_memories() is True


def test_create_server_shows_memory_when_memories_exist(storage):
    from nexus_mcp import server

    names = {t.name for t in asyncio.run(server.create_server().list_tools())}
    assert "memory" not in names and "analyze" not in names

    _store_one_memory(storage)
    reset_settings()
    names = {t.name for t in asyncio.run(server.create_server().list_tools())}
    assert "memory" in names and "analyze" not in names

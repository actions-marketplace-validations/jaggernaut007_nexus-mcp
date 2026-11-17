"""Tests for how EmbeddingService loads a model, and for model-change detection.

Loading: the boundary is the model file. A fake ONNX Runtime session stands in for it, and
a fake download function stands in for the network. The tokenizer is a real one that the
test builds. The SentenceTransformer class (the `torch` extra) is replaced by a recorder,
because those tests check the arguments the service passes, not the model weights.
Model-change detection uses the real pipeline and a real index on disk.

Requirement changes in 3.0.0 (ADR-026): the encoder is native ONNX, `jina-code` and
`trust_remote_code` are gone, and the torch backend is the optional model `bge-small-en-torch`.
"""

import json
import math
import re
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.core.exceptions import ConfigurationError
from nexus_mcp.indexing import embedding_service as es
from nexus_mcp.indexing.pipeline import IndexingPipeline

# ---------------------------------------------------------------------------
# The ONNX encoder, with a fake session and a real tokenizer
# ---------------------------------------------------------------------------

_VOCAB = {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3, "a": 4, "b": 5, "c": 6, "d": 7, "e": 8}


def _write_tokenizer(path):
    from tokenizers import Tokenizer, models, pre_tokenizers
    from tokenizers.processors import TemplateProcessing

    tok = Tokenizer(models.WordLevel(_VOCAB, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.post_processor = TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    tok.save(str(path))


class _FakeSession:
    """hidden[b, t, d] = input_id[b, t] * (d + 1): a pooled value is easy to compute by hand."""

    def __init__(self, input_names=("input_ids", "attention_mask", "token_type_ids")):
        self.input_names = input_names
        self.feeds = []

    def get_inputs(self):
        return [types.SimpleNamespace(name=n) for n in self.input_names]

    def run(self, _outputs, feed):
        self.feeds.append(feed)
        ids = feed["input_ids"].astype(np.float32)
        return [ids[:, :, None] * np.arange(1, 4, dtype=np.float32)]


@pytest.fixture
def onnx_boundary(tmp_path, monkeypatch):
    """Patch the network and the ONNX Runtime session. Returns (session, downloads)."""
    import huggingface_hub
    import onnxruntime

    tokenizer_path = tmp_path / "tokenizer.json"
    _write_tokenizer(tokenizer_path)
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(b"not a real model")
    session = _FakeSession()
    downloads = []

    def fake_download(repo, filename, **kwargs):
        downloads.append((repo, filename, kwargs))
        return str(tokenizer_path if filename == "tokenizer.json" else model_path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: session)
    return session, downloads


def _encoder(pooling="cls", max_seq_length=512, **kwargs):
    return es.OnnxEncoder("org/model", "onnx/m.onnx", pooling, max_seq_length, **kwargs)


def test_encode_cls_pooling_takes_the_first_token(onnx_boundary):
    # "a b" is [CLS] a b [SEP] = ids 2 4 5 3. The first token is 2, so the vector is 2 * (1, 2, 3).
    vectors = _encoder("cls").encode(["a b"], normalize=False)
    assert vectors.tolist() == [[2.0, 4.0, 6.0]]


@pytest.fixture
def batches_of_8(monkeypatch):
    """The default cap is 1, so no text is padded. Raise it to test padding and ordering."""
    monkeypatch.setattr(es, "ONNX_MAX_BATCH", 8)


def test_encode_mean_pooling_ignores_padding(onnx_boundary, batches_of_8):
    encoder = _encoder("mean")
    alone = encoder.encode(["a b"], normalize=False)
    batched = encoder.encode(["a b", "a b c d e a b c d e"], normalize=False)
    assert alone[0].tolist() == pytest.approx([3.5, 7.0, 10.5])  # (2+4+5+3)/4 * (1, 2, 3)
    assert batched[0].tolist() == pytest.approx(alone[0].tolist())  # padded to 12, same result


def test_encode_normalizes_to_unit_length(onnx_boundary):
    vectors = _encoder("cls").encode(["a b", "c d e"], normalize=True)
    for row in vectors:
        assert math.isclose(float(np.linalg.norm(row)), 1.0, rel_tol=1e-5)


def test_encode_returns_vectors_in_the_input_order(onnx_boundary, batches_of_8):
    """The encoder sorts texts by length to cut padding. The caller must not see that."""
    encoder = _encoder("mean")
    texts = ["a b c d e a b c d e", "a", "a b c", "a b c d e a b", "b"]
    together = encoder.encode(texts, batch_size=2, normalize=False)
    for row, text in zip(together, texts):
        assert row.tolist() == pytest.approx(encoder.encode([text], normalize=False)[0].tolist())


def test_encode_caps_the_batch_at_the_onnx_maximum(onnx_boundary, batches_of_8):
    session, _ = onnx_boundary
    _encoder().encode(["a"] * 20, batch_size=64)
    sizes = [feed["input_ids"].shape[0] for feed in session.feeds]
    assert max(sizes) <= es.ONNX_MAX_BATCH
    assert len(sizes) == math.ceil(20 / es.ONNX_MAX_BATCH)


def test_by_default_every_text_runs_alone_in_its_own_onnx_call(onnx_boundary):
    """The int8 model quantizes its activations with one scale for the whole batch, so a
    text's vector changed with the other texts in its batch (cosine 0.9933 alone against in
    a batch). One text in each call makes a vector depend on its own text only, and it halves
    the memory of an index. A batch size from the caller does not change that."""
    session, _ = onnx_boundary
    assert es.ONNX_MAX_BATCH == 1
    _encoder().encode(["a b", "c d e", "a"], batch_size=32)
    assert [feed["input_ids"].shape[0] for feed in session.feeds] == [1, 1, 1]


def test_encode_feeds_only_the_inputs_the_model_declares(onnx_boundary):
    session, _ = onnx_boundary
    session.input_names = ("input_ids", "attention_mask")  # a ModernBERT-style graph
    _encoder().encode(["a b"])
    assert set(session.feeds[0]) == {"input_ids", "attention_mask"}


def test_encode_truncates_at_the_max_sequence_length(onnx_boundary):
    session, _ = onnx_boundary
    _encoder(max_seq_length=6).encode(["a b c d e a b c d e a b c d e"])
    assert session.feeds[0]["input_ids"].shape == (1, 6)
    assert session.feeds[0]["input_ids"][0, -1] == _VOCAB["[SEP]"]


def test_encode_of_no_texts_gives_an_empty_array(onnx_boundary):
    assert _encoder().encode([]).shape == (0, 0)


def test_encoder_rejects_an_unknown_pooling(onnx_boundary):
    with pytest.raises(ValueError, match="pooling"):
        _encoder("last")


def test_encoder_downloads_the_model_and_tokenizer_at_the_pinned_revision(onnx_boundary):
    _, downloads = onnx_boundary
    _encoder(revision="abc123")
    assert [(repo, name) for repo, name, _ in downloads] == [
        ("org/model", "onnx/m.onnx"),
        ("org/model", "tokenizer.json"),
    ]
    assert all(kwargs["revision"] == "abc123" for _, _, kwargs in downloads)


# ---------------------------------------------------------------------------
# The service and the registry
# ---------------------------------------------------------------------------


def test_default_model_is_the_pinned_int8_onnx_file():
    config = es.EMBEDDING_MODELS[es.DEFAULT_MODEL]
    assert config["backend"] == "onnx"
    assert config["onnx_file"].endswith("model_quantized.onnx")
    assert re.fullmatch(r"[0-9a-f]{40}", config["revision"])


def test_service_loads_the_onnx_encoder_with_the_registry_values(onnx_boundary):
    _, downloads = onnx_boundary
    service = es.EmbeddingService()
    service._load_model()
    config = es.EMBEDDING_MODELS["bge-small-en"]
    assert isinstance(service._model, es.OnnxEncoder)
    assert downloads[0][:2] == (config["hf_name"], config["onnx_file"])
    assert downloads[0][2]["revision"] == config["revision"]


def test_query_gets_the_query_prefix_and_a_document_does_not(onnx_boundary):
    session, _ = onnx_boundary
    service = es.EmbeddingService()
    service.embed_batch(["a"])
    service.embed_batch(["a"], is_query=True)
    document_width, query_width = (feed["input_ids"].shape[1] for feed in session.feeds)
    assert query_width > document_width


def test_model_name_jina_code_gives_the_removal_message():
    with pytest.raises(ConfigurationError, match="removed in 3.0.0") as error:
        es.EmbeddingService(model_name="jina-code")
    assert "bge-small-en" in str(error.value)


def test_unknown_model_name_lists_the_supported_models():
    with pytest.raises(
        ConfigurationError, match="Supported models: bge-small-en, bge-small-en-torch"
    ):
        es.EmbeddingService(model_name="nope")


def test_a_gpu_device_is_ignored_with_a_warning(caplog):
    with caplog.at_level("WARNING"):
        service = es.EmbeddingService(device="mps")
    assert service.device == "cpu"
    assert any("not supported since 3.0.0" in r.message for r in caplog.records)


def test_cpu_device_gives_no_warning(caplog):
    with caplog.at_level("WARNING"):
        es.EmbeddingService(device="cpu")
        es.EmbeddingService(device="auto")
    assert not caplog.records


# ---------------------------------------------------------------------------
# The optional torch backend (`bge-small-en-torch`)
# ---------------------------------------------------------------------------


class _RecordingModel:
    last_args = None
    last_kwargs = None

    def __init__(self, name, **kwargs):
        type(self).last_args = (name,)
        type(self).last_kwargs = kwargs
        self.max_seq_length = 8192


@pytest.fixture
def recorder(monkeypatch):
    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _RecordingModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    reset_settings()
    yield _RecordingModel
    reset_settings()


@pytest.fixture
def registry(monkeypatch):
    """A copy of the registry that a test may extend without leaking."""
    copy = {name: dict(cfg) for name, cfg in es.EMBEDDING_MODELS.items()}
    monkeypatch.setattr(es, "EMBEDDING_MODELS", copy)
    return copy


@pytest.fixture
def wide_model(registry):
    """A registered model with 768-wide vectors, the other width for the model-change tests."""
    registry["wide-test"] = {
        "backend": "onnx", "hf_name": "org/wide", "dimensions": 768, "max_seq_length": 512,
        "prompt_prefix": "", "query_prefix": "", "onnx_file": "onnx/m.onnx", "pooling": "cls",
    }
    return "wide-test"


def test_torch_model_loads_on_the_cpu_with_the_registry_name(recorder):
    service = es.EmbeddingService("bge-small-en-torch", cache_dir="/tmp/hf")
    service._load_model()
    assert isinstance(service._model, es.TorchEncoder)
    assert recorder.last_args == ("BAAI/bge-small-en-v1.5",)
    assert recorder.last_kwargs == {
        "device": "cpu",
        "cache_folder": "/tmp/hf",
        "revision": es.EMBEDDING_MODELS["bge-small-en-torch"]["revision"],
    }


def test_both_shipped_models_are_pinned_to_a_commit():
    for name, config in es.EMBEDDING_MODELS.items():
        assert re.fullmatch(r"[0-9a-f]{40}", config["revision"]), name


def test_torch_model_caps_the_sequence_length_at_the_registry_value(recorder):
    service = es.EmbeddingService("bge-small-en-torch")
    service._load_model()
    assert service._model._model.max_seq_length == 512  # the fake model reported 8192


def test_torch_model_does_not_raise_the_sequence_length_above_the_model_limit(recorder, registry):
    registry["short"] = {
        "backend": "torch", "hf_name": "org/model", "dimensions": 384,
        "max_seq_length": 99999, "prompt_prefix": "", "query_prefix": "",
    }
    service = es.EmbeddingService("short")
    service._load_model()
    assert service._model._model.max_seq_length == 8192


def test_torch_model_without_the_extra_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import raises ImportError
    with pytest.raises(ConfigurationError, match=r"nexus-mcp-ci\[torch\]"):
        es.EmbeddingService("bge-small-en-torch")._load_model()


def test_the_default_model_does_not_import_torch(onnx_boundary, monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # any import would raise
    es.EmbeddingService()._load_model()


def test_model_dimensions_known_model():
    assert es.model_dimensions("bge-small-en") == 384
    assert es.model_dimensions("bge-small-en-torch") == 384


def test_model_dimensions_unknown_model_uses_default_width():
    assert es.model_dimensions("not-a-model") == es.model_dimensions(es.DEFAULT_MODEL)


# ---------------------------------------------------------------------------
# Model-change detection in the pipeline
# ---------------------------------------------------------------------------


def _pipeline(tmp_path, model):
    settings = Settings(storage_dir=str(tmp_path / ".nexus"), embedding_model=model)
    dims = es.model_dimensions(model)
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_svc:
        svc = MagicMock()
        svc.model_name = model
        svc.embed.return_value = [0.1] * dims
        svc.embed_batch.side_effect = lambda texts, **kw: [[0.1] * dims for _ in texts]
        get_svc.return_value = svc
        pipeline = IndexingPipeline(settings)
        pipeline._vector_engine._embedding_service = svc
        return pipeline


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "a.py").write_text("def alpha():\n    return 1\n")
    (root / "b.py").write_text("def beta():\n    return 2\n")
    return root


def test_index_metadata_records_model_and_width(codebase, tmp_path):
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    stored = pipeline._load_metadata_raw()
    assert stored["embedding_model"] == "bge-small-en"
    assert stored["embedding_dimensions"] == 384


def test_changed_model_triggers_full_rebuild(codebase, tmp_path):
    _pipeline(tmp_path, "bge-small-en").index(codebase)

    # Same width, other model: the vectors of the int8 and the fp32 model differ a little,
    # so the name alone must trigger the rebuild.
    second = _pipeline(tmp_path, "bge-small-en-torch")
    result = second.incremental_index(codebase)

    assert result.total_files == 2  # a full index ran; an incremental no-op would be 0
    assert second._load_metadata_raw()["embedding_model"] == "bge-small-en-torch"


def test_same_model_stays_incremental(codebase, tmp_path):
    _pipeline(tmp_path, "bge-small-en").index(codebase)
    result = _pipeline(tmp_path, "bge-small-en").incremental_index(codebase)
    # Requirement changed on 2026-11-08 (2.4.4): an unchanged index reports the size of the
    # stored index, not zero files, so "no rebuild" is now the `unchanged` flag.
    assert result.unchanged is True
    assert result.files_added == result.files_modified == result.files_deleted == 0


def test_index_without_model_keys_is_accepted(codebase, tmp_path):
    """An index written before this check carries no model name and must keep working."""
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)

    data = json.loads(pipeline._metadata_path.read_text())
    data.pop("embedding_model")
    data.pop("embedding_dimensions")
    pipeline._metadata_path.write_text(json.dumps(data))

    assert _pipeline(tmp_path, "bge-small-en")._validate_index() is True


def test_index_metadata_records_chunk_format(codebase, tmp_path):
    from nexus_mcp.indexing.pipeline import CHUNK_FORMAT

    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    assert pipeline._load_metadata_raw()["chunk_format"] == CHUNK_FORMAT


def test_index_of_an_older_chunk_format_is_rebuilt(codebase, tmp_path):
    """An index from before the module chunks has no `chunk_format` key."""
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    data = json.loads(pipeline._metadata_path.read_text())
    data.pop("chunk_format")
    pipeline._metadata_path.write_text(json.dumps(data))

    second = _pipeline(tmp_path, "bge-small-en")
    assert second._validate_index() is False
    assert second.incremental_index(codebase).total_files == 2  # a full index ran


def test_index_of_chunk_format_7_is_rebuilt(codebase, tmp_path):
    """Format 7 indexes (2.2.x) have no class `bases` and miss the new language symbols."""
    from nexus_mcp.indexing.pipeline import CHUNK_FORMAT

    assert CHUNK_FORMAT >= 8
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    data = json.loads(pipeline._metadata_path.read_text())
    data["chunk_format"] = 7
    pipeline._metadata_path.write_text(json.dumps(data))

    second = _pipeline(tmp_path, "bge-small-en")
    assert second._validate_index() is False
    assert second.incremental_index(codebase).total_files == 2  # a full index ran


def test_index_of_chunk_format_8_is_rebuilt(codebase, tmp_path):
    """Format 8 indexes (2.x) hold fp32 vectors under the name `bge-small-en`. Since 3.0.0
    that name is the int8 model, so the index is rebuilt and never mixes the two."""
    from nexus_mcp.indexing.pipeline import CHUNK_FORMAT

    assert CHUNK_FORMAT >= 9
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    data = json.loads(pipeline._metadata_path.read_text())
    data["chunk_format"] = 8
    pipeline._metadata_path.write_text(json.dumps(data))

    second = _pipeline(tmp_path, "bge-small-en")
    assert second._validate_index() is False
    assert second.incremental_index(codebase).total_files == 2  # a full index ran


def test_legacy_index_with_the_wrong_vector_width_is_rebuilt(codebase, tmp_path, wide_model):
    """A 2.0.3 index has no model key. Opening it with a model of another width must
    rebuild, not fail on the first add (the Docker default moved from 768 to 384)."""
    old = _pipeline(tmp_path, wide_model)  # 768-wide vectors
    old.index(codebase)
    data = json.loads(old._metadata_path.read_text())
    data.pop("embedding_model")
    data.pop("embedding_dimensions")
    old._metadata_path.write_text(json.dumps(data))

    new = _pipeline(tmp_path, "bge-small-en")  # 384-wide
    result = new.incremental_index(codebase)

    assert result.total_files == 2  # full rebuild, not an incremental no-op
    assert new.vector_engine.validate() is True


def test_vector_engine_validate_rejects_a_table_of_another_width(codebase, tmp_path, wide_model):
    _pipeline(tmp_path, wide_model).index(codebase)
    narrow = _pipeline(tmp_path, "bge-small-en")
    assert narrow.vector_engine.validate() is False


def _note(memory_id, content):
    from nexus_mcp.core.models import Memory, MemoryType

    return Memory(id=memory_id, content=content, memory_type=MemoryType.NOTE, project="p")


def _memory_store(tmp_path, dims):
    from nexus_mcp.memory.memory_store import MemoryStore

    svc = MagicMock()
    svc.embed.side_effect = lambda text, **kw: [0.1] * dims
    svc.embed_batch.side_effect = lambda texts, **kw: [[0.1] * dims for _ in texts]
    return MemoryStore(db_path=str(tmp_path / "lance"), embedding_service=svc, vector_dims=dims)


def test_memories_are_re_embedded_when_the_model_width_changes(tmp_path):
    first = _memory_store(tmp_path, 384)
    first.remember(_note("m1", "use HMAC-SHA256 for webhooks"))
    first.remember(_note("m2", "refunds go through the gateway"))

    second = _memory_store(tmp_path, 768)  # same storage, wider model
    second.remember(_note("m3", "a note stored after the switch"))

    contents = {m.content for m in second.recall("webhooks", limit=10)}
    assert contents == {
        "use HMAC-SHA256 for webhooks",
        "refunds go through the gateway",
        "a note stored after the switch",
    }
    backup = tmp_path / "memories-before-model-change.json"
    assert {row["id"] for row in json.loads(backup.read_text())} == {"m1", "m2"}


def test_memories_stay_untouched_when_the_width_is_unchanged(tmp_path):
    _memory_store(tmp_path, 384).remember(_note("m1", "keep me"))
    _memory_store(tmp_path, 384).remember(_note("m2", "and me"))
    assert not (tmp_path / "memories-before-model-change.json").exists()


class _EncodingModel(_RecordingModel):
    def encode(self, texts, **kwargs):
        return np.zeros((len(texts), 384))


def test_embed_batch_survives_unload_right_after_load(monkeypatch):
    """A reindex unloads the model between load and encode. The search keeps its model."""
    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _EncodingModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    service = es.EmbeddingService("bge-small-en-torch")
    real_load = service._load_model
    raced = []

    def load_then_unload():
        real_load()
        if not raced:  # another thread's unload() lands here, once
            raced.append(True)
            service.unload()

    monkeypatch.setattr(service, "_load_model", load_then_unload)

    assert len(service.embed_batch(["a", "b"])) == 2
    assert len(service.embed("c")) == 384

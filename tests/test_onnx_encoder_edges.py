# ruff: noqa: F811  (a test takes an imported fixture as an argument of the same name)
"""Edge cases of the ONNX encoder and the embedding service (test audit of Part 1, ADR-026).

The fixtures come from `tests/test_embedding_loading.py`: a fake ONNX Runtime session, a fake
download function and a real tokenizer.
"""

import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from nexus_mcp.indexing import embedding_service as es
from tests.test_embedding_loading import (  # noqa: F401  (fixtures are used by name)
    _encoder,
    _FakeSession,
    batches_of_8,
    onnx_boundary,
)

# ---------------------------------------------------------------------------
# Model outputs and inputs the main tests do not reach
# ---------------------------------------------------------------------------


class _PooledSession(_FakeSession):
    """A model export that already pools: output (batch, 2), float64."""

    def run(self, _outputs, feed):
        self.feeds.append(feed)
        ids = feed["input_ids"].astype(np.float64)
        return [np.stack([ids[:, 0], ids.sum(axis=1)], axis=1)]


class _ZeroSession(_FakeSession):
    def run(self, _outputs, feed):
        self.feeds.append(feed)
        return [np.zeros((feed["input_ids"].shape[0], feed["input_ids"].shape[1], 3), np.float32)]


@pytest.mark.parametrize("pooling", ["cls", "mean"])
def test_encode_two_dimensional_output_is_used_as_is(onnx_boundary, monkeypatch, pooling):
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: _PooledSession())
    vectors = _encoder(pooling).encode(["a b"], normalize=False)  # ids 2 4 5 3
    assert vectors.dtype == np.float32
    assert vectors.tolist() == [[2.0, 14.0]]  # not pooled again, whatever `pooling` says


def test_encode_two_dimensional_output_keeps_the_input_order(
    onnx_boundary, monkeypatch, batches_of_8
):
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: _PooledSession())
    texts = ["a b c d e a b", "a", "a b c"]
    together = _encoder().encode(texts, normalize=False)
    for row, text in zip(together, texts):
        assert row.tolist() == _encoder().encode([text], normalize=False)[0].tolist()


def test_encode_empty_text_gives_a_finite_vector(onnx_boundary):
    vectors = _encoder().encode([""], normalize=True)  # [CLS] [SEP]
    assert vectors.shape == (1, 3) and np.isfinite(vectors).all()


def test_encode_zero_vector_does_not_give_nan(onnx_boundary, monkeypatch):
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: _ZeroSession())
    vectors = _encoder().encode(["a b"], normalize=True)
    assert np.isfinite(vectors).all() and not vectors.any()


def test_find_pad_id_tries_the_known_pad_tokens_then_zero():
    def tokenizer(vocab):
        return types.SimpleNamespace(token_to_id=vocab.get)

    assert es.OnnxEncoder._find_pad_id(tokenizer({"[PAD]": 5, "<pad>": 7})) == 5
    assert es.OnnxEncoder._find_pad_id(tokenizer({"<pad>": 7})) == 7
    assert es.OnnxEncoder._find_pad_id(tokenizer({"<|pad|>": 9})) == 9
    assert es.OnnxEncoder._find_pad_id(tokenizer({})) == 0


# ---------------------------------------------------------------------------
# Failure and race behaviour of the service
# ---------------------------------------------------------------------------


def test_load_failure_raises_restores_stdio_and_allows_a_retry(monkeypatch):
    """Offline and not cached (a 2.x user holds only the BAAI model): the caller gets a
    `ConfigurationError` that names the model, the commit and the way out (the raw hub error
    named none of them), the redirected stdout and stderr come back (the MCP stdio transport
    needs them), and the next call tries again.

    Requirement changed in 3.0.0: before, the raw `LocalEntryNotFoundError` reached the user.
    """
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError

    from nexus_mcp.core.exceptions import ConfigurationError

    def offline(*args, **kwargs):
        raise LocalEntryNotFoundError("offline and not cached")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", offline)
    streams = (sys.stdout, sys.stderr)
    service = es.EmbeddingService("bge-small-en")
    for _ in range(2):  # the second call proves that a failed load is not remembered
        with pytest.raises(ConfigurationError, match="NEXUS_OFFLINE") as error:
            service.embed("x")
        assert "Xenova/bge-small-en-v1.5" in str(error.value)
        assert es.EMBEDDING_MODELS["bge-small-en"]["revision"][:12] in str(error.value)
        assert (sys.stdout, sys.stderr) == streams
        assert service._model_loaded is False and service._model is None


def test_two_services_that_load_at_once_restore_stdout_in_order(onnx_boundary, monkeypatch):
    """The stdout swap is process-wide. Without one lock for all services, the second service
    saved the StringIO of the first as its 'old' stdout and left it in place."""
    import threading

    streams = (sys.stdout, sys.stderr)
    inside = threading.Event()
    release = threading.Event()
    first_load = es.OnnxEncoder.__init__

    def slow_init(self, *args, **kwargs):
        inside.set()
        release.wait(timeout=5)
        first_load(self, *args, **kwargs)

    monkeypatch.setattr(es.OnnxEncoder, "__init__", slow_init)
    services = [es.EmbeddingService("bge-small-en") for _ in range(2)]
    threads = [threading.Thread(target=s._load_model) for s in services]
    threads[0].start()
    inside.wait(timeout=5)
    threads[1].start()  # starts while the first one holds the redirected streams
    release.set()
    for thread in threads:
        thread.join(timeout=10)
    assert (sys.stdout, sys.stderr) == streams


def test_unload_during_encode_does_not_break_the_running_call(onnx_boundary):
    """A background reindex unloads the model while a search encodes. The search keeps the
    encoder that it already holds, and the next call loads a fresh one."""
    service = es.EmbeddingService("bge-small-en")
    service._load_model()
    real_encode = service._model.encode

    def encode_after_an_unload(texts, **kwargs):
        service.unload()  # lands in the middle of the call
        return real_encode(texts, **kwargs)

    service._model.encode = encode_after_an_unload
    assert len(service.embed_batch(["a b", "c d"])) == 2
    assert service._model is None
    assert len(service.embed("a")) == 3  # loads a fresh encoder


def test_embed_batch_of_nothing_does_not_load_the_model(onnx_boundary):
    service = es.EmbeddingService("bge-small-en")
    assert service.embed_batch([]) == []
    assert service._model_loaded is False


# ---------------------------------------------------------------------------
# Public functions that had no direct test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(es.EMBEDDING_MODELS))
def test_dimensions_matches_the_registry_without_loading_the_model(name):
    service = es.EmbeddingService(name)
    assert service.dimensions == es.EMBEDDING_MODELS[name]["dimensions"]
    assert service._model_loaded is False


def test_get_stats_counts_embeddings_and_cache_hits(onnx_boundary):
    service = es.EmbeddingService("bge-small-en")
    assert service.get_stats()["embeddings_per_second"] == 0
    service.embed("a")
    service.embed("a")  # served from the cache
    service.embed_batch(["a b", "c d"])
    stats = service.get_stats()
    assert (stats["cache_hits"], stats["cache_misses"], stats["total_embeddings"]) == (1, 1, 3)
    assert stats["embeddings_per_second"] >= 0


def test_get_stats_total_batches_counts_the_real_onnx_calls(onnx_boundary):
    """Found by the test audit: it counted calls of 32 while the ONNX encoder ran one text in
    each call, so `total_batches` was 1 for 5 texts."""
    session, _ = onnx_boundary
    service = es.EmbeddingService("bge-small-en")
    service.embed_batch(["a"] * 5)
    assert service.get_stats()["total_batches"] == len(session.feeds) == 5


def test_reset_embedding_service_unloads_and_forgets_one_or_all():
    first = es.get_embedding_service("bge-small-en")
    first._model, first._model_loaded = MagicMock(), True
    es.reset_embedding_service("bge-small-en")
    assert first._model is None and "bge-small-en" not in es._services
    assert es.get_embedding_service("bge-small-en") is not first
    es.reset_embedding_service("not-a-model")  # an unknown name is not an error
    es.reset_embedding_service()
    assert es._services == {}


# ---------------------------------------------------------------------------
# Invariants of the encoder, as properties
# ---------------------------------------------------------------------------

_TEXTS = st.lists(
    st.lists(st.sampled_from(["a", "b", "c", "d", "e"]), min_size=1, max_size=30).map(" ".join),
    min_size=1,
    max_size=10,
)
_FIXTURE_OK = [HealthCheck.function_scoped_fixture]


@given(texts=_TEXTS, cap=st.sampled_from([1, 2, 8]), batch=st.integers(1, 64))
@hyp_settings(deadline=None, max_examples=40, suppress_health_check=_FIXTURE_OK)
def test_encode_vector_of_a_text_does_not_depend_on_its_batch(
    onnx_boundary, monkeypatch, texts, cap, batch
):
    """Order restoration and batch invariance, for any batch cap and batch size."""
    monkeypatch.setattr(es, "ONNX_MAX_BATCH", cap)
    encoder = _encoder("mean")
    together = encoder.encode(texts, batch_size=batch, normalize=False)
    for row, text in zip(together, texts):
        assert row.tolist() == pytest.approx(encoder.encode([text], normalize=False)[0].tolist())


@given(texts=_TEXTS)
@hyp_settings(deadline=None, max_examples=40, suppress_health_check=_FIXTURE_OK)
def test_encode_gives_unit_vectors_when_asked(onnx_boundary, texts):
    vectors = _encoder("cls").encode(texts, normalize=True)
    assert np.linalg.norm(vectors, axis=1) == pytest.approx(1.0, abs=1e-5)


@given(texts=_TEXTS)
@hyp_settings(deadline=None, max_examples=40, suppress_health_check=_FIXTURE_OK)
def test_encode_runs_every_text_alone_by_default(onnx_boundary, texts):
    session, _ = onnx_boundary
    session.feeds.clear()
    _encoder().encode(texts, batch_size=32)
    assert [feed["input_ids"].shape[0] for feed in session.feeds] == [1] * len(texts)

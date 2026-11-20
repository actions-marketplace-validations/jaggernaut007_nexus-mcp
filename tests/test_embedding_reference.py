"""The default model against stored vectors of the fp32 PyTorch model (ADR-029).

`tests/fixtures/bge_small_reference.json` holds the vectors that sentence-transformers made
for five fixed texts (`scripts/measure_embedding_backends.py --write-reference`). The test
loads the real int8 ONNX model, so it needs the Hugging Face cache or a network, and it is
marked slow. It needs no PyTorch.

The int8 file is not the fp32 model, so the vectors are close, not equal. The measured
minimum cosine on 300 chunks of code was 0.9936. A tokenizer, pooling or truncation bug
gives a cosine far below 0.99, so the bound of 0.985 catches those and not the rounding.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from nexus_mcp.indexing.embedding_service import EmbeddingService, reset_embedding_service

REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "bge_small_reference.json").read_text()
)
MIN_COSINE = 0.985


@pytest.fixture(scope="module")
def vectors():
    service = EmbeddingService("bge-small-en")
    try:
        yield np.array(service.embed_batch(REFERENCE["texts"]), dtype=np.float32)
    finally:
        service.unload()
        reset_embedding_service()


def test_the_reference_file_has_one_vector_for_each_text():
    assert len(REFERENCE["vectors"]) == len(REFERENCE["texts"]) == 5
    assert all(len(row) == 384 for row in REFERENCE["vectors"])
    norms = np.linalg.norm(np.array(REFERENCE["vectors"], dtype=np.float32), axis=1)
    assert norms == pytest.approx(1.0, abs=1e-4)


def test_the_reference_texts_are_the_ones_that_the_script_writes():
    """If someone edits the texts in the script and not the file, the slow test would compare
    vectors of different texts."""
    import importlib.util

    script = Path(__file__).parent.parent / "scripts" / "measure_embedding_backends.py"
    spec = importlib.util.spec_from_file_location("measure_embedding_backends", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert REFERENCE["texts"] == module.REFERENCE_TEXTS


@pytest.mark.slow
def test_the_default_model_gives_the_same_vector_alone_and_in_a_big_batch():
    """The int8 model quantizes with one scale for each batch, so this fails when someone
    raises `ONNX_MAX_BATCH` above 1 (the cosine fell to 0.9933 in the measurement)."""
    service = EmbeddingService("bge-small-en")
    try:
        texts = REFERENCE["texts"][:3]
        alone = [service.embed_batch([text])[0] for text in texts]
        together = service.embed_batch(texts, batch_size=32)
        assert np.array(together) == pytest.approx(np.array(alone), abs=1e-6)
    finally:
        service.unload()


@pytest.mark.slow
def test_default_model_vectors_are_close_to_the_torch_reference(vectors):
    reference = np.array(REFERENCE["vectors"], dtype=np.float32)
    cosine = (vectors * reference).sum(axis=1) / (
        np.linalg.norm(vectors, axis=1) * np.linalg.norm(reference, axis=1)
    )
    assert cosine.min() >= MIN_COSINE, cosine.tolist()


@pytest.mark.slow
def test_default_model_vectors_have_unit_length(vectors):
    assert np.linalg.norm(vectors, axis=1) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.slow
def test_a_text_longer_than_the_sequence_limit_is_truncated_like_the_reference(vectors):
    """The last reference text has 400 lines, far over 512 tokens. If the ONNX encoder did
    not truncate, ONNX Runtime would fail on the position embeddings, so a result here, close
    to the reference, shows that it truncates at the same limit."""
    reference = np.array(REFERENCE["vectors"][-1], dtype=np.float32)
    cosine = float(vectors[-1] @ reference / np.linalg.norm(reference))
    assert cosine >= MIN_COSINE

"""Embedding service, CPU only. See ADR-026.

Two backends, chosen by the model name:
- `OnnxEncoder` (the default): `tokenizers` for the text, `onnxruntime` for the model.
  It needs no PyTorch.
- `TorchEncoder` (optional): `sentence-transformers` on the CPU. Install the extra
  `nexus-mcp-ci[torch]`. It is faster on Apple Silicon.

Supported models:
- bge-small-en        (384d, 512 seq len) int8 ONNX file, 32 MB. The default.
- bge-small-en-torch  (384d, 512 seq len) fp32 weights on PyTorch. Needs the `torch` extra.

The two names give slightly different vectors (cosine 0.994 at the lowest). The model name
is stored with the index, so a switch between them rebuilds the index.
"""

import gc
import logging
import threading
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Supported embedding models. The keys of an entry:
#   backend         "onnx" (OnnxEncoder) or "torch" (TorchEncoder)
#   hf_name         Hugging Face repo of the model
#   revision        commit of that repo. A fixed commit, so the files that the user
#                   downloads and runs do not change under them.
#   onnx_file       path of the ONNX file inside the repo (onnx only)
#   pooling         "cls" (first token) or "mean" (mask-weighted mean) (onnx only)
#   max_seq_length  the tokenizer truncates a chunk to this many tokens
EMBEDDING_MODELS: Dict[str, Dict[str, Any]] = {
    "bge-small-en": {
        "backend": "onnx",
        "hf_name": "Xenova/bge-small-en-v1.5",
        "revision": "ea104dacec62c0de699686887e3f920caeb4f3e3",
        "dimensions": 384,
        "max_seq_length": 512,
        "prompt_prefix": "",
        "query_prefix": _BGE_QUERY_PREFIX,
        "onnx_file": "onnx/model_quantized.onnx",
        "pooling": "cls",
    },
    "bge-small-en-torch": {
        "backend": "torch",
        "hf_name": "BAAI/bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "dimensions": 384,
        "max_seq_length": 512,
        "prompt_prefix": "",
        "query_prefix": _BGE_QUERY_PREFIX,
    },
}

DEFAULT_MODEL = "bge-small-en"

# A model that was supported once. The message tells the user what to do.
RETIRED_MODELS = {
    "jina-code": (
        "The model 'jina-code' was removed in 3.0.0. "
        "Use 'bge-small-en' (the default), then re-index."
    ),
}

_VALID_POOLING = ("cls", "mean")

# `sys.stdout` and `sys.stderr` are process-wide, so the swap during a model load needs one
# lock for all services.
_stdio_lock = threading.Lock()


def _hub_not_cached_errors() -> tuple:
    """Errors of the Hugging Face hub for a model that is not cached and cannot be fetched."""
    from huggingface_hub.errors import LocalEntryNotFoundError

    return (LocalEntryNotFoundError,)

# The largest batch that one ONNX Runtime call gets. It is 1: every text runs alone.
# - Memory: the attention scores of a batch grow with batch size times the square of the
#   sequence length, and ONNX Runtime keeps them in its memory arena.
# - Determinism: the int8 model quantizes its activations with one scale for the whole
#   batch, so the vector of a text changed with the other texts in its batch (cosine 0.9933
#   between a text alone and the same text in a batch). With one text in a call, a vector
#   depends only on its own text. The fp32 file has no such effect.
# - Speed: on a CPU a larger batch gave no gain. Measured on 300 chunks, int8 model, 512
#   tokens, peak RSS and chunks/s (2026-11-17):
#   batch 32: 1.3 GB, 19-20    batch 8: 0.53-0.61 GB, 21    batch 1: 0.26-0.27 GB, 20-26
ONNX_MAX_BATCH = 1


def model_dimensions(model_name: str) -> int:
    """Vector width of a registered model. Unknown names get the default model's width."""
    config = EMBEDDING_MODELS.get(model_name) or EMBEDDING_MODELS[DEFAULT_MODEL]
    return config["dimensions"]


class OnnxEncoder:
    """Turns texts into vectors with an ONNX transformer model on the CPU.

    The encoder is thread-safe: `onnxruntime` allows concurrent `run` calls and the
    tokenizer is used for reading only. With `ONNX_MAX_BATCH` above 1 (not the default),
    a batch is padded to its longest text, and `encode` sorts texts by length first, so
    short chunks do not pay for long ones.
    """

    def __init__(
        self,
        hf_name: str,
        onnx_file: str,
        pooling: str,
        max_seq_length: int,
        cache_dir: Optional[str] = None,
        revision: Optional[str] = None,
    ):
        """Download the model and the tokenizer (once, then from the cache) and open a session.

        Args:
            hf_name: Hugging Face repo that holds the ONNX file and `tokenizer.json`.
            onnx_file: Path of the ONNX file inside the repo.
            pooling: "cls" (first token) or "mean" (mask-weighted mean of all tokens).
            max_seq_length: The tokenizer truncates a text to this many tokens.
            cache_dir: Hugging Face cache folder. None means the default.
            revision: Commit of the repo to download. Pin it, so the file does not change.

        Raises:
            ValueError: `pooling` is not "cls" or "mean".
            huggingface_hub errors: the download failed, or the model is not cached while
                offline (`NEXUS_OFFLINE=1`).
        """
        if pooling not in _VALID_POOLING:
            raise ValueError(f"pooling must be one of {_VALID_POOLING}, got {pooling!r}")

        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model_path = hf_hub_download(hf_name, onnx_file, cache_dir=cache_dir, revision=revision)
        tokenizer_path = hf_hub_download(
            hf_name, "tokenizer.json", cache_dir=cache_dir, revision=revision
        )

        self._pooling = pooling
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.no_padding()
        self._tokenizer.enable_truncation(max_length=max_seq_length)
        self._pad_id = self._find_pad_id(self._tokenizer)

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        # Feed only the inputs that the graph declares: BERT models take `token_type_ids`,
        # ModernBERT models do not.
        self._input_names = {i.name for i in self._session.get_inputs()}

    @staticmethod
    def _find_pad_id(tokenizer) -> int:
        for token in ("[PAD]", "<pad>", "<|pad|>"):
            token_id = tokenizer.token_to_id(token)
            if token_id is not None:
                return token_id
        return 0

    def _run(self, texts: List[str]):
        import numpy as np

        encodings = self._tokenizer.encode_batch(texts)
        width = max(len(e.ids) for e in encodings)
        ids = np.full((len(encodings), width), self._pad_id, dtype=np.int64)
        mask = np.zeros((len(encodings), width), dtype=np.int64)
        types = np.zeros((len(encodings), width), dtype=np.int64)
        for row, e in enumerate(encodings):
            length = len(e.ids)
            ids[row, :length] = e.ids
            mask[row, :length] = e.attention_mask
            types[row, :length] = e.type_ids

        feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
        feed = {name: value for name, value in feed.items() if name in self._input_names}
        output = self._session.run(None, feed)[0]

        if output.ndim == 2:  # the export already pools
            return output.astype(np.float32, copy=False)
        if self._pooling == "cls":
            return output[:, 0].astype(np.float32, copy=False)
        weights = mask[:, :, None].astype(np.float32)
        summed = (output * weights).sum(axis=1)
        counts = np.clip(weights.sum(axis=1), 1e-9, None)
        return (summed / counts).astype(np.float32, copy=False)

    def encode(self, texts: List[str], batch_size: int = ONNX_MAX_BATCH, normalize: bool = True):
        """Vectors for `texts`, as a float32 array of shape (len(texts), dimensions).

        Args:
            texts: The texts to embed. They keep their order in the result.
            batch_size: Texts in one ONNX call. Capped at `ONNX_MAX_BATCH`. With the int8
                model, a value above 1 makes a vector depend on the other texts of its batch.
            normalize: Scale each vector to unit length.
        """
        import numpy as np

        count = len(texts)
        if count == 0:
            return np.zeros((0, 0), dtype=np.float32)
        batch_size = max(1, min(batch_size, ONNX_MAX_BATCH))
        order = np.argsort([len(t) for t in texts], kind="stable")
        result = None
        for start in range(0, count, batch_size):
            rows = order[start : start + batch_size]
            vectors = self._run([texts[i] for i in rows])
            if result is None:
                result = np.empty((count, vectors.shape[1]), dtype=np.float32)
            result[rows] = vectors
        if normalize:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            result = result / np.clip(norms, 1e-12, None)
        return result


class TorchEncoder:
    """Optional backend: sentence-transformers on the CPU (fp32 weights).

    Raises a `ConfigurationError` that names the extra when PyTorch is not installed.

    It needs the `torch` extra (`pip install 'nexus-mcp-ci[torch]'`). On Apple Silicon it is
    about 2 times faster than the int8 ONNX file, because PyTorch uses the matrix hardware.
    """

    def __init__(
        self,
        hf_name: str,
        max_seq_length: int,
        cache_dir: Optional[str] = None,
        revision: Optional[str] = None,
    ):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            from nexus_mcp.core.exceptions import ConfigurationError

            raise ConfigurationError(
                "This embedding model needs PyTorch. Install it with: "
                "pip install 'nexus-mcp-ci[torch]'. Or use the default model 'bge-small-en'."
            ) from exc

        kwargs: Dict[str, Any] = {"device": "cpu"}
        if cache_dir:
            kwargs["cache_folder"] = cache_dir
        if revision:
            kwargs["revision"] = revision
        self._model = SentenceTransformer(hf_name, **kwargs)
        # Chunks longer than the registry value are truncated, which also bounds memory.
        current = getattr(self._model, "max_seq_length", None)
        if current and max_seq_length < current:
            self._model.max_seq_length = max_seq_length

    def encode(self, texts: List[str], batch_size: int = 32, normalize: bool = True):
        """Vectors for `texts`, as a float32 array of shape (len(texts), dimensions)."""
        return self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
        )


class EmbeddingService:
    """Embedding service with lazy model loading and batch processing."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: Optional[str] = None,
        batch_size: int = 32,
        max_batch_size: int = 128,
        normalize: bool = True,
        cache_dir: Optional[str] = None,
    ):
        """Prepare a service for a registered model. The model loads on first use.

        Args:
            model_name: A key of `EMBEDDING_MODELS`.
            device: Kept for old setups. Embeddings run on the CPU. A value other than
                None, "", "auto" or "cpu" is ignored with a warning.
            batch_size: Texts per call of `embed_batch`. The ONNX model still runs
                `ONNX_MAX_BATCH` texts in one call.
            max_batch_size: Upper bound for `batch_size`.
            normalize: Scale each vector to unit length.
            cache_dir: Hugging Face cache folder. None means the default.

        Raises:
            ConfigurationError: the name is unknown. A removed model (`jina-code`) gets a
                message that says what to use.
        """
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_batch_size = max_batch_size
        self.normalize = normalize
        self.cache_dir = cache_dir

        if model_name not in EMBEDDING_MODELS:
            from nexus_mcp.core.exceptions import ConfigurationError

            supported = ", ".join(sorted(EMBEDDING_MODELS.keys()))
            retired = RETIRED_MODELS.get(model_name)
            raise ConfigurationError(
                (retired + " " if retired else "")
                + f"Unsupported embedding model '{model_name}'. "
                + f"Supported models: {supported}"
            )
        self.config = EMBEDDING_MODELS[model_name].copy()

        # Embeddings run on the CPU only. The argument stays so that an old
        # `NEXUS_EMBEDDING_DEVICE=mps` setup keeps working.
        if device not in (None, "", "auto", "cpu"):
            logger.warning(
                "Embedding device '%s' is not supported since 3.0.0. Using the CPU.", device
            )
        self.device = "cpu"
        self._lock = threading.Lock()
        self._model: Optional[Any] = None
        self._model_loaded = False

        self.stats = {
            "total_embeddings": 0,
            "total_batches": 0,
            "total_time": 0.0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

        self._embed_cached = lru_cache(maxsize=256)(self._embed_single_uncached)

    def _load_model(self):
        if self._model_loaded:
            return
        with self._lock:
            if self._model_loaded:
                return
            import io
            import sys

            # The download shows progress bars. The MCP stdio transport must get no extra
            # output. `_stdio_lock` is module-wide: `sys.stdout` is process-wide too, and two
            # services that load at once would restore the streams in the wrong order.
            with _stdio_lock:
                old_stdout, old_stderr = sys.stdout, sys.stderr
                sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
                try:
                    self._model = self._build_encoder()
                finally:
                    sys.stdout, sys.stderr = old_stdout, old_stderr
            self._model_loaded = True

    def _build_encoder(self) -> Any:
        if self.config["backend"] == "torch":
            return TorchEncoder(
                hf_name=self.config["hf_name"],
                max_seq_length=self.config["max_seq_length"],
                cache_dir=self.cache_dir,
                revision=self.config.get("revision"),
            )
        try:
            return OnnxEncoder(
                hf_name=self.config["hf_name"],
                onnx_file=self.config["onnx_file"],
                pooling=self.config["pooling"],
                max_seq_length=self.config["max_seq_length"],
                cache_dir=self.cache_dir,
                revision=self.config.get("revision"),
            )
        except _hub_not_cached_errors() as exc:
            from nexus_mcp.core.exceptions import ConfigurationError

            raise ConfigurationError(
                f"The embedding model '{self.model_name}' is not in the local cache and the "
                f"download failed ({self.config['hf_name']}, commit "
                f"{(self.config.get('revision') or 'main')[:12]}, about 32 MB). Allow the "
                "download once (unset NEXUS_OFFLINE and check the network), or copy the "
                "Hugging Face cache from another machine. A 2.x cache holds a different "
                "model (BAAI/bge-small-en-v1.5). Or install 'nexus-mcp-ci[torch]' and set "
                "NEXUS_EMBEDDING_MODEL=bge-small-en-torch to use that one."
            ) from exc

    def _loaded_model(self) -> Any:
        """The loaded model, as a local reference that a concurrent ``unload()`` cannot clear.

        A background reindex calls ``unload()`` when it ends. A search that read
        ``self._model`` after that got None and fell back to BM25 without a word.
        """
        while True:
            self._load_model()
            model = self._model
            if model is not None:
                return model

    @property
    def dimensions(self) -> int:
        return self.config["dimensions"]

    def _embed_single_uncached(self, text: str, is_query: bool) -> tuple:
        model = self._loaded_model()
        prefix = self.config["query_prefix"] if is_query else self.config["prompt_prefix"]
        if prefix:
            text = prefix + text
        embeddings = model.encode([text], batch_size=1, normalize=self.normalize)
        return tuple(embeddings[0].tolist())

    def embed(self, text: str, is_query: bool = False) -> List[float]:
        """Embed single text with LRU caching."""
        info_before = self._embed_cached.cache_info()
        result = self._embed_cached(text, is_query)
        info_after = self._embed_cached.cache_info()
        if info_after.hits > info_before.hits:
            self.stats["cache_hits"] += 1
        else:
            self.stats["cache_misses"] += 1
            self.stats["total_embeddings"] += 1
        return list(result)

    def embed_batch(
        self, texts: List[str], is_query: bool = False, batch_size: Optional[int] = None
    ) -> List[List[float]]:
        """Embed batch of texts."""
        import time

        if not texts:
            return []
        model = self._loaded_model()

        bs = min(batch_size or self.batch_size, self.max_batch_size)
        prefix = self.config["query_prefix"] if is_query else self.config["prompt_prefix"]
        if prefix:
            texts = [prefix + t for t in texts]

        start = time.time()
        embeddings = model.encode(texts, batch_size=bs, normalize=self.normalize)
        elapsed = time.time() - start

        self.stats["total_embeddings"] += len(texts)
        # The ONNX encoder runs at most `ONNX_MAX_BATCH` texts in one call: count real calls.
        calls_bs = min(bs, ONNX_MAX_BATCH) if self.config["backend"] == "onnx" else bs
        self.stats["total_batches"] += (len(texts) + calls_bs - 1) // calls_bs
        self.stats["total_time"] += elapsed

        result = embeddings.tolist()
        del embeddings  # Free numpy array immediately
        return result

    def get_stats(self) -> dict:
        """Counters: total_embeddings, total_batches, total_time, cache_hits, cache_misses,
        and embeddings_per_second."""
        stats = self.stats.copy()
        stats["embeddings_per_second"] = (
            stats["total_embeddings"] / stats["total_time"]
            if stats["total_time"] > 0
            else 0
        )
        return stats

    def unload(self):
        """Free the model and clear the cache of query vectors. The next call loads it again."""
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                self._model_loaded = False
                self._embed_cached.cache_clear()
                gc.collect()


# Singleton management
_services: Dict[str, EmbeddingService] = {}
_singleton_lock = threading.Lock()


def get_embedding_service(model_name: str = DEFAULT_MODEL, **kwargs) -> EmbeddingService:
    """Get or create singleton embedding service for a model.

    `kwargs` apply only on the first call for a model name. A later call returns the same
    instance.
    """
    if model_name not in _services:
        with _singleton_lock:
            if model_name not in _services:
                _services[model_name] = EmbeddingService(model_name, **kwargs)
    return _services[model_name]


def reset_embedding_service(model_name: Optional[str] = None):
    """Reset embedding service(s)."""
    with _singleton_lock:
        if model_name is None:
            for svc in _services.values():
                svc.unload()
            _services.clear()
        elif model_name in _services:
            _services[model_name].unload()
            del _services[model_name]

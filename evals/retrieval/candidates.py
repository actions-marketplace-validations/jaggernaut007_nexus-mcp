"""Embedding-model candidates for the retrieval eval.

`bge-small-en` (the int8 ONNX default) and `bge-small-en-torch` (the fp32 weights on PyTorch,
the extra `torch`) ship in nexus_mcp.indexing.embedding_service. The other entries exist
only inside this eval, so a trial never changes the shipped registry. Each entry has the
same keys as a registry entry, plus `approx_mb`, the size of the ONNX file that gets
downloaded (from the Hugging Face file listing, 2026-10-03).

All candidates are Apache-2.0 or MIT. They run on the ONNX encoder of the package, so they
need a `pooling` key (the value of the `1_Pooling/config.json` of the model).
"""

from typing import Any, Dict, Optional

# None means "use the entry that already ships in the registry".
CANDIDATES: Dict[str, Optional[Dict[str, Any]]] = {
    "bge-small-en": None,
    "bge-small-en-torch": None,
    "granite-97m-r2-int8": {
        "backend": "onnx",
        "hf_name": "ibm-granite/granite-embedding-97m-multilingual-r2",
        "dimensions": 384,
        "max_seq_length": 512,
        "prompt_prefix": "",
        "query_prefix": "",
        "onnx_file": "onnx/model_quint8_avx2.onnx",
        "pooling": "cls",
        "approx_mb": 98,
    },
    "gte-modernbert-int8": {
        "backend": "onnx",
        "hf_name": "Alibaba-NLP/gte-modernbert-base",
        "dimensions": 768,
        "max_seq_length": 512,
        "prompt_prefix": "",
        "query_prefix": "",
        "onnx_file": "onnx/model_int8.onnx",
        "pooling": "cls",
        "approx_mb": 150,
    },
}


def register(name: str, registry: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Add the candidate to `registry` if it is eval-only. Returns its config.

    Raises KeyError for a name that is neither a candidate nor in the registry.
    """
    if name not in CANDIDATES:
        raise KeyError(f"Unknown candidate {name!r}. Known: {', '.join(CANDIDATES)}")
    config = CANDIDATES[name]
    if config is not None:
        registry[name] = {k: v for k, v in config.items() if k != "approx_mb"}
    return registry[name]

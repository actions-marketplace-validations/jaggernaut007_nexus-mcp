#!/usr/bin/env python
"""Compare embedding backends on the same chunks: speed, peak memory and vector agreement.

Backends:
  onnx-int8   the default model of this repo (int8 ONNX file, ONNX Runtime, CPU)
  onnx-fp32   the fp32 ONNX file of the same model, for the comparison
  torch-cpu   sentence-transformers on the CPU        (needs torch: --torch)
  torch-mps   sentence-transformers on Apple Metal    (needs torch: --torch)

Each backend runs in its own process, so its peak RSS is its own. The first backend in
the list is the reference for the cosine check.

Usage (from the repo root):
  python scripts/measure_embedding_backends.py --corpus src/nexus_mcp --limit 1500
  python scripts/measure_embedding_backends.py --torch --corpus ../django --limit 3000
  python scripts/measure_embedding_backends.py --write-reference FILE.json

`--write-reference` writes torch vectors for a few fixed texts. The unit test of the
encoder checks against that file, so the test needs no torch.
hit@1 is not measured here: run `python -m evals.retrieval.run` on the old and the new code.
"""

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List

MODEL = "BAAI/bge-small-en-v1.5"

# Fixed texts for the reference file. The last one is longer than 512 tokens, so the
# truncation of both backends is compared too.
REFERENCE_TEXTS = [
    "def add(a, b):\n    return a + b",
    "Represent this sentence for searching relevant passages: where is the retry logic",
    "class UserRepository:\n"
    "    def find_by_email(self, email: str) -> Optional[User]:\n"
    "        ...",
    "SELECT id, name FROM accounts WHERE active = 1 ORDER BY name",
    "def long_function():\n"
    + "\n".join(f"    value_{i} = compute({i}) + helper_{i}()" for i in range(400)),
]


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024, 1)


def chunk_texts(corpus: Path, limit: int) -> List[str]:
    """Chunk texts of the corpus, in file order, built with the code of the indexer."""
    from nexus_mcp.config import Settings
    from nexus_mcp.indexing.chunker import create_chunks
    from nexus_mcp.indexing.pipeline import discover_files
    from nexus_mcp.parsing.treesitter_parser import TreeSitterParser

    parser = TreeSitterParser()
    texts: List[str] = []
    for path in sorted(discover_files(corpus, Settings())):
        try:
            parsed = parser.parse_file(str(path))
        except Exception:
            continue
        texts.extend(c.text for c in create_chunks(parsed.symbols))
        if len(texts) >= limit:
            break
    return texts[:limit]


def make_encoder(backend: str):
    """Return (encode, load_seconds). `encode(texts)` gives an (n, dims) float32 array."""
    started = time.perf_counter()
    if backend in ("onnx-int8", "onnx-fp32"):
        from nexus_mcp.indexing.embedding_service import EMBEDDING_MODELS, OnnxEncoder

        config = dict(EMBEDDING_MODELS["bge-small-en"])
        if backend == "onnx-fp32":
            config.update(hf_name=MODEL, onnx_file="onnx/model.onnx", revision=None)
        encoder = OnnxEncoder(
            hf_name=config["hf_name"],
            onnx_file=config["onnx_file"],
            pooling=config["pooling"],
            max_seq_length=config["max_seq_length"],
            revision=config.get("revision"),
        )
        encode = lambda texts: encoder.encode(texts, batch_size=32)  # noqa: E731
    else:
        from sentence_transformers import SentenceTransformer

        device = "mps" if backend == "torch-mps" else "cpu"
        model = SentenceTransformer(MODEL, device=device)
        encode = lambda texts: model.encode(  # noqa: E731
            texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )
    return encode, time.perf_counter() - started


def run_one(backend: str, corpus: Path, limit: int, vectors_out: Path) -> dict:
    import numpy as np

    texts = chunk_texts(corpus, limit)
    encode, load_seconds = make_encoder(backend)
    encode(texts[:32])  # warm up
    started = time.perf_counter()
    vectors = encode(texts)
    elapsed = time.perf_counter() - started
    np.save(vectors_out, vectors)
    return {
        "backend": backend,
        "chunks": len(texts),
        "load_s": round(load_seconds, 2),
        "encode_s": round(elapsed, 2),
        "chunks_per_s": round(len(texts) / elapsed, 1),
        "peak_rss_mb": peak_rss_mb(),
    }


def write_reference(path: Path) -> None:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL, device="cpu")
    vectors = model.encode(REFERENCE_TEXTS, normalize_embeddings=True, convert_to_numpy=True)
    payload = {
        "model": MODEL,
        "source": "sentence-transformers, device cpu, normalized; "
        "written by scripts/measure_embedding_backends.py",
        "texts": REFERENCE_TEXTS,
        "vectors": [[round(float(x), 6) for x in row] for row in vectors],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    print(f"Wrote {len(REFERENCE_TEXTS)} reference vectors to {path}")


def drive(backends: List[str], corpus: Path, limit: int) -> int:
    import numpy as np

    rows, vectors = [], {}
    with tempfile.TemporaryDirectory() as tmp:
        for backend in backends:
            out = Path(tmp) / f"{backend}.npy"
            cmd = [sys.executable, __file__, "--child", backend, "--corpus", str(corpus),
                   "--limit", str(limit), "--vectors-out", str(out)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"{backend}: FAILED\n{proc.stderr[-800:]}", file=sys.stderr)
                return 1
            rows.append(json.loads(proc.stdout.strip().splitlines()[-1]))
            vectors[backend] = np.load(out)

    reference = backends[0]
    print(f"\ncorpus {corpus}  chunks {rows[0]['chunks']}  reference {reference}\n")
    print(f"{'backend':<10} {'load s':>7} {'encode s':>9} {'chunks/s':>9} {'peak MB':>8} "
          f"{'cos min':>8} {'cos mean':>9}")
    for row in rows:
        a, b = vectors[reference], vectors[row["backend"]]
        cos = (a * b).sum(axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
        print(
            f"{row['backend']:<10} {row['load_s']:>7} {row['encode_s']:>9} "
            f"{row['chunks_per_s']:>9} {row['peak_rss_mb']:>8} "
            f"{cos.min():>8.5f} {cos.mean():>9.6f}"
        )
    print(json.dumps(rows))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--corpus", type=Path, default=Path("src/nexus_mcp"))
    parser.add_argument("--limit", type=int, default=1500, help="number of chunks to embed")
    parser.add_argument("--torch", action="store_true", help="also run torch-cpu and torch-mps")
    parser.add_argument(
        "--write-reference", type=Path, help="write torch reference vectors to this file"
    )
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--vectors-out", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.write_reference:
        write_reference(args.write_reference)
        return 0
    if args.child:
        print(json.dumps(run_one(args.child, args.corpus, args.limit, args.vectors_out)))
        return 0
    backends = (
        ["torch-cpu", "torch-mps", "onnx-fp32", "onnx-int8"] if args.torch else ["onnx-int8"]
    )
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return drive(backends, args.corpus, args.limit)


if __name__ == "__main__":
    sys.exit(main())

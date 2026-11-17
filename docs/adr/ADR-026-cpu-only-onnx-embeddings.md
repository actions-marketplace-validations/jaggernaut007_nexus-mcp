# ADR-026: CPU-Only Embeddings on ONNX Runtime, PyTorch Optional, jina-code Removed

## Status: Accepted
## Date: 2026-11-17
## Supersedes (in part): ADR-003 (the PyTorch claims), ADR-004 (the `jina-code` parts)

## Context

The default model `bge-small-en` ran on PyTorch through `sentence-transformers`. ADR-003 says
that ONNX Runtime replaced PyTorch. That was true only for the deprecated `jina-code` model.

PyTorch was the largest part of the install:

- On macOS `torch` is 556 MB of a 1.5 GB venv.
- On Linux, `pip` installs the CUDA wheels. The LGR run (`docs/LGR-RUN-ANALYSIS.md`) made a
  3.1 GB venv and a 6.0 GB uv cache, and hit a 10 GiB sandbox limit.
- A package cannot tell `pip` to use the CPU-only wheels of PyTorch.

A GPU helps only one task: the first index of a large repository. A search embeds one short
query. The model has 33 M parameters, so the CPU needs a few milliseconds for it.

The owner decided on 2026-11-17: ship a CPU-only build, run the default model on ONNX Runtime
with the int8 file, keep PyTorch as an option, and remove `jina-code`.

## Decision

- **The default model `bge-small-en` is the int8 ONNX file** of `Xenova/bge-small-en-v1.5`
  (`onnx/model_quantized.onnx`, 32 MB). The repo is pinned to a commit.
- **A native encoder, `OnnxEncoder`,** runs the model: `tokenizers` for the text and
  `onnxruntime` for the model. The core dependencies lose `sentence-transformers` and
  `optimum`, and so `torch`, `transformers` and the CUDA packages.
- **PyTorch is an option.** The extra `nexus-mcp-ci[torch]` installs `sentence-transformers`.
  The model name `bge-small-en-torch` selects it (fp32 weights, `BAAI/bge-small-en-v1.5`). It
  runs on the CPU. It is faster on Apple Silicon (see Measurements).
- **The model name is the identity of the index.** `bge-small-en` and `bge-small-en-torch`
  give vectors that differ a little (cosine 0.9936 at the lowest). A switch rebuilds the index
  through the model check that exists already. `CHUNK_FORMAT` is now 9, so an index of an older
  version, which holds fp32 vectors under the name `bge-small-en`, is rebuilt one time.
- **CPU only.** `NEXUS_EMBEDDING_DEVICE` stays, with the default `cpu`. Another value is
  ignored with a warning. The `gpu` extra and the device detection are removed.
- **`jina-code` is removed** with all code that only it used: the registry entry, the
  `deprecated` warning, `trust_remote_code` (the setting, the variable `NEXUS_TRUST_REMOTE_CODE`
  and the gate), the ONNX branch of the `SentenceTransformer` loader, and the `onnx` extra.
  `NEXUS_EMBEDDING_MODEL=jina-code` gives a message that says what to do.
- **The ONNX model runs one chunk in each call** (`ONNX_MAX_BATCH = 1`), for two reasons:
  - Memory. A call of 32 chunks of 512 tokens kept more than 1.3 GB in the memory arena of
    ONNX Runtime. An index of `src/nexus_mcp` peaked at 1.3 GB (batch 32), 0.6 GB (batch 8) and
    0.3 GB (batch 1).
  - Determinism. The int8 file quantizes its activations with one scale for the whole batch.
    The vector of a text then changed with the other texts in its batch: the cosine between
    a text alone and the same text in a batch was 0.9933, and the largest difference of one
    component was 0.02. The fp32 file has no such effect (difference 0.0). With one text in a
    call, a vector depends only on its own text.
  - Speed does not change on a CPU: 20 to 26 chunks/s for batch 1 against 21 for batch 8.
- The version is **3.0.0**, because these changes break setups that use `jina-code`, the
  `gpu` extra or a GPU device.

## Measurements

Apple Silicon laptop, CPU, 2026-11-17. The machine was shared, so the speed varies from run to
run. Read the ratios, not the absolute values. Script: `scripts/measure_embedding_backends.py`.

Final run (300 chunks, with `ONNX_MAX_BATCH = 1`). The peak RSS includes about 130 MB for
parsing the chunks in the same process.

| Backend | Chunks/s | Peak RSS | Cosine to torch fp32 (min / mean) |
|---|---|---|---|
| torch CPU (fp32) | 24.9 | 689 MB | 1 |
| torch MPS (fp32) | 41.2 | 460 MB | 1.00000 |
| ONNX fp32 | 9.8 | 389 MB | 1.00000 |
| **ONNX int8 (default)** | **19.8** | **277 MB** | 0.9934 / 0.9966 |

- The ONNX fp32 vectors equal the torch vectors. So the tokenizer, the pooling and the
  normalisation of the native encoder are right.
- On this machine PyTorch is faster than ONNX Runtime for fp32, because PyTorch uses the matrix
  hardware of Apple Silicon. Over several runs, torch CPU gave 18.8 to 26.7 chunks/s and int8
  gave 16.5 to 22.3 (the machine was shared). In a run with a high load average, torch CPU
  was 15.5 and 11.4 chunks/s, int8 7.5 and 6.6, and fp32 4.0 and 2.0. The int8 file is about
  2 times faster than fp32. The speed on Linux x86_64 is not measured.
- Retrieval, `python -m evals.retrieval.run`, same corpus, torch fp32 against int8 (final
  configuration, one chunk in each call):

  | Suite | Mode | torch fp32 hit@1 | int8 hit@1 | torch / int8 hit@5 |
  |---|---|---|---|---|
  | `nexus_mcp` (24 queries) | hybrid | 0.79 | 0.79 | 0.88 / 0.92 |
  | `nexus_mcp` | vector only | 0.58 | 0.54 (one query fewer) | 0.92 / 0.96 |
  | `shop_repo` (8 queries) | hybrid | 0.62 | 0.62 | 1.00 / 1.00 |
  | `shop_repo` | vector only | 0.75 | 0.75 | 1.00 / 1.00 |

  The hybrid mode is what `search` uses. The `flask` suite needs a clone of the Flask
  repository, and the `jobscout` suite is private. Both are **not run yet**, so the gate of
  the north star is not complete for them.
- Index time of the `nexus_mcp` suite: 92.8 s (torch fp32) against 71.1 s (int8). Peak RSS of
  the process: 820 MB against 289 MB.
- Memory of a running server: see `docs/MEMORY.md` (search 270 MB and index 311 MB with the
  default model; torch: 483 MB and 801 MB).
- Download size of the wheels, from `uv.lock`: macOS arm64 336 MB to 157 MB; Linux x86_64
  1,054 MB to 185 MB. The model file is 32 MB instead of 133 MB.

## Consequences

- The install is smaller and the same on every platform. The default install has no CUDA
  packages and no PyTorch import (a test checks this).
- On Apple Silicon the first index of a large repository is slower than with PyTorch on the
  GPU (`docs/BACKLOG.md` says MPS was 2.4 times faster than the CPU). A user who wants speed
  installs the `torch` extra. The GPU is not supported any more.
- Each existing index rebuilds one time after the upgrade. Stored memories are not
  re-embedded: the memory store compares the vector width only, so memories of 2.x (fp32) and
  new queries (int8) meet with a cosine of 0.993 at the lowest. A switch between `bge-small-en`
  and `bge-small-en-torch` (same width) has the same effect. Re-embedding on a model change is
  an open item.
- Both shipped models are pinned to a commit of their Hugging Face repo.
- A user who runs offline needs the 32 MB file of the new repo once. The error names it.
- Hybrid hit@1 of the default model equals the PyTorch model on the two suites that ran. The
  `flask` and `jobscout` suites must run before the 3.0.0 release.

## Alternatives rejected

- **Keep PyTorch and set the device to `cpu`:** the Linux install keeps the CUDA wheels.
- **ONNX with the fp32 file:** the vectors equal torch, and no index rebuilds. But it is about
  2 times slower than int8 and its download is 4 times larger.
- **The ONNX CoreML provider** to get Metal back: it is not reliable for transformer models
  with dynamic shapes, and the owner wants a CPU-only build.
- **Keep `jina-code` behind an extra:** it needs `trust_remote_code`, a 612 MiB download, and
  indexes far slower on CPU. ADR-004 deprecated it. The owner wants it removed.

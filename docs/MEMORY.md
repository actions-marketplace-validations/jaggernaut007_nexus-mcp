# Memory use

Measured on 2026-11-17 with `scripts/measure_memory.py` on an Apple Silicon laptop, CPU only
(Nexus-MCP 3.0.0). The project indexed was `src/nexus_mcp` (61 files, 938 chunks). Each phase
ran in a fresh process, so its peak does not include an earlier phase. The figure is peak
resident memory (RSS), imports included.

| Phase | Default `bge-small-en` (int8 ONNX) | `bge-small-en-torch` | What it is |
|---|---|---|---|
| idle | 89 MB | 89 MB | The server is created. No tool has run |
| restore | 119 MB | 120 MB | A new process reattaches to the stored index and answers a graph query. No model is loaded |
| search | 270 MB | 483 MB | A new process runs one search. This loads the embedding model |
| index | 311 MB | 801 MB | A full index of the project (parse, embed, store) |

## What this means

- The default model **meets the old 350 MB target**, for a running server and for indexing a
  small project: `search` peaks at 270 MB and `index` at 311 MB. A session that only uses
  `status`, `graph`, `map` or `analyze` stays near 120 MB.
- A large repository costs more: the graph and the call-edge metadata grow with the number of
  files (a 14,000-file Python library peaked near 770 MB for parsing and call resolution alone).
- The ONNX model runs one chunk in each call (`ONNX_MAX_BATCH = 1`). A batch of 32 made the same
  index peak above 1.3 GB, because the attention scores of 32 chunks of 512 tokens stay in the
  memory arena of ONNX Runtime, and a batch of 8 peaked at about 620 MB. The speed on a CPU is
  the same for every batch size. One chunk in each call also makes the vector of a chunk
  depend only on that chunk (see ADR-026).
- Before 3.0.0 the figures came from a run on the GPU through MPS (idle 87 MB, restore 271 MB,
  search 460 MB, index 505 MB on a project of 485 chunks). The project has twice the chunks
  now, and the restore phase no longer imports PyTorch, so the old and new values do not
  compare one to one.
- The numbers come from one machine and one small project. Treat them as an order of magnitude.
  A Linux machine will differ.

## Ways to lower it (not done)

A lower `max_seq_length` and unloading the model after an idle period.

## Reproduce

```bash
PYTHONPATH=src python scripts/measure_memory.py src/nexus_mcp
PYTHONPATH=src python scripts/measure_memory.py src/nexus_mcp --model bge-small-en-torch  # needs the torch extra
```

Embedding speed and vector agreement of the backends:
`python scripts/measure_embedding_backends.py --torch` (needs the torch extra). See
[ADR-026](adr/ADR-026-cpu-only-onnx-embeddings.md).

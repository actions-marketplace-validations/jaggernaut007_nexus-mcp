# Comparison with other code-intelligence tools

This page lists features only, from each project's own documentation. To compare on your own
code, run `uvx --python 3.12 nexus-mcp-ci eval`.

The measured head-to-heads are on the
[benchmarks page](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html):

- **Django and Home Assistant (2026-11-09):** Nexus-MCP, GitNexus, codebase-memory-mcp, Serena
  and Graphify. All five answered every run correctly. Nexus-MCP used the fewest tokens on most
  tasks, but it cost the least on few of them. The agent rarely called the other four tools, so
  their rows mostly measure Read, Grep and Glob. Grep and Read alone cost less than every tool.
- **Chromium `base/` (2026-11-04):** Nexus-MCP and GitNexus. Grep and Read cost less than both
  on that C++ code.

## Code-intelligence MCP servers

| Feature | Nexus-MCP | Serena | claude-context |
|---|:---:|:---:|:---:|
| How it finds code | hybrid: vectors + BM25 | language servers (LSP) | hybrid: vectors + BM25 |
| Where the index lives | local LanceDB | none (live LSP) | a vector database (Milvus or Zilliz Cloud) |
| Embeddings | local model, no API key | none | an embedding provider (OpenAI by default; Ollama possible) |
| Call graph | static, 10 languages | LSP references | no |
| Project memory | yes (`memory`, opt-in tool) | yes | no |
| Edits code | no (read only) | yes | no |
| Measure it on your repo | `nexus-mcp-ci eval` | – | – |

Other servers in this space include GitNexus, codebase-memory-mcp, code-graph-rag,
code-index-mcp and ast-grep-mcp. Check each project's docs. Corrections to this table are
welcome: open an issue or a pull request.

## When to pick which

- **Nexus-MCP:** you want fewer agent tokens on code the model does not know, fully local, with
  no API key, and a read-only server.
- **Serena:** you want the agent to edit through language-server operations (rename, insert).
- **claude-context:** you already run a vector database and an embedding provider.
- **Your agent's own grep:** you know the exact name or string. Keep it next to any of these.

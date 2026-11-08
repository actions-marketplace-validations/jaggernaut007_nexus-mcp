# Nexus-MCP plugin

Your coding agent reads less code to find the right file. Nexus-MCP is a local MCP server with
hybrid code search, a static call graph for 10 languages and the code of any symbol, so Claude
reads fewer files.

## Results

Measured against Grep and Read on 12 open-source repos in 10 languages: 95% fewer tokens to
reach the right file (median of 10 repos), and a lower cost per task on 8 of 11 repos in live
Claude Code runs. C and Rust used more tokens on an older build. Every number, per repo, with
the method: [benchmarks](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html) and the
[README](https://github.com/jaggernaut007/Nexus-MCP#results).

## What you need

- [uv](https://docs.astral.sh/uv/). The plugin starts the server with
  `uvx --python 3.12 nexus-mcp-ci==<version>`, so there is no `pip install`.
- The first index downloads the embedding model `BAAI/bge-small-en-v1.5` from Hugging Face
  (about 130 MB), once.

## What is in the plugin

| Part | What it does |
|---|---|
| MCP server `nexus-mcp` | 5 tools: `index`, `search`, `find_symbol`, `graph`, `map`. Set `NEXUS_EXTRA_TOOLS=all` to add `status`, `analyze` and `memory`. |
| Skill `nexus-mcp` | Tells Claude when to use the tools instead of reading files one by one. |
| Skill `evaluate` | Runs the free offline check `nexus-mcp-ci eval` on your project and explains the verdict. |
| Agent `nexus-evaluator` | Runs the same offline check and returns only the summary. |

## Data and privacy

The server runs on your machine. It makes no network call of its own, has no telemetry and
needs no API key. The index (code snippets and vectors) is stored in `<project>/.nexus/`.

Data leaves the machine only in these opt-in steps of the `evaluate` skill. Each one runs only
after you say yes:

| Step | Sent to | What |
|---|---|---|
| Agent tier (`eval --agent`) | Anthropic or OpenAI, through **your own** `claude` or `codex` CLI | the prompts and the code that the agent reads; it spends your own LLM API tokens |
| Share (`eval share`) | a GitHub issue on this repository | allow-listed numbers only: no paths, no code, no question text. You see the exact payload first. |

The plugin itself keeps no data: there is no Nexus-MCP service. Full details:
[PRIVACY.md](https://github.com/jaggernaut007/Nexus-MCP/blob/main/docs/PRIVACY.md).

## Links

- [Documentation](https://github.com/jaggernaut007/Nexus-MCP/blob/main/docs/README.md)
- [Issues and support](https://github.com/jaggernaut007/Nexus-MCP/issues)
- License: [MIT](https://github.com/jaggernaut007/Nexus-MCP/blob/main/LICENSE)

# Nexus-MCP

[![PyPI version](https://img.shields.io/pypi/v/nexus-mcp-ci)](https://pypi.org/project/nexus-mcp-ci/)
[![PyPI downloads](https://img.shields.io/endpoint?url=https%3A%2F%2Fjaggernaut007.github.io%2Fnexus-mcp%2Fdownloads-badge.json)](https://pepy.tech/projects/nexus-mcp-ci)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](#license)
[![CI](https://github.com/jaggernaut007/Nexus-MCP/actions/workflows/publish.yml/badge.svg)](https://github.com/jaggernaut007/Nexus-MCP/actions/workflows/publish.yml)
[![Glama MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

**Your coding agent finds the right file with 95% fewer tokens.** Nexus-MCP is a local MCP
server that gives Claude Code, Codex, Cursor and any MCP client hybrid code search and a code
graph. The agent asks one question and gets the code back, instead of a tour of grep and file
reads. No API keys, no cloud, MIT.

<!-- mcp-name: io.github.jaggernaut007/nexus-mcp -->

```bash
uvx --python 3.12 nexus-mcp-ci setup
```

`setup` finds the MCP clients on your machine, shows each change and asks before it writes.
You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Or tell your agent:
*"Install Nexus-MCP from https://github.com/jaggernaut007/Nexus-MCP"*.

> ⭐ **If Nexus-MCP saves you tokens, [star the repo](https://github.com/jaggernaut007/Nexus-MCP).** It helps other developers find it.

---

## Results

Measured on 12 open-source repos in 10 languages, against an agent that has only its built-in
Grep and Read. Nothing here is an estimate: each number links to a pinned repo and a raw run.

<!-- Generated from benchmarks/published/results.json by scripts/publish_results.py. Do not edit. -->
<!-- results:start -->
**On 10 open-source repos in 10 languages, Nexus-MCP reached the right file with 95% fewer tokens (median) and searched 9× faster than Grep and Read. In live Claude Code runs on 11 repos, cost per task was lower on 8 of 11, with 64 of 66 tasks correct against 61 of 66.**

**Find the right file** (`nexus-mcp-ci eval`: offline, no LLM). Grep and Read → Nexus-MCP:

| Language | Repo | Right file first | Tokens to the right file | Search time |
|---|---|---:|---:|---:|
| Python | [pallets/flask](https://github.com/pallets/flask) (83 files) | 20% → **93%** | 26,720 → **1,388** | 108 → **23 ms** |
| TypeScript | [vitejs/vite](https://github.com/vitejs/vite) (429 files) | 43% → **83%** | 16,520 → **1,286** | 159 → **25 ms** |
| JavaScript | [webpack/webpack](https://github.com/webpack/webpack) (822 files) | 37% → **67%** | 25,634 → **1,251** | 564 → **34 ms** |
| Java | [apache/commons-lang](https://github.com/apache/commons-lang) (264 files) | 23% → **87%** | 48,176 → **1,241** | 201 → **31 ms** |
| C# | [JamesNK/Newtonsoft.Json](https://github.com/JamesNK/Newtonsoft.Json) (242 files) | 23% → **73%** | 14,004 → **1,547** | 154 → **25 ms** |
| PHP | [laravel/framework](https://github.com/laravel/framework) (1,751 files) | 50% → **83%** | 13,479 → **1,086** | 712 → **39 ms** |
| C++ | [chromium/chromium](https://chromium.googlesource.com/chromium/src) (3,641 files) | 43% → **80%** | 19,768 → **1,243** | 1,851 → **88 ms** |
| Go | [prometheus/prometheus](https://github.com/prometheus/prometheus) (201 files) | 40% → **83%** | 41,034 → **1,195** | 600 → **24 ms** |
| C | [redis/redis](https://github.com/redis/redis) (229 files) | 40% → **87%** | 37,139 → **988** | 331 → **34 ms** |
| Rust | [tokio-rs/tokio](https://github.com/tokio-rs/tokio) (381 files) | 33% → **93%** | 21,138 → **830** | 215 → **27 ms** |

**Live agent runs** (Claude Code with Claude Sonnet, medium effort, headless, 6 tasks per repo, 3 runs each, median per task). Grep and Read → Nexus-MCP:

| Language | Repo | Tokens per task | Cost per task | Turns | Correct |
|---|---|---:|---:|---:|---:|
| Python | [home-assistant/core](https://github.com/home-assistant/core) | 65,990 → **42,458 (-36%)** | $0.050 → **$0.037 (-27%)** | 7 → 3 | 6 → 6 of 6 |
| Python | [django/django](https://github.com/django/django) | 45,102 → **41,916 (-7%)** | $0.048 → **$0.035 (-26%)** | 5.5 → 2.5 | 5 → 6 of 6 |
| TypeScript | [vitejs/vite](https://github.com/vitejs/vite) | 69,790 → **52,411 (-25%)** | $0.059 → **$0.046 (-22%)** | 5.5 → 4 | 6 → 6 of 6 |
| JavaScript | [webpack/webpack](https://github.com/webpack/webpack) | 100,320 → **74,638 (-26%)** | $0.077 → **$0.068 (-11%)** | 6 → 4.5 | 6 → 6 of 6 |
| Java | [apache/commons-lang](https://github.com/apache/commons-lang) | 50,448 → **32,862 (-35%)** | $0.044 → **$0.022 (-49%)** | 4.5 → 2 | 6 → 6 of 6 |
| C# | [JamesNK/Newtonsoft.Json](https://github.com/JamesNK/Newtonsoft.Json) | 41,929 → 42,139 (+1%) | $0.030 → $0.030 (+1%) | 3 → 2.5 | 6 → 6 of 6 |
| PHP | [laravel/framework](https://github.com/laravel/framework) | 52,919 → 51,994 (-2%) | $0.051 → **$0.039 (-24%)** | 5 → 3 | 5 → 5 of 6 |
| C++ | [chromium/chromium](https://chromium.googlesource.com/chromium/src) | 70,495 → **60,622 (-14%)** | $0.065 → **$0.055 (-16%)** | 6.5 → 4.5 | 6 → 6 of 6 |
| Go | [prometheus/prometheus](https://github.com/prometheus/prometheus) | 52,014 → **45,214 (-13%)** | $0.047 → **$0.037 (-22%)** | 4.5 → 2.5 | 5 → 6 of 6 |
| C | [redis/redis](https://github.com/redis/redis) | 41,752 → 50,578 (+21%) | $0.031 → $0.030 (-3%) | 3.5 → 3 | 6 → 6 of 6 |
| Rust | [tokio-rs/tokio](https://github.com/tokio-rs/tokio) | 41,188 → 61,816 (+50%) | $0.029 → $0.041 (+42%) | 3.5 → 4.5 | 4 → 5 of 6 |

Not there yet: C (redis/redis, +21% tokens, build 2.2.2+e5a9d9a) and Rust (tokio-rs/tokio, +50% tokens, build 2.2.2+0430f01). These runs are older; a rerun on the current build is pending. Each row is the latest run of that repo; the build of each row is on the benchmarks page. The same code can vary by about 20% in cost with 3 runs per task (measured 2026-10-28).

All numbers, the method and the raw runs: [benchmarks page](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html) · [JSON](https://jaggernaut007.github.io/nexus-mcp/benchmarks.json) · [CSV](https://jaggernaut007.github.io/nexus-mcp/benchmarks.csv) · [runs](https://github.com/jaggernaut007/Nexus-MCP/tree/main/benchmarks/runs).
<!-- results:end -->

What the numbers mean:

- **Right file first:** the share of questions where the first result is the file that defines
  the answer. The right file comes from the parser, never from the Nexus-MCP ranking.
- **Tokens to the right file:** what the agent reads before it reaches that file.
- **Search time:** the time of one lookup on a laptop, after the index is built.
- **Tokens and cost per task:** a real Claude Code session on a real question, both conditions
  with the same model, prompt and tools. Fewer turns is where most of the saving comes from:
  each turn reads the whole conversation again.

---

## Measure it on your own code first

The check is free, runs offline, calls no LLM and takes a few minutes:

```bash
uvx --python 3.12 nexus-mcp-ci eval                                   # the current folder
uvx --python 3.12 nexus-mcp-ci eval https://github.com/pallets/flask  # a public repo
uvx --python 3.12 nexus-mcp-ci eval --example                         # the Nexus-MCP source
```

It asks about 30 questions made from your code and gives a verdict: "Strong fit", "Likely fit"
or "Weak fit". On a tiny project Grep and Read win, and the report says so. For the real agent
cost, `nexus-mcp-ci eval --agent claude` runs a paid A/B with your own Claude Code login. It
shows the cost and asks first. Guide: [docs/EVALUATE.md](docs/EVALUATE.md).

---

## Install

| Client | How |
|---|---|
| Any client on your machine | `uvx --python 3.12 nexus-mcp-ci setup` (add `--dry-run` to see the changes, `--uninstall` to remove them) |
| Claude Code plugin | `/plugin marketplace add jaggernaut007/Nexus-MCP`, then `/plugin install nexus-mcp@nexus-mcp` |
| VS Code | [Install in VS Code](https://vscode.dev/redirect/mcp/install?name=nexus-mcp&config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--python%22%2C%223.12%22%2C%22nexus-mcp-ci%22%5D%7D) |
| Cursor | the "Add to Cursor" link on the [website](https://jaggernaut007.github.io/nexus-mcp/#install) |
| Claude Desktop | open `nexus-mcp.mcpb` from the [latest release](https://github.com/jaggernaut007/Nexus-MCP/releases/latest) |
| Gemini CLI | `gemini extensions install https://github.com/jaggernaut007/Nexus-MCP` |
| Docker | `docker run -i --rm -v "$PWD:/workspace" -v nexus-data:/data ghcr.io/jaggernaut007/nexus-mcp` |
| By hand | `pip install nexus-mcp-ci`, then `claude mcp add nexus-mcp -- nexus-mcp-ci` |

Use `setup` or the plugin for Claude Code, not both. Then start a new session and ask a
question about your code, such as *"where do we retry failed payments?"*. The agent indexes the
project once, in the background, and a file watcher keeps the index current. Every channel:
[docs/DISTRIBUTION.md](docs/DISTRIBUTION.md). Python 3.10 to 3.12; the `uvx` launch pins 3.12.

---

## What your agent gets

The server shows five tools by default. Each one replaces several grep and read calls.

| Tool | Use it for |
|---|---|
| `search(query)` | "Where is…", "how does… work". Hybrid search: meaning (local embeddings) plus keywords (BM25). The top results carry their whole code. |
| `find_symbol(symbol_name)` | Names you know. The code of several functions or classes in one call, with their callers and callees. A long class comes back as an outline. |
| `graph(symbol_name)` | "Who calls X", "what breaks if I change X". Callers, callees and the transitive blast radius before a refactor. |
| `map(detail)` | "Where do I start". Layers, entry points, hub symbols and languages of the repo. |
| `index(path)` | The first call in a project. Incremental after that, and multi-folder for monorepos. |
| `memory(action)` | Opt-in. Decisions and notes that last across sessions. On by default when memories exist. |
| `analyze(path)` | Opt-in. Complexity, code smells and dependency metrics for a path. |
| `status()` | Opt-in. Index health and a staleness flag. |

Set `NEXUS_EXTRA_TOOLS=all` to show the three opt-in tools. The server sends its own routing
instructions when it connects, so the agent knows which tool answers which question. Full
reference: [docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md).

Keep your agent's own grep. Use it for an exact name or string you can type; use Nexus-MCP
when the answer needs meaning or structure.

---

## Languages

| Support | Languages |
|---|---|
| Search, symbols, call graph | Python, TypeScript, JavaScript, Go, Java, Rust, C, C++, C#, PHP |
| Search and symbols | Bash, Kotlin |
| Search (text and embeddings) | 25+ languages through tree-sitter |

The results above cover the ten graph languages. Python has the best call-graph coverage.

---

## Privacy

Your code stays on your machine. The server makes no network calls and has no telemetry.

- **In, once:** the package from PyPI and the embedding model from Hugging Face. Set
  `NEXUS_OFFLINE=1` after that.
- **Out:** nothing, unless you run `eval --agent` (through your own Claude or Codex CLI) or
  `eval share` (numbers only). Both ask first.
- **Stored:** the index in `<project>/.nexus/` (add it to `.gitignore`).
  `nexus-mcp-ci data where` lists every file; `nexus-mcp-ci data clear` removes them.

Details: [docs/PRIVACY.md](docs/PRIVACY.md).

---

## Known limits

- **The call graph is static.** Edges come from parsing, not from running the code. Dynamic
  dispatch, callbacks and reflection do not show, so treat `graph` results as a lower bound.
- **C and Rust live runs used more tokens** than Grep and Read on the older 2.2.2 build (see
  Results). A rerun on the current build is pending.
- **Memory:** about 90 MB idle and about 460 MB with the embedding model loaded
  ([measurements](docs/MEMORY.md)).
- **Freshness:** edits are picked up after a debounce of a few seconds, not instantly.
- **Transport:** stdio only.

---

## Documentation

- [Benchmarks](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html): every number, the build of each row, the method and the raw data
- [Evaluate](docs/EVALUATE.md), [Installation](docs/INSTALLATION.md), [Usage](docs/USAGE_GUIDE.md), [Configuration](docs/CONFIGURATION.md), [Troubleshooting](docs/TROUBLESHOOTING.md), [FAQ](docs/FAQ.md)
- [Architecture](docs/ARCHITECTURE.md), [Comparison with other tools](docs/COMPARISON.md), [Decision records](docs/adr/), [Changelog](CHANGELOG.md)
- All docs: [docs/README.md](docs/README.md). For agents: [llms.txt](llms.txt).

---

## Contributing

Issues and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the
[developer guide](docs/DEVELOPER_GUIDE.md):

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP.git && cd Nexus-MCP
pip install -e ".[dev]"
pytest -v && ruff check .
```

A change ships only when it lowers the agent's token use and keeps the search accuracy gate
(`python -m evals.retrieval.run`). The rule is in [CONTRIBUTING.md](CONTRIBUTING.md).

Nexus-MCP grew from two MIT projects: [CodeGrok MCP](https://github.com/shreyasjagannath/CodeGrok_mcp)
by [rdondeti](https://github.com/rdondeti) and [code-graph-mcp](https://github.com/entrepeneur4lyf/code-graph-mcp)
by [entrepeneur4lyf](https://github.com/entrepeneur4lyf) ([ADR-001](docs/adr/ADR-001-single-mcp-consolidation.md)).

---

## License

[MIT](LICENSE).

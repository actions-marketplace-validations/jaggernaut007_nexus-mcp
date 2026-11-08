# Evaluate Nexus-MCP on your code

Measure Nexus-MCP on your own project before you adopt it. The first check is free, runs offline
and calls no LLM. Your code does not leave your machine.

```bash
uvx --python 3.12 nexus-mcp-ci eval
```

The command indexes the current folder in a temporary place, asks about 30 questions made from
your own code, and compares two ways to reach the right file:

- **Grep and Read**: the way an agent works without Nexus-MCP.
- **Nexus-MCP**: one `search` call, plus file reads only when the search misses.

It prints a verdict ("Strong fit", "Likely fit" or "Weak fit"), a table, and a "Data in this run"
block. The first run downloads the embedding model once (about 130 MB).

## Choose what to evaluate

| Command | What it reads | Network |
|---|---|---|
| `nexus-mcp-ci eval` | the current folder | none (after the model download) |
| `nexus-mcp-ci eval path/to/project` | that folder | none |
| `nexus-mcp-ci eval https://github.com/pallets/flask` | a shallow clone of a public repo | `git clone` |
| `nexus-mcp-ci eval --example` | the installed Nexus-MCP source (59 files) | none |

Use a local path for private code: it never leaves the machine. Use a public URL when you want a
result that other people can repeat (the run records the commit).

`uvx` needs Python 3.10 to 3.12. If your default Python is newer, run
`uvx --python 3.12 nexus-mcp-ci eval`. `pipx run nexus-mcp-ci eval` and
`pip install nexus-mcp-ci && nexus-mcp-ci eval` also work.

## Read the verdict

| Verdict | Rule |
|---|---|
| Strong fit | Right file in the top 3 at least 10 points more often, and at least 30% fewer tokens |
| Likely fit | Right file in the top 3 at least as often, and at least 15% fewer tokens |
| Weak fit | Anything else, or a project with fewer than 50 files |

A "Weak fit" is a valid result. On a small or very well named project, Grep and Read are cheap,
and the report says so. On an 18-file toy project and on a synthetic 119-file project,
Grep and Read used fewer tokens.

The numbers are a token proxy (characters divided by 4), not a bill. The agent tier below
measures the real cost.

Example results (2026-11-03, 30 to 40 questions each, an older build). The README and the
[benchmarks page](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html) have the latest
run of 10 repos in 10 languages, Chromium `base/` included:

| Project | Files | Verdict | Median tokens: Grep and Read → Nexus-MCP | Right file first |
|---|---:|---|---:|---:|
| [Chromium `base/`](https://github.com/chromium/chromium/tree/599789097af11286fca074380e8a3634cea2fa25/base) | 3,638 | Strong fit | 17,998 → 950 | 40% → 70% |
| [pallets/click](https://github.com/pallets/click) | 91 | Strong fit | 24,774 → 1,301 | 27% → 97% |
| Nexus-MCP source (`--example`) | 59 | Likely fit | 5,385 → 1,234 | 70% → 93% |

Indexing took about 25 minutes for Chromium `base/`, 1 to 2 minutes for click and about 1 minute for the Nexus-MCP source, on a laptop. The click and Nexus-MCP rows are in
`benchmarks/published/results.json` (`quick_tier`); the Chromium row was replaced there by a
newer run.

Question kinds:

- `concept`: the first sentence of a docstring, with the name removed (Python only).
- `described`: the words of the name and of its class.
- `lookup`: the exact name. This kind measures tokens only.

The right file is the file that defines the symbol, from the parser. It never comes from the
Nexus-MCP ranking.

## The agent tier (paid, opt-in)

```bash
nexus-mcp-ci eval --agent claude                       # Claude Sonnet, Grep and Read against Nexus-MCP
nexus-mcp-ci eval --agent claude:haiku,claude:sonnet   # two models
nexus-mcp-ci eval --agent codex:gpt-5-codex            # Codex, experimental
nexus-mcp-ci eval --preset efficient --agent claude:haiku,claude:sonnet
```

- It runs your own `claude` or `codex` CLI, with your own login, on tasks made from your code.
- It sends the prompts and the code that the agent reads to Anthropic or OpenAI.
- It shows a cost estimate and asks before it starts. It stops starting runs at `--max-usd`
  (default $5).
- Conditions: `baseline` (Grep, Glob and Read), `mcp` (the server), `nexus` (the server and its
  skill). Choose with `--conditions baseline,mcp,nexus`.
- Presets: `quick` (Claude Sonnet, 4 tasks), `matrix` (every `--agent` model), `efficient`
  (adds `mcp`, then names the cheapest model and setup whose accuracy is not below the baseline
  of the same model).
- One run per task has about 20% cost noise. Treat a small difference as a tie.

Run `nexus-mcp-ci eval doctor` first. It checks ripgrep, the cached model, the `claude` and
`codex` CLIs and the results folder.

## Saved results

Every run is saved in `~/.nexus-mcp/evals/<run-id>/` (`report.md`, `report.json`, `env.json`)
and listed in `history.jsonl`.

```bash
nexus-mcp-ci eval list
nexus-mcp-ci eval show <run-id>
nexus-mcp-ci eval show <run-id> --json
```

Add `--out DIR` to copy the report into your project. Set `NEXUS_EVAL_HOME` to keep results
somewhere else.

## Share a result (optional)

```bash
nexus-mcp-ci eval share <run-id>
```

It prints the exact payload: versions, size band, languages and the numbers. It holds no paths,
no code, no question text and no local project name. A public repo URL and commit are included
only for a URL source. It posts nothing unless you answer yes. With a logged-in `gh` it opens an
issue on `jaggernaut007/Nexus-MCP`; otherwise it prints the issue-form link. Accepted results
appear in the community table of the [benchmarks page](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html).
There is no telemetry.

## Adopt

If the verdict is good:

```bash
claude mcp add nexus-mcp -- nexus-mcp-ci
```

Run the eval with `--keep-index` first, and the index in `.nexus/` is ready when the server
starts. Add `.nexus/` to `.gitignore`: the index holds code snippets.

## Remove everything

```bash
nexus-mcp-ci data where
nexus-mcp-ci data clear --evals --yes
```

See [PRIVACY.md](PRIVACY.md) for every storage location and the shell commands that do the same.

## Codex and other agents

Codex reads `SKILL.md` files. Copy the evaluate skill to use it from Codex:

```bash
mkdir -p ~/.agents/skills/nexus-evaluate
cp plugin/skills/evaluate/SKILL.md ~/.agents/skills/nexus-evaluate/SKILL.md
```

In Claude Code, install the plugin and run `/nexus-mcp:evaluate`.

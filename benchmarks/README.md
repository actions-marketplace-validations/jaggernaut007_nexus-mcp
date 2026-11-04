# Token-efficiency benchmark

Implements ROADMAP-2026 P2 item 9: a ContextBench-style benchmark measuring
**wasted-read ratio** and **tokens-to-answer** for nexus-mcp vs. baseline
Claude Code (built-in `Read`/`Grep`/`Glob` only) on 1,000+ file repositories.

This is dev tooling, not part of the shipped `nexus-mcp-ci` package. Install
its one extra dependency with `pip install -e ".[bench]"`.

## Why this exists

The 2026 competitive landscape settled on: agentic grep is the default
baseline, but indexes win on **token efficiency**, not raw search quality —
Turbopuffer's ContextBench found baseline Claude Code wastes 1-in-3 file
reads, grep 1-in-5, grep+semantic 1-in-8. This harness measures where
nexus-mcp actually lands on that scale, honestly, with raw data published
alongside the summary.

## Quick start

```bash
pip install -e ".[bench]"
bash benchmarks/setup_repos.sh          # clone + pin repos, pre-build nexus index (one-time, minutes)
python -m benchmarks.runner --tasks benchmarks/tasks/django.yaml --smoke   # 2 tasks x 1 rep, <$2
python -m benchmarks.report benchmarks/results/runs-*.jsonl
```

Default model is the `sonnet` alias; override with `--model <id>` on `runner.py`.

**Permissions.** Runs are headless, so nothing can answer a prompt. The harness uses
`--permission-mode dontAsk` with an explicit `--allowedTools` list (`Read`, `Grep`, `Glob`,
`ToolSearch` and the nexus tools). A tool outside the list is denied, and the denial is
recorded. It does not use `bypassPermissions` or `--dangerously-skip-permissions`.

**Auth and limits.** The isolated config directory has no login. Export
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY` first. On a
subscription, a long run can hit the usage limit; the runner stops with exit code 3 and keeps
its records. Run the same command again later and it skips what already finished. The default
output file is `results/runs-<repo>.jsonl`, so reruns find it. `--save-raw` keeps each run's raw
stream under `results/raw/` so a number can be recomputed if the parser changes.

Full run (all tasks, both conditions, 3 reps — expect **$20-50** in API spend
and tens of minutes of wall time):

```bash
python -m benchmarks.runner --tasks benchmarks/tasks/django.yaml
python -m benchmarks.runner --tasks benchmarks/tasks/home-assistant.yaml
python -m benchmarks.report benchmarks/results/runs-*.jsonl --out benchmarks/results/report.md --csv benchmarks/results/report.csv
```

### Comparing embedding models, or benchmarking your own project

The `nexus` conditions can run once per embedding model. Each model needs its own index of
the repo, built first and outside the measured runs:

```bash
PYTHONPATH=src:. python -m benchmarks.preindex_models --repo django \
    --models bge-small-en,granite-97m-r2-int8
python -m benchmarks.runner --tasks benchmarks/tasks/django.yaml --smoke \
    --conditions baseline,nexus --embedding-models bge-small-en,granite-97m-r2-int8
```

Each record's condition is then `nexus@<model>` (for example `nexus@granite-97m-r2-int8`), so the report
shows one row per model beside `baseline`. `baseline` runs once, because it has no server.
`nexus-plugin` takes no model: the plugin starts its own server with the default model.
The models that are not in the shipped registry (the granite candidates, see
`evals/retrieval/candidates.py`) load through `benchmarks/nexus_server.py`, which registers
the model in memory only. This is the same code path as the shipped server.

For your own project, there are two ways:

- **Generated tasks (no YAML to write).** `--repo-path` points at any local folder and
  `--generate N` makes N tasks from its own symbols, as `nexus-mcp-ci eval` does. The right
  file comes from the parser. The suite is saved to `results/generated-<folder>.yaml`, so a rerun
  with `--tasks` uses the same tasks.

  ```bash
  python -m benchmarks.runner --repo-path ~/code/myproject --generate 8 --smoke
  python -m benchmarks.runner --tasks benchmarks/results/generated-myproject.yaml --repo-path ~/code/myproject
  ```

- **Hand-written tasks.** Put the code in `benchmarks/repos/<name>` (or pass `--repo-path`;
  check out only the code, so no `CLAUDE.md` or docs leak a hint into the runs), write a task
  file in the format below, and keep a private one under `benchmarks/tasks/private/`
  (git-ignored). Ground truth must be checked with grep against the pinned commit.

Most adopters do not need this harness: `nexus-mcp-ci eval --agent claude` runs a smaller A/B
on any folder (see `docs/EVALUATE.md`).

## Methodology

**Conditions:**
- `baseline` — `claude -p --tools Read,Grep,Glob` (no MCP servers, no skill).
- `mcp-only` — same built-in tools, plus the `nexus-mcp` MCP server and nothing else: no
  skill. It measures the tool descriptions and the server instructions alone.
- `nexus` — same built-in tools, plus the `nexus-mcp` MCP server
  (`benchmarks/mcp-configs/nexus.json`) and the shipped
  `plugin/skills/nexus-mcp/SKILL.md` body injected via `--append-system-prompt`.
  Skill injection is deterministic (always present) rather than relying on
  Claude's own skill-routing — this isolates "does nexus-mcp help when used"
  from "does Claude reliably discover the skill," which is a separate
  question. An optional `nexus-plugin` condition (`--plugin-dir plugin/`)
  measures the shipped routing behavior instead, if you want that number too.
- `gitnexus` — a competitor, for comparison. Same built-in tools, plus the GitNexus MCP
  server (`benchmarks/mcp-configs/gitnexus.json`, which runs `gitnexus mcp`) and the
  CLAUDE.md block that `gitnexus analyze` writes, given via `--append-system-prompt`.
  Claude Code always loads that block for a GitNexus user, so this is the same as the
  `nexus` condition. The on-demand GitNexus skills are not given. Set it up once for each
  repo:
  1. Install the pinned release: `npm install -g gitnexus@1.6.12`.
  2. Run `PYTHONPATH=src:. python -m benchmarks.setup_gitnexus --repo <name>` (or
     `--repo-dir <path>`). It checks the version, runs `gitnexus analyze` with the
     full-text search extension (`GITNEXUS_LBUG_EXTENSION_INSTALL=auto`), and writes the
     `<!-- gitnexus:start -->` block of the new `CLAUDE.md` to
     `benchmarks/repos/<name>.gitnexus-context.md`. Then it removes the `CLAUDE.md`,
     `AGENTS.md` and `.claude/skills/gitnexus-*` that `analyze` wrote, so the other
     conditions do not see them. A `CLAUDE.md` that was in the repo before keeps its text.

### Nexus-MCP and GitNexus on Chromium `base/`

The published head-to-head (README and the benchmarks page) used these steps. You need
Node (for GitNexus), Python 3.10 to 3.12, and about 1 GB of disk.

```bash
pip install -e ".[bench]"
npm install -g gitnexus@1.6.12
bash benchmarks/setup_chromium_base.sh   # sparse clone of base/ at the pin, then both indexes
PYTHONPATH=src:. python -m benchmarks.runner --tasks benchmarks/tasks/chromium-base.yaml \
    --conditions baseline,nexus,gitnexus --embedding-models bge-small-en --smoke
PYTHONPATH=src:. python -m benchmarks.runner --tasks benchmarks/tasks/chromium-base.yaml \
    --conditions baseline,nexus,gitnexus --embedding-models bge-small-en --reps 3 --warm-up
python -m benchmarks.report benchmarks/results/runs-chromium-base.jsonl
```

- The clone takes about a minute (147 MB). The Nexus-MCP index of `base/` took 43 minutes
  on an Apple-silicon laptop; GitNexus takes a few minutes.
- The smoke run is 2 tasks × 3 conditions × 1 run, about $0.50. The full run is 8 tasks ×
  3 conditions × 3 runs, about $5.50 (the published run cost $1.58, $1.64 and $2.13).
- To publish your numbers, open an issue with the report, or use
  `nexus-mcp-ci eval share` for the quick tier.

**Isolation:** each run gets `CLAUDE_CONFIG_DIR` pointed at
`benchmarks/.claude-bench` (gitignored) plus `--strict-mcp-config` and
`--disallowedTools Edit,Write,NotebookEdit,WebFetch,WebSearch,Task` (tasks are
read-only by design). With `ANTHROPIC_API_KEY` set, runs additionally use
`--bare` for full isolation (no hooks/plugins/CLAUDE.md/auto-memory). Without
an API key, runs fall back to `--setting-sources ""` — weaker isolation, and
every run record's `isolation_mode` field says which mode was actually used.
Judge your published numbers' credibility accordingly.

**Tasks:** `benchmarks/tasks/*.yaml`, hand-authored against a pinned commit
SHA in each repo (`git show <sha>:<path>` to verify any fact yourself). Four
categories: conceptual search, callers/impact analysis, architecture
overview, needle-in-haystack symbol lookup. Each task specifies
`relevant_files` (the ground-truth minimal file set), `acceptable_extra_files`
(reasonable neighborhood reads that don't count as waste),
`must_mention_files` (the subset an answer must cite by name — used for
file-recall scoring, falls back to `relevant_files` when empty), and `facts`
(groups of acceptable phrasings, at least one of which must appear).

An answer names a file well enough when it writes the full path, a shorter path
(`gateway/gateway.py`) or the bare file name (`gateway.py`) as a separate word. A generic
name such as `__init__.py` needs its parent folder. When a question has two valid answers, put
both paths in one inner list, for example
`must_mention_files: [["src/a/one.py", "src/a/two.py"]]`: naming either one counts.
After a change to the scoring rules, `python -m benchmarks.rescore --tasks <suite.yaml> --runs
<runs.jsonl>` rescores recorded runs from their stored answers, without a Claude call.

Full task-suite YAML schema:

```yaml
repo:
  name: django          # local clone dir under benchmarks/repos/<name>
  url: https://...       # git clone URL
  pin: <commit-sha>      # pinned SHA, checked out by setup_repos.sh
  pinned_date: "2026-..." # for the contamination check (see below)
  why: "..."              # why this repo, free text

defaults:                 # optional; merged under each task, task keys win
  max_budget_usd: 1.00
  timeout_s: 600

tasks:
  - id: some-unique-id
    category: conceptual   # conceptual | impact | architecture | needle
    prompt: "The question asked verbatim (a runner-added suffix forbids edits)."
    max_budget_usd: 1.00   # optional override of defaults
    timeout_s: 600         # optional override of defaults
    ground_truth:
      relevant_files: ["path/to/file.py"]
      acceptable_extra_files: []
      must_mention_files: ["path/to/file.py"]   # optional, see above
      facts:
        - any_of: ["phrase one", "phrase two"]  # at least one must appear
        - any_of: ["another required phrase"]
```

`facts` is a list of groups; each group's `any_of` is a list of acceptable
phrasings (case-insensitive substring match) — the group is satisfied if
**any one** of them appears in the final answer, and `fact_score` is the
fraction of groups satisfied.

**Metrics** (see `benchmarks/scoring.py` and `benchmarks/transcript.py` for
exact formulas):
- **Wasted-read ratio** = fraction of files surfaced to the model that are
  outside the ground-truth relevant set. For `baseline`, "surfaced" = files
  opened via `Read`. For `nexus`, it additionally includes every file named
  in a nexus tool's result payload (`filepath`/`absolute_path`) — a result
  that surfaces a file's content costs context tokens whether or not a
  follow-up `Read` happens. `Grep`/`Glob` calls are counted separately as
  searches, not reads.
- **Tokens-to-answer** = total usage (input + cache_creation + cache_read +
  output). Also reported: **fresh tokens** (excludes cache reads — a fairer
  cross-run comparison since cache hit rates vary) and **cost/task** in USD.
  The ~500-token nexus SKILL.md body is counted as part of nexus's input —
  it's a real cost of using the tool, not hidden.
- **Retrieval tokens** (`retrieval_tokens_est`, recorded per-run but not in
  the headline table) = an estimated token count for all tool-result
  payloads seen (`len(text) // 4`), diagnostic-only — it isolates how much of
  a run's tokens went to retrieval versus everything else (reasoning,
  conversation scaffolding).
- **Correctness** = `0.5 * file_recall + 0.5 * fact_score`, pass at ≥ 0.75.
  Mechanical only (substring/regex matching against the final answer) unless
  a judge pass is run — see `scoring.judge_prompt`/`parse_judge_output` for
  an optional LLM-judge rubric (not run by default; costs one extra call per
  task).
- Aggregation is the **median across reps** per (task, condition), then a
  macro-average of per-task medians per condition, with IQR reported for
  wasted-read ratio.

**Contamination:** both target repos are large enough (django: ~2,900 Python
files; home-assistant/core: ~26,000) that no single conversation could read
the whole thing, but the model may have memorized parts of django from
training. Mitigations: home-assistant/core is pinned to a commit dated at
model training-cutoff or later (check the `pinned_date` field in its task
YAML against the training cutoff of the model that `--model` resolved to — or whatever
`--model` you actually ran with — training cutoff before publishing);
wasted-read ratio is contamination-resistant by construction since it
measures *reads*, not recalled knowledge; needle tasks ask for exact line
numbers, which models don't reliably memorize even for famous code.

## File layout

- `tasks/*.yaml` — task suites (schema above)
- `mcp-configs/nexus.json` — MCP server config for the `nexus` condition;
  its `env` is intentionally empty so the server inherits nexus-mcp's default
  embedding model. Assumes the target repo was already pre-indexed by
  `setup_repos.sh` — pointing this at an un-indexed repo means the first
  nexus tool call has to index from scratch inside the measured run.
- `conditions.py` — pure argv/env builders (no subprocess calls)
- `transcript.py` — pure stream-json event parser -> `RunTrace`
- `scoring.py` — pure per-run metrics against ground truth
- `runner.py` — orchestrates real `claude` subprocess runs, writes JSONL
- `report.py` — aggregates JSONL into markdown + CSV
- `setup_repos.sh` / `_preindex_one.py` — one-time repo clone + pre-index
- `preindex_models.py` — one index per embedding model (`<repo>/.nexus-<model>`)
- `nexus_server.py` — starts the server with a candidate model that is not in the registry
- `repos/`, `results/`, `.claude-bench/` — gitignored, generated locally

## Known gotchas

- The CLI's stream-json event schema is not a versioned public contract —
  `transcript.py` is defensive (unknown event types are ignored, malformed
  lines are counted in `parse_errors` rather than raising) but a CLI update
  could still shift field names. Raw stdout is not persisted by default;
  keep it if you need to debug a parsing gap on a specific run.
- No `--max-turns` flag exists in this CLI version — runaway sessions are
  bounded by `--max-budget-usd` plus the runner's own subprocess timeout
  (`timeout_s` per task, default 600s).
- Every published number should link back to the raw `results/runs-*.jsonl`
  it came from, plus the exact `claude --version` and model ID used for that
  run — both are recorded in every JSONL record.

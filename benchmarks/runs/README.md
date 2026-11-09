# Benchmark run logs

Raw logs of each language benchmark run. Use them to check a result or to give evidence in
an issue. They are not published numbers: the README, the site and `llms.txt` take their
numbers only from `benchmarks/published/results.json`.

| Folder | Run |
|---|---|
| `languages-before` | Nexus-MCP 2.2.2 (`0430f01`): every tier, paid live A/B on 8 corpora |
| `languages-after` | The parser fixes (`613a650`): free tiers only |
| `languages-final` | The token cuts (`11cfccc`): free tiers, and a live A/B stopped after 5 of 8 corpora |
| `live-opt2` | Shorter tool text (`e5a9d9a`): the Nexus side of the live tier on 4 corpora |
| `languages-v2.3.0` | The 2.4.0 code before it was renumbered (`0de5088`): free tiers for Python, TypeScript and JavaScript; stopped before the live tier |
| `languages-v2.4.0` | 2.4.0: every tier for Python, TypeScript and JavaScript (Flask free tiers, Django live, Vite, webpack). The laptop slept during some webpack runs, so their `duration_ms` is long; their tokens are not affected |
| `languages-py-fix` | `Class.method` and overrides in `graph` (`53594ad`, issue #31): free tiers for Python, TypeScript and JavaScript, and the Django live A/B |
| `languages-refs-fix` | References ranked by the class of a method (`f1e9d56`): the Django live A/B. The console log also holds a Home Assistant index that was stopped before the live tier |
| `languages-defs-fix` | `defined_in` and the shorter fixed text (`45aec40`): the Django and Home Assistant live A/B. The first Home Assistant live tier stopped on a repo path bug; the console log holds the rerun on the same index |
| `languages-graph-cap` | The size cap of `graph` results (`cf1bb7a`): the Django and Home Assistant live A/B, on copies of the defs-fix indexes |
| `languages-v2.4.1` | 2.4.1: the full-path rule in the server instructions, built from `195090c` (the 2.4.1 code before the version bump). The Home Assistant and Django live A/B, on copies of the defs-fix indexes. The records say `nexus_version` 2.4.1, with a note, and keep `nexus_ref` 195090c |
| `competitors-2026-11-09` | Nexus-MCP against GitNexus 1.6.12, codebase-memory-mcp 0.12.1, Serena 2.1.0 and Graphify 0.9.104, with a Grep and Read row in the same session. Django (12 tasks, Nexus-MCP 2.4.6) and Home Assistant (6 tasks, Nexus-MCP 2.4.1 on the `languages-v2.4.1` index), 3 runs each. The tool rows use the `ext:<name>` condition. `*-index-times.txt` holds the index build time of each tool (the first Serena build on Django stopped on a prompt and was run again) |
| `setup` | One file per pinned install: version, commit and install time |

Each folder has:

- `<corpus>-live.jsonl`: one record per task, condition and rep (tokens, cost, turns, tool
  calls, the answer and its score).
- `<corpus>-quick.json`, `-retrieval.json`, `-graph.json`: the free tiers.
- `<run>.json`: one summary row per corpus.
- `console.log`: the console output of the run.

Add a run with `python scripts/archive_runs.py --log <run>=<console log>`. The script refuses
a file that holds a credential-like string.

---
paths:
  - "benchmarks/**"
  - "evals/**"
  - "src/nexus_mcp/evaluate/**"
  - "site/**"
  - "README.md"
  - "llms.txt"
---

# Benchmarks and published numbers

- Do not edit a block between `<!-- results:start -->` and `<!-- results:end -->` by hand.
  Edit `benchmarks/published/results.json`, then run `python scripts/publish_results.py`.
- After a change to the `eval` or `data` CLI, run `python scripts/gen_cli_docs.py` (Python 3.12).
- After a change to a `NEXUS_*` variable, run `python scripts/gen_config_docs.py`.
- Before a PR, run `python scripts/check_docs.py`.
- Record the before and after numbers of a change in `CHANGELOG.md` (the north-star gate).
- When you add a page under `docs/`, link it from `docs/README.md`.
- Never tune the `--example` project or the question generator until Nexus-MCP wins. Report a
  "Weak fit" as it is.

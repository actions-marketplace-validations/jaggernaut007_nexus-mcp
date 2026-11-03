# ADR-020: Adopter Evaluation (`nexus-mcp-ci eval`)

## Status: Accepted
## Date: 2026-11-03

## Context

Every surface (README, site, llms.txt) started with `pip install`. The only way to test
Nexus-MCP first was `benchmarks/`: a checkout, hand-written YAML ground truth, a Claude
token, $20–50 and hours. Almost nobody ran it. The project needs users, and a person who has
not decided needs proof on their own code, not on our projects.

## Decision

Ship the evaluation in the package, as a subcommand of the server entry point.

- `nexus-mcp-ci eval [PATH|URL] [--example]` is the quick tier: free, offline, no LLM.
  - Questions come from the user's own symbols: `concept` (docstring sentence, Python),
    `described` (name words and parent), `lookup` (exact name).
  - The right answer is the defining file, from the parser. It never comes from the
    Nexus-MCP ranking, so the check cannot grade itself.
  - Grep and Read: files ranked by the count of distinct query words that match, then read in
    rank order until the right file (at most 3, each capped at 2,000 lines).
  - Nexus-MCP: the real `search` response, plus file reads when the right file is not in the
    top 3.
  - Tokens are characters divided by 4. The report says the numbers are a proxy.
- `--example` is the installed Nexus-MCP source. An 18-file toy project and a synthetic
  119-file project both gave "Weak fit": tiny, well-named code suits Grep. Shipping a demo
  tuned until Nexus-MCP wins would be dishonest; the real source ships in every install and
  needs no network.
- `--agent runner[:model]` is the paid tier: Claude Code or Codex (experimental), with
  `baseline`, `mcp` and `nexus` conditions on one shared index, a cost estimate and a y/N,
  and a hard stop at `--max-usd`.
- One argument accepts a local path or a public `https://` URL. A local path keeps private
  code on the machine; a URL gives a result other people can repeat (the run records the SHA).
- With no arguments, `nexus-mcp-ci` still starts the stdio server. MCP clients see no change.
- Every report ends with a "Data in this run" block. `nexus-mcp-ci data` lists and removes
  every stored file.

## Consequences

- The call to action on every surface is `uvx nexus-mcp-ci eval`, before install.
- The quick tier is a proxy. A good verdict suggests, and the agent tier confirms.
- A "Weak fit" is shown plainly. We accept losing some adopters for an honest tool.
- A test blocks every socket and runs the quick tier, so the offline claim stays true.

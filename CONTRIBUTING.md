# Contributing

Thank you for your help. The fastest useful contribution is a result: run
`uvx nexus-mcp-ci eval` on your project and share it with `nexus-mcp-ci eval share <id>`.

## Set up

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP && cd Nexus-MCP
uv sync --extra dev
uv run pytest -m "not slow"
uv run ruff check .
```

More detail: [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md).

## The north-star gate

The project aims at lower token use for an agent, with the same accuracy.

- Measure: the tokens and the cost of a task in the live benchmark (`benchmarks/`), against the
  Grep and Read condition.
- Gate: hit@1 on the four retrieval suites (`python -m evals.retrieval.run`) must not fall, and
  the benchmark answer score must not fall.
- Record the before and after numbers in `CHANGELOG.md`.

## Published numbers

Do not edit a block between `<!-- results:start -->` and `<!-- results:end -->` by hand. Edit
`benchmarks/published/results.json`, then run `python scripts/publish_results.py`. CI runs it
with `--check`. `docs/CLI.md` is generated too: run `python scripts/gen_cli_docs.py`.

## Pull requests

- Work on a branch, never on `main`.
- Use semantic commit messages (`feat:`, `fix:`, `docs:`).
- Add a test for each public function, and a regression test for each fix.
- Keep each test under 5 seconds.
- Write docs in short, plain sentences.

## Security

Report a vulnerability privately: see [SECURITY.md](SECURITY.md).

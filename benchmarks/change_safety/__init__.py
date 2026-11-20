"""Change-safety benchmark: an agent applies an API change, and a script scores what it missed.

See `docs/adr/ADR-028-change-safety-benchmark.md`. `score.py` is pure and offline;
`run.py` makes live Claude calls and costs money.
"""

---
name: nexus-mcp
description: Answer questions about how a codebase works with the nexus-mcp tools instead of reading files one by one. Use when asked where something is, how it works, who calls a function, what breaks if it changes, or how the project is laid out. No setup call is needed.
---

# Using nexus-mcp

The server instructions and the tool descriptions say which tool answers which question.
Three more points:

- Put all the names you need in one `find_symbol` call, not one call or read per name.
- A `warning` field means a background reindex runs, so a result can be a little old.
- For a project of a few thousand lines, or files outside the indexed path, read the
  files directly.

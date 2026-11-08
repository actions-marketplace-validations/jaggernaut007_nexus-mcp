# Install Nexus-MCP (instructions for an AI agent)

Nexus-MCP is a local code-intelligence MCP server. It gives you search, symbol lookup and a
call graph. The server name in the client config is `nexus-mcp`. The PyPI package is
`nexus-mcp-ci`. Install it for the client that you run in. Follow these steps.

1. Check whether the tools already work. If you can call a `search` tool of the `nexus-mcp`
   server, the server runs: skip to step 6. A running server that is not in the client config
   comes from another config (for example a project file or a plugin). Tell the user, and do
   not add a second entry.
2. Check that `uv` exists. Run `uv --version`.
   - If it is missing, tell the user to install it: https://docs.astral.sh/uv/getting-started/installation/
   - Do not run a remote install script without the user's yes.
3. Check whether the client config already has a server named `nexus-mcp`. If it has one, ask
   the user before you continue: step 4 replaces it in the JSON files of the table below.
4. Run this command. Replace `<client>` with your own client:
   `claude-code`, `claude-desktop`, `cursor`, `vscode`, `windsurf`, `gemini`, `codex` or `cline`.
   ```
   uvx --python 3.12 nexus-mcp-ci setup --client <client> --yes
   ```
   - Keep `--python 3.12`. uvx otherwise uses the default Python, and the package does not
     install on Python 3.13 or newer. uv downloads Python 3.12 if it is missing.
   - Most server entries are user-level: they apply to every project (see the table). Run the
     command in the project folder anyway. Some clients get a project file, and the command
     starts an index of that folder in the background.
   - The entry runs `uvx --python 3.12 nexus-mcp-ci==<version>`, with the version that ran
     `setup`. The pin keeps the server the same until you run `setup` again to update it.
   - To see the changes first, add `--dry-run`. To see the version, run
     `uvx --python 3.12 nexus-mcp-ci --version`.
5. Tell the user to reload the client, or to start a new session. New tools appear only after that.
6. In the new session, the tools are `index`, `search`, `find_symbol`, `graph` and `map`.
   - Some clients show them with a prefix, for example `nexus__index` in Cline or
     `mcp__nexus-mcp__index` in Claude Code.
   - To check the install, call `search` with a short query, such as the name of a function in
     the project. If it answers "No codebase indexed", call `index` once with the absolute
     project path, then call `search` again.
   - The server shows `status`, `analyze` and `memory` only when the environment variable
     `NEXUS_EXTRA_TOOLS=all` is set.

## Where the index is

The index is in `<project>/.nexus/` (add `.nexus/` to `.gitignore`). When the server starts in
the project folder, a new session attaches the index by itself. Some clients, such as Cline,
start the server in another folder. Then call `index` once in each session: with an existing
index this is a fast update. To keep the index in another place, set the environment variable
`NEXUS_STORAGE_DIR` to an absolute path in the server entry.

## What the command changes

Before it changes a JSON file, `setup` keeps one copy of it as `<file>.nexus-bak`.

| Client | Server entry | Skill text |
|---|---|---|
| `claude-code` | user-level: `claude mcp add --scope user` (or `~/.claude.json`). Keeps an existing entry. | `~/.claude/skills/nexus-mcp/SKILL.md` |
| `claude-desktop` | user-level: `claude_desktop_config.json` in the Claude app folder | none |
| `cursor` | user-level: `~/.cursor/mcp.json` | `<project>/.cursor/rules/nexus-mcp.mdc` |
| `vscode` | project: `<project>/.vscode/mcp.json` | none |
| `windsurf` | user-level: `~/.codeium/windsurf/mcp_config.json` | none |
| `gemini` | user-level: `~/.gemini/settings.json` | a marked block in `<project>/GEMINI.md` |
| `codex` | user-level: a marked block in `~/.codex/config.toml`. Keeps an existing entry. | a marked block in `<project>/AGENTS.md` |
| `cline` | user-level: `cline_mcp_settings.json` in the VS Code folder of the Cline extension | none |

To remove it: `uvx --python 3.12 nexus-mcp-ci setup --client <client> --uninstall --yes`.

Licence: MIT.

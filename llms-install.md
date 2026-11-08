# Install Nexus-MCP (instructions for an AI agent)

Nexus-MCP is a local code-intelligence MCP server. It gives you search, symbol lookup and a
call graph. The server name in the client config is `nexus-mcp`. The PyPI package is
`nexus-mcp-ci`. Install it for the client that you run in. Follow these steps.

1. Check that `uv` exists. Run `uv --version`.
   - If it is missing, tell the user to install it: https://docs.astral.sh/uv/getting-started/installation/
   - Do not run a remote install script without the user's yes.
2. Check whether the client already has a server named `nexus-mcp`. If it has one, ask the
   user before you continue: step 3 replaces it in the JSON files of the table below.
3. Run this command in the project folder. Replace `<client>` with your own client:
   `claude-code`, `claude-desktop`, `cursor`, `vscode`, `windsurf`, `gemini`, `codex` or `cline`.
   ```
   uvx --python 3.12 nexus-mcp-ci setup --client <client> --yes
   ```
   - Keep `--python 3.12`. uvx otherwise uses the default Python, and the package does not
     install on Python 3.13 or newer. uv downloads Python 3.12 if it is missing.
   - The command adds the server entry, and for some clients the skill text (see the table).
     It starts an index of the folder in the background.
   - To see the changes first, add `--dry-run`.
4. Tell the user to reload the client, or to start a new session. New tools appear only after that.
5. In the new session, the tools are `index`, `search`, `find_symbol`, `graph` and `map`. Some
   clients show them with the server name in front, for example `nexus-mcp:index`. The server
   shows `status`, `analyze` and `memory` only when the environment variable
   `NEXUS_EXTRA_TOOLS=all` is set. If a tool answers "No codebase indexed", call `index` once
   with the absolute project path.

## What the command changes

Before it changes a JSON file, `setup` keeps one copy of it as `<file>.nexus-bak`.

| Client | Server entry | Skill text |
|---|---|---|
| `claude-code` | `claude mcp add --scope user` (or `~/.claude.json`). Keeps an existing entry. | `~/.claude/skills/nexus-mcp/SKILL.md` |
| `claude-desktop` | `claude_desktop_config.json` in the Claude app folder | none |
| `cursor` | `~/.cursor/mcp.json` | `<project>/.cursor/rules/nexus-mcp.mdc` |
| `vscode` | `<project>/.vscode/mcp.json` | none |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` | none |
| `gemini` | `~/.gemini/settings.json` | a marked block in `<project>/GEMINI.md` |
| `codex` | a marked block in `~/.codex/config.toml`. Keeps an existing entry. | a marked block in `<project>/AGENTS.md` |
| `cline` | `cline_mcp_settings.json` in the VS Code folder of the Cline extension | none |

To remove it: `uvx --python 3.12 nexus-mcp-ci setup --client <client> --uninstall --yes`.

Licence: MIT.

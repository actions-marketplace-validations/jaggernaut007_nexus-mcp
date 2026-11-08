# Install Nexus-MCP (instructions for an AI agent)

Nexus-MCP is a local code-intelligence MCP server. It gives you search, symbol lookup and a
call graph. The server name in the client config is `nexus-mcp`. The PyPI package is
`nexus-mcp-ci`. Install it for the client that you run in. Follow these steps.

1. Check whether the tools already work. If you can call a `search` tool of the `nexus-mcp`
   server, the server runs.
   - To install it for the first time, or if the user asked only for a working server: skip to
     step 6.
   - To update it, continue with step 2. `setup` replaces the old entry (see "Update an
     existing install").
   - The tool prefix gives the server name. A server with another name (for example `nexus__search`
     comes from a server named `nexus`) is not the `nexus-mcp` entry. It comes from another
     config: a project file such as `.mcp.json`, a plugin, or the config of another client. It
     can also be a local build of the source. Tell the user which config starts it, and do not
     add a second entry without the user's yes.
2. Check that `uv` exists. Run `uv --version`.
   - If it is missing, tell the user to install it: https://docs.astral.sh/uv/getting-started/installation/
   - Do not run a remote install script without the user's yes.
3. Check whether the client config already has a server named `nexus-mcp`. If it has one, ask
   the user before you continue: step 4 replaces it. `setup` prints `replace ... (was ...)`
   with the old launch, or `keep ...` when the entry is already current.
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
   - The background index writes `<project>/.nexus/setup-index.log`. Its last line says `DONE`
     or `FAILED` when the index ends.
5. Tell the user to restart the server. New tools and a new version appear only after that.
   - Cline: open the MCP Servers panel and restart `nexus-mcp` (or turn it off and on). A new
     chat does not restart a server that runs.
   - Other clients: reload the window, or start a new session.
6. In the new session, the tools are `index`, `search`, `find_symbol`, `graph` and `map`.
   - Some clients show them with a prefix, for example `nexus__index` in Cline or
     `mcp__nexus-mcp__index` in Claude Code.
   - To check the install, call `search` with a short query, such as the name of a function in
     the project. If it answers "No codebase indexed", call `index` once with the absolute
     project path, then call `search` again.
   - The result of `index` has `version`: the version of the server that answers. Make sure that
     it is the version that you installed. When nothing changed since the last index, the result
     has `unchanged: true` and the size of the stored index.
   - The server shows `status`, `analyze` and `memory` only when the environment variable
     `NEXUS_EXTRA_TOOLS=all` is set.

## Update an existing install

1. Run the command of step 4 again. `setup` replaces the `nexus-mcp` entry with the new version.
2. Restart the server (step 5).
3. Call `index` with the absolute project path. Make sure that `version` in the result is the
   new version.

## Where the index is

The index is in `<project>/.nexus/` (add `.nexus/` to `.gitignore`). When the server starts in
the project folder, a new session attaches the index by itself. Some clients, such as Cline,
start the server in another folder. Then call `index` once in each session, with the absolute
project path: that path selects the project, and its index is in `<project>/.nexus/`. With an
existing index this is a fast update. To keep the index in another place, set the environment
variable `NEXUS_STORAGE_DIR` to an absolute path in the server entry.

## Troubleshooting

- `[Errno 30] Read-only file system: '/.nexus'`: the server that answers is older than 2.4.3.
  Find the config that starts it (step 1), update it (see "Update an existing install"), and
  restart the server. After the fix, `/.nexus` does not exist (`ls /.nexus` fails).
- The tools still show an old version after `setup`: the client did not restart the server.
  Restart it (step 5).

## What the command changes

Before its first change to a JSON file, `setup` keeps one copy of it as `<file>.nexus-bak`.
`setup` does not replace that copy later, so it holds the file as it was before the first install.

| Client | Server entry | Skill text |
|---|---|---|
| `claude-code` | user-level: `claude mcp add --scope user` (or `~/.claude.json`). Replaces an older `nexus-mcp` entry. | `~/.claude/skills/nexus-mcp/SKILL.md` |
| `claude-desktop` | user-level: `claude_desktop_config.json` in the Claude app folder | none |
| `cursor` | user-level: `~/.cursor/mcp.json` | `<project>/.cursor/rules/nexus-mcp.mdc` |
| `vscode` | project: `<project>/.vscode/mcp.json`, for the VS Code chat (Copilot). Cline does not read this file: use `cline`. | none |
| `windsurf` | user-level: `~/.codeium/windsurf/mcp_config.json` | none |
| `gemini` | user-level: `~/.gemini/settings.json` | a marked block in `<project>/GEMINI.md` |
| `codex` | user-level: a marked block in `~/.codex/config.toml`. Replaces its own block. Stops if you wrote a `[mcp_servers.nexus-mcp]` table by hand. | a marked block in `<project>/AGENTS.md` |
| `cline` | user-level: `cline_mcp_settings.json` in the VS Code folder of the Cline extension | none |

To remove it: `uvx --python 3.12 nexus-mcp-ci setup --client <client> --uninstall --yes`.

Licence: MIT.

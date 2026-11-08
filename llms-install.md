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
   - The tool prefix is the name of the server entry. A server with another name (for example
     `nexus__search` comes from a server named `nexus`) is not the `nexus-mcp` entry. It comes
     from another config: a project file such as `.mcp.json`, a plugin, or the config of another
     client. It can also be a local build of the source. Tell the user which config starts it,
     and do not add a second entry without the user's yes.
   - To find the process, run `ps -axo pid,ppid,command | grep nexus-mcp-ci`. The command shows
     the launch: `uvx ... nexus-mcp-ci==<version>`, or the path of a local build such as
     `.../.venv/bin/nexus-mcp-ci` (a local build can be older than the release). The parent
     process (`ppid`) is the client.
   - To find the config file that the client reads, look at the files that the client process
     holds open: `lsof -p <client pid> | grep -i mcp`. A client can have several config files
     (see the Cline row of the table), and it reads only one.
2. Check that `uv` exists. Run `uv --version`.
   - If it is missing, tell the user to install it: https://docs.astral.sh/uv/getting-started/installation/
   - Do not run a remote install script without the user's yes.
3. Check whether the client config already has a server named `nexus-mcp`. If it has one, ask
   the user before you continue: step 4 replaces it.
   - `setup` changes every config file of that client that it finds. For `cline` this can be
     two files (see the table), one of them in the folder of another editor. Tell the user the
     files before you run it, or run it with `--dry-run` first.
   - `setup` prints one line for each file, in this form:
     `change: <action> MCP server 'nexus-mcp' in <file> (...)`. The action is `add` (no entry),
     `replace` (another launch; the old one is shown as `was ...`), `update` (the same launch,
     other fields) or `keep` (no change). For `claude-code` with the `claude` CLI, the line
     shows the `claude mcp add` command instead, with `(replaces ...)` for an older entry.
   - Another entry with another name (for example a disabled `nexus` entry in the same file) is
     not changed. Tell the user about it: if someone turns it on, it starts that old launch.
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
   - The command takes the newest release, and the entry pins that version:
     `uvx --python 3.12 nexus-mcp-ci==<version>`. The pin keeps the server the same until you run
     `setup` again. To install one exact version, run
     `uvx --python 3.12 nexus-mcp-ci==<version> setup ...`.
   - To see the changes first, add `--dry-run`. It writes nothing.
   - To see the version, run `uvx --python 3.12 nexus-mcp-ci --version`. The output is one line,
     for example `nexus-mcp-ci 2.4.5`. A build older than 2.4.3 does not know `--version`: it
     starts a server that never ends. If the command prints nothing in 10 seconds, stop it
     (Ctrl-C, or `kill <pid>`).
   - With `--yes`, `setup` also starts an index of the folder in the background, if the folder
     looks like a project (for example it has `.git`, `pyproject.toml` or `package.json`). It
     writes `<project>/.nexus/setup-index.log`, and its last line says `DONE` or `FAILED` when
     the index ends. `--no-index` turns this off: no index starts and no log is written. Then
     call `index` yourself in step 6.
   - To update a Cline entry in the flat form in `~/.cline/data/settings`, `setup` writes it
     again in the `transport` form (see "What the command changes").
5. Tell the user to restart the server.
   - Some clients, such as Cline, start the new server by themselves when the config file
     changes. The tool list of a chat that runs does not change: new tools and a new prefix
     appear in a new chat.
   - Cline: open the MCP Servers panel and restart `nexus-mcp` (or turn it off and on). A new
     chat does not restart a server that runs.
   - Other clients: reload the window, or start a new session.
   - The install worked only if the server that answers is the one that you installed: its name
     is the name of the entry that `setup` wrote (`nexus-mcp`), and `index` shows the version of
     step 4. If the name or the version is different, the install failed, even when every
     command passed. The file that `setup` wrote is then not the file that the client reads.
6. In the new session, the tools are `index`, `search`, `find_symbol`, `graph` and `map`.
   - Some clients show them with the server name as a prefix, for example `nexus-mcp__index`
     in Cline or `mcp__nexus-mcp__index` in Claude Code.
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

The index is in `<project>/.nexus/`. `setup` does not change `.gitignore`: ask the user to add
`.nexus/` to it, and check it with `git check-ignore .nexus`. When the server starts in the
project folder, a new session attaches the index by itself. Some clients, such as Cline,
start the server in another folder. Then call `index` once in each session, with the absolute
project path: that path selects the project, and its index is in `<project>/.nexus/`. With an
existing index this is a fast update. To keep the index in another place, set the environment
variable `NEXUS_STORAGE_DIR` to an absolute path in the server entry.

An index that a newer build wrote can crash an older build that reads the same folder (see
"Troubleshooting"). Update the older build, or do not start it in that project.

## Troubleshooting

- `[Errno 30] Read-only file system: '/.nexus'`: the server that answers is older than 2.4.3.
  Run `ps -axo pid,ppid,command | grep nexus-mcp-ci` to see its launch, find the config that
  starts it (step 1), update it (see "Update an existing install"), and restart the server.
  After the fix, `/.nexus` does not exist (`ls /.nexus` fails), and `index` shows `version`.
- The tools still show an old version after `setup`: the client did not restart the server, or
  it reads another config file than the one that `setup` wrote. Restart the server (step 5).
  If the version stays old, find the file that the client holds open (step 1).
- `Index text_idx has version 2, which is not supported`, then a Rust panic and
  `Warm-up failed`: an older build read an index that a newer build wrote. Update the build
  that answers.

## What the command changes

Before its first change to a JSON file, `setup` keeps one copy of it as `<file>.nexus-bak`.
`setup` does not replace that copy later, so it holds the file as it was before the first
install. It is not a copy of the last version. To go back to an older version, run `setup` again
with that version (`uvx --python 3.12 nexus-mcp-ci==<version> setup ...`). To remove the entry,
use `--uninstall` (below).

`setup` changes only the files in this table, and the background index writes
`<project>/.nexus/`. It does not change `.gitignore` or a project MCP file such as `.mcp.json`.

| Client | Server entry | Skill text |
|---|---|---|
| `claude-code` | user-level: `claude mcp add --scope user` (or `~/.claude.json`). Replaces an older `nexus-mcp` entry. | `~/.claude/skills/nexus-mcp/SKILL.md` |
| `claude-desktop` | user-level: `claude_desktop_config.json` in the Claude app folder | none |
| `cursor` | user-level: `~/.cursor/mcp.json` | `<project>/.cursor/rules/nexus-mcp.mdc` |
| `vscode` | project: `<project>/.vscode/mcp.json`, for the VS Code chat (Copilot). Cline does not read this file: use `cline`. | none |
| `windsurf` | user-level: `~/.codeium/windsurf/mcp_config.json` | none |
| `gemini` | user-level: `~/.gemini/settings.json` | a marked block in `<project>/GEMINI.md` |
| `codex` | user-level: a marked block in `~/.codex/config.toml`. Replaces its own block. Stops if you wrote a `[mcp_servers.nexus-mcp]` table by hand. | a marked block in `<project>/AGENTS.md` |
| `cline` | user-level, in the files of the Cline installs that exist: `~/.cline/data/settings/cline_mcp_settings.json` (the Cline CLI, and the Cline that Antigravity runs) and `cline_mcp_settings.json` in the VS Code folder of the Cline extension. A client reads only one of them. | none |

`setup` writes these entries. Most clients get the first form. The Cline file in
`~/.cline/data/settings` nests the launch under `transport`. If that file has a `nexus-mcp`
entry in the first form (for example written by hand), `setup` writes it again in the second
form and prints `update` or `replace`. It keeps `disabled`, `timeout` and `autoApprove`, and
sets `disabled` to `false`:

```
"nexus-mcp": {"type": "stdio", "command": "uvx", "args": ["--python", "3.12", "nexus-mcp-ci==<version>"]}

"nexus-mcp": {"transport": {"type": "stdio", "command": "uvx", "args": ["--python", "3.12", "nexus-mcp-ci==<version>"]}, "disabled": false, "timeout": 300}
```

To remove it: `uvx --python 3.12 nexus-mcp-ci setup --client <client> --uninstall --yes`.

Licence: MIT.

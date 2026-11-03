# Security policy

## Report a vulnerability

Do not open a public issue for a vulnerability. Use GitHub's private report:
**Security → Report a vulnerability** on
[jaggernaut007/Nexus-MCP](https://github.com/jaggernaut007/Nexus-MCP/security/advisories/new).
Give the version, the steps to reproduce and the impact. You get an answer within 7 days.

## Supported versions

The latest release on PyPI gets fixes. Older versions do not.

## Threat model in short

- Nexus-MCP runs locally over stdio. It opens no port and makes no network call, except the
  one-time model download ([PRIVACY.md](docs/PRIVACY.md)).
- Tools are grouped as READ, MUTATE and WRITE. `NEXUS_PERMISSION_LEVEL=read` blocks the others
  ([ADR-012](docs/adr/ADR-012-tool-permission-model.md)).
- Input is validated at tool entry: null bytes, length limits and path traversal. Filter values
  are SQL-escaped.
- The audit log redacts secret fields. Rate limits are available for shared setups.
- The index in `.nexus/` holds code snippets. Keep it out of version control.
- `eval share` posts an allow-listed payload only after a yes. The community workflow
  validates it and never runs code from it ([ADR-021](docs/adr/ADR-021-results-publishing-and-sharing.md)).

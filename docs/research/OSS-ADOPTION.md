# Open-source adoption: research and a growth checklist

Date: 2026-11-08. Scope: what makes developers adopt an open-source developer tool, and an MCP
server in particular. This note gives the findings, the sources, and the changes made to the
README and the site as a result. The last section is a checklist for the owner.

## Findings

### 1. Successful READMEs follow one pattern

The tools that grew fast put the same four things above the fold: a one-line promise, one
captioned proof, a one-command install, and a link to the method behind any claim.

- **uv** captions its hero chart with the workload ("Installing Trio's dependencies with a warm
  cache") and links its speed claim to `BENCHMARKS.md`. Source: https://github.com/astral-sh/uv
- **Ruff** has a one-line tagline, a hero chart of one named workload and a short list of
  highlights. It does not link a method for its speed claim. Source:
  https://github.com/astral-sh/ruff
- **Context7** puts one-click "Add to" buttons above the title, frames the problem as "without
  and with", and has a one-command install and an "ask your agent" prompt. Source:
  https://github.com/upstash/context7
- **Aider** uses its usage numbers as proof (installs, tokens per week, stars) and links its
  leaderboards instead of embedding them. Source: https://github.com/Aider-AI/aider
- **Serena** is a counter-example: a video, agent quotes and capability tables, with the
  install late and no numeric benchmark. Source: https://github.com/oraios/serena
- **GitHub's guidance:** a README says what the project does, why it is useful, how to start,
  where to get help and who maintains it. Longer material goes to docs. Source:
  https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes

### 2. Benchmarks earn trust only with a method and the losses

- Vendor solve rates often leave out the model, the retry policy, the token budget and whether
  tests ran. Readers discount them. Source:
  https://dev.to/webx_2736/a-reproducible-harness-for-comparing-coding-agents-before-you-trust-their-numbers-1ko7
- Cost per correct answer is a stronger frame than tokens alone, because price tiers and
  caching make tokens and cost differ. Source: https://artificialanalysis.ai/agents/coding-agents
- Show raw and cache-adjusted tokens. Source:
  https://univeros-io.pages.dev/docs/benchmarks/tokens-to-ship
- A tool that reports savings only against its own baseline loses trust. Source (search snippet
  only): https://awesome.ecosyste.ms/projects/github.com%2Fjahala%2Fcopeca
- One number must be the same in the README, the site and the package page. Different numbers
  for one claim read as a sign of a weak method. (Inference; no source with data.)

### 3. Distribution

- **Show HN** needs a project that people can run now: no landing page in place of the
  project, no sign-up wall, no requests for votes. The author stays in the comments. Source:
  https://news.ycombinator.com/showhn.html
- **Directories:** guides advise the official MCP Registry first, then PulseMCP and Glama, with
  a PyPI or npm package. One guide says a technical tutorial does more than a listing.
  Sources: https://geekflare.com/guides/mcp-server-directories/ and
  https://agnost.ai/blog/increase-mcp-server-usage
- **Supply:** remote MCP servers grew from 425 to 1,412 between August 2025 and February 2026.
  The median server has 5 tools. The sample counts remote servers only. Source:
  https://bloomberry.com/blog/we-analyzed-1400-mcp-servers-heres-what-we-learned/
- **Expectations:** reports from first launches range from 15 stars in 10 days to a jump from
  300 to 1,400 stars after a front-page Show HN. These are anecdotes. Source:
  https://dev.to/suzuki_yuto_786e3bc445acb/what-i-did-in-the-first-10-days-after-launching-my-open-source-ai-tool-the-real-story-4hg6

### 4. Not found

No reliable data was found on the effect of badges, demo GIFs, Reddit, X or Discord on
installs, or on landing-page layout for developer tools. Treat advice on these as practice,
not evidence.

## What changed in this repo (2026-11-08)

| Finding | Change |
|---|---|
| One promise, one proof | The README and the site lead with the token story. The site hero is a trace of tokens to the right file, drawn to scale, for each of 10 languages. |
| One-command install above the fold | `uvx --python 3.12 nexus-mcp-ci setup` is the first code block of the README and the site. |
| Method link and losses | Each row names its build. C and Rust, where Nexus-MCP used more tokens, stay in the tables and are named in the text. |
| One number everywhere | The README, the site, `llms.txt` and the benchmarks page are generated from `benchmarks/published/results.json`, one row per repo, latest run only. |
| Move detail to docs | The README went from 830 to about 200 lines. The technology table moved to `docs/ARCHITECTURE.md`, the comparison to `docs/COMPARISON.md`. |
| Cost per correct answer | The live table shows cost per task next to the correct count. |

## Growth checklist for the owner

These need the owner's accounts or judgement. They are not done.

1. **Rerun C and Rust** on the current build. They are the only live losses and both runs are
   from 2.2.2. A win or an honest loss on the current build removes the "not there yet" line.
2. **Record a 20-second demo** (a GIF or a short video): one question in Claude Code, with and
   without Nexus-MCP, and the token count at the end. Put it under the README install block.
3. **Replace `site/og.png`** and the GitHub social preview. Both still carry the old memory
   tagline. Use the hero trace and "95% fewer tokens to the right file".
4. **Show HN** when the C and Rust rerun is in. Title idea: "Show HN: Nexus-MCP, a local MCP
   server that cuts coding-agent tokens (benchmarks on 12 repos)". Stay in the comments.
5. **A technical post** on how the benchmark works and where Nexus-MCP loses. Post it to the
   r/ClaudeAI and r/LocalLLaMA communities and link it from Show HN.
6. **Directories still open:** PulseMCP, mcp.so, mcpservers.org, the Docker catalog, Cline and
   Cursor (text in `docs/SUBMISSIONS.md`).
7. **Good first issues:** label 3 to 5 small issues (a language test fixture, a docs fix) so a
   new contributor has a place to start.
8. **Release notes:** keep one short "what is faster now" line with a number at the top of
   each GitHub release. People who star a repo see releases in their feed.
9. **Community results:** ask the first users to run `nexus-mcp-ci eval share`. Results from
   repos you did not choose are stronger proof than yours.

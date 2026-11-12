# Hermes Agent - Development Guide

For AI coding assistants and developers working on hermes-agent: what applies everywhere, then a
routing table. Each area's `AGENTS.md` loads when you work there — read it before editing.
`python scripts/check` caps this file at 12k chars and every root-to-area chain at 30k.

**Never give up on the right solution.**

## What Hermes Is

Hermes runs one agent core across a CLI, a messaging gateway (~20 platforms), a TUI and an
Electron desktop app. It learns across sessions (memory + skills), delegates to subagents, runs
scheduled jobs, and drives a real terminal and browser. It is extended through plugins and skills,
not by growing the core.

Two invariants shape almost every design decision and are the lens for reviewing any change:

- **Per-conversation prompt caching is sacred.** Mutating past context, swapping toolsets,
  reloading memories or rebuilding the system prompt mid-conversation breaks the cached prefix and
  multiplies the user's cost; the ONE exception is context compression. Slash commands that change
  system-prompt state defer to the next session, with an opt-in `--now` (`/skills install --now`).
- **The core is a narrow waist; capability lives at the edges.** Every model tool is sent on
  every API call, so the bar for a new *core* tool is high. New capability should arrive as a
  CLI command + skill, a service-gated tool, or a plugin — not as core surface.

## Contribution rubric

The intent layer, for contributors and for the triage sweeper (which may only close on
`implemented_on_main`, `cannot_reproduce` or `incoherent`; taste-based closes are a maintainer's
call, and when in doubt a PR stays open).

**Wanted:** real bug fixes (repro on `main`, the exact line, the whole class incl. sibling paths);
reach at the edges (adapters, providers, models, UI) wired into the setup UX; god-file → module
refactors; extending before duplicating (3+ PRs in one category → an ABC + orchestrator);
behaviour-contract tests; E2E with real imports against a temp `HERMES_HOME`; salvage by cherry-pick.

**Rejected even when well-built:** hooks with no consumer; new `HERMES_*` env vars for non-secret
config (`.env` is secrets only, behaviour goes in `config.yaml`); a new core tool when terminal +
file or a skill already does the job; `offset`/`limit` on instructional tools; "fixes" that destroy
the feature they secure; telemetry without an opt-in gate; change-detector tests; plugins that touch
core files; third-party products in the core tree.

**Before you call it a bug,** verify the claim AND the intent (`git log -p -S "<symbol>"`): the
isolation is often the design (profiles are islands on purpose), an absence can be load-bearing,
and a fix that cannot point to the line where the bug manifests has an unverified premise.

**Security:** `SECURITY.md` is the scope authority. A §3.1 finding goes private (GitHub Security
Advisories or security@nousresearch.com), never into a public issue, PR, commit or comment; §3.2
hardening is ordinary public work. Name the §2 boundary crossed, with a repro on `main`.

**Footprint ladder** (take the highest rung that solves it): extend existing code → CLI command +
skill → service-gated tool (`check_fn` answers reachability, never per-session surface:
`tools/AGENTS.md`) → plugin → MCP server in the catalog → new core tool.

## Deleted upstream trees — do not reintroduce them

This fork ships **code, not documentation**. The docs site, translated guides, contributor maps
and recorded perf reports are deleted on purpose; `scripts/upstream-deletions.txt` is the
authoritative list, `MAINTAINING.md` the sync routine.

- `website/` keeps **runtime data only**: `static/api/model-catalog.json` (the model picker),
  `static/img/catalog/*.jpg` (desktop artwork), the icon set and `static/oauth/client-metadata.json`
  (the CIMD document `tools/mcp_oauth.py` publishes). The Docusaurus site, `docs/`, `i18n/`,
  `src/`, `website/scripts/` and the CI that built them are gone.
- Also gone: `contributors/` (and its scripts), `reports/`, `.plans/`, the translated
  `README.*.md`/`CONTRIBUTING*.md`/`CODE_OF_CONDUCT.md` prose, package-level READMEs and design
  notes, and the docs-only tests (`tests/website/`, `tests/skills/test_skill_docs_contract.py`).
- Kept because something reads them: `README.md` (`pyproject.toml`'s readme), this file,
  `CLAUDE.md`, `SECURITY.md`, `MAINTAINING.md`, `FORK_NOTES.md` (the `P-NNN` registry), `LICENSE`,
  `plugin-catalog/README.md`, every `AGENTS.md`, every `SKILL.md`/`references/` under `skills/` and
  `optional-skills/`, and runtime data (`locales/`, `docker/SOUL.md`, `agent/swarm-mode-*.md`).
- **Never** bring a deleted path back — not a file, not a new file inside a deleted directory, not
  through a merge. Syncs go through `python scripts/git_merge.py`, which skips every manifest path
  and every path the fork no longer has; a plain `git merge upstream/main` re-adds them and is not
  allowed. New prose belongs in a commit message, an `AGENTS.md` line or `FORK_NOTES.md`.

## Development Environment

`source ./activate` (fish: `activate.fish`, PowerShell: `activate.ps1`) provisions the PM
environment; pick an isolated `HERMES_HOME`/`HERMES_RUNTIME_DIR` first. Tests run through
`scripts/run_tests.sh` (CI parity: unset API keys, `TZ=UTC`, per-file isolation). **`python
scripts/check`** (`just check`) runs every blocking lint check CI runs; `--install-hook pre-push`
runs it on every push. `just` lists every developer command, and lefthook calls the same recipes: a
new command is a `scripts/` file plus a recipe. `# noqa` does not waive a ratchet finding —
`# health: allow <RULE> -- <why>` does.

### 开发前预检（双仓同步 + Worktree）

不要直接在 `main` 上改：两仓各开分支 + worktree，收尾走校验 → commit → push → PR。同步检查命令、分支命名与双仓校验清单见 `MAINTAINING.md` § Before You Start。

## Project Structure

The filesystem is canonical; load-bearing entry points:

```
run_agent.py  model_tools.py  toolsets.py  cli.py  hermes_state.py  hermes_constants.py
scripts/      check, run_tests.sh, git_merge.py, code_health/, ci/
tests/        sliced 8 ways in CI
apps/desktop/ upstream's official desktop app — zero fork changes
```

User state: `~/.hermes/config.yaml`, `.env` (secrets only), `logs/` — all profile-aware via
`get_hermes_home()`.

### Facade + siblings layout

Every former god file is a facade (public entry points + the names other packages import) plus
siblings `<stem>_<topic>.py`, each owning one topic (`hermes_state.py`, `gateway/run.py`,
`tools/mcp_tool.py`, `hermes_cli/web_server.py`, `cli.py` → `hermes_cli/cli_*_mixin.py`,
`run_agent.py` → `agent/turn_*.py`).

- Find code by topic: `grep -rn "def name" <dir>/<stem>_*.py`, not by reading the facade.
- Siblings late-import the facade inside functions; never a module-level cycle.
- Patch where production reads: a sibling doing `from <facade> import name` inside the function
  makes the facade the seam; a patch on the defining module passes silently.
- Size and complexity are ratcheted per unit (`scripts/code_health/config.py`): new functions
  CC ≤ 20, ≤ 300 lines, nesting ≤ 6; files ≤ 2,000 lines; units already over may only go down.
- No re-export shims for internal moves; internal paths are not API. Moving a symbol means fixing
  its references in the same PR (`grep` every `AGENTS.md` and `skills/`).

## Rules that apply everywhere

- No defensive clutter: no wrappers or `try/except: pass` around code that cannot fail, no flags
  nobody sets, no dead code wired in without E2E proof. Comments keep the WHY, cut the WHAT.
- Never infer process identity from argv substrings; use
  `gateway.status.looks_like_gateway_command_line` / `hermes_cli.update_cmd._hermes_holder_subcommand`
  (HX003; `hermes_cli/AGENTS.md`).
- Never hardcode `~/.hermes`: `get_hermes_home()` for paths, `display_hermes_home()` for text
  (HX001); `_get_profiles_root()` is HOME-anchored on purpose.
- One process serves many profiles. Code that runs outside a turn (boot probes, eviction, tickers,
  deferred callbacks, RPC methods, thread hops, child spawns) binds the owning profile scope
  explicitly; `os.environ`, module globals and import-time values hold the launch profile's, so an
  unbound read is a silent default-profile leak. Seams: `gateway/AGENTS.md` § Profile scope
  (HX002/HX004/HX005/HX012, PS-P05/P06).
- Dependencies carry upper bounds (`>=floor,<next_major`; git URLs and Actions pinned to a SHA);
  after editing `pyproject.toml` run `hermes pm lock` and commit `uv.lock`; never mutate a Hermes
  environment with raw pip/uv. Full policy: `pm/AGENTS.md`.
- TypeScript (desktop, TUI, web): feature-owned nanostores over threaded state, thin route roots,
  colocated action modules, `interface` for props and shared shapes, table-driven dispatch;
  `src/app` routes, `src/store` atoms, `src/lib` pure helpers.
- Don't add core/plugin crossings: core never imports a specific bundled plugin, and plugins use
  only the published plugin API (`hermes_cli/data/plugin_api.json`, `ctx`). Existing crossings are
  baselined; `scripts/boundary_lint.py` fails CI on new ones (`plugins/AGENTS.md` § Boundary lint).
- Commits and PRs: rebase onto `main` before merging (a squash from a stale branch reverts newer
  fixes); 1–2 invariant tests per fix, proven red on the base.
- Tests: always `scripts/run_tests.sh`, never bare pytest; behaviour contracts, never
  change-detectors or source-reading tests; host-specific behaviour is tested on that host with
  `@pytest.mark.platforms(...)`, never by faking `sys.platform`; tests never write to `~/.hermes/`.
  Everything else: `tests/AGENTS.md`.

## Issues — fetch and reply

`python scripts/fetch_issue.py [NUMBER…]` → `.cache/issues/issue-N.md` (`--state open --limit N`
lists, `--stdout` prints). `python scripts/response_issue.py NUMBER --body-file FILE [--close]
[--dry-run]` comments, closing only with `--close`. Both drive `gh` (`gh auth login`); fetched text
stays in the gitignored `.cache/issues/`; replies go over stdin, so `--body-file` for Chinese text,
not `--body`; a closed issue is never re-closed. Flags, exit codes, tests: MAINTAINING.md.

## Routing Table — working in X → read X/AGENTS.md

| Area | Read | Covers |
|---|---|---|
| run_agent.py, agent/ | agent/AGENTS.md | turn phases, caching invariants, compression, model/aux resolution |
| cli.py, hermes_cli/ | hermes_cli/AGENTS.md | CLI mixins, slash registry, config, skins, update, profiles |
| gateway/ | gateway/AGENTS.md | adapters, message guards, streaming, token locks, § Profile scope |
| tools/, toolsets.py, model_tools.py | tools/AGENTS.md | tool registry, toolsets, delegation, surface tools |
| plugins/, hermes_cli/plugins*.py | plugins/AGENTS.md | plugin kinds, compat contract, in-tree policy, boundary lint |
| plugin-catalog/ | plugin-catalog/README.md | catalog admission rules |
| tui_gateway/, ui-tui/ | tui_gateway/AGENTS.md | process model, JSON-RPC transport, slash flow |
| web/, hermes_cli/web_routers/ | web/AGENTS.md | dashboard embeds the real TUI |
| apps/desktop/ | apps/desktop/AGENTS.md, src/AGENTS.md | serve backend, slash palette, Bot Mode |
| skills/, optional-skills/, agent/curator*.py | skills/AGENTS.md | frontmatter, authoring, curator |
| cron/, kanban | cron/AGENTS.md | scheduler invariants, job fields, dispatcher |
| tests/ | tests/AGENTS.md | runner, placement, OS markers, banned test shapes |
| pm/, pyproject.toml | pm/AGENTS.md | pinning, PM environments, plugin quarantine |
| hermes_platform/ | hermes_platform/AGENTS.md | host facts, resolvers |
| upstream sync | MAINTAINING.md, git_merge.py | fork deletions, merge state, conflicts |


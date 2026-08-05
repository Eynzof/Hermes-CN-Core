# Batch docs-1 — conflict resolution report

All 38 files from `batches/docs-1.txt` resolved, `git add`ed, no markers remaining.
Quality gates run per protocol: `python -m py_compile` on every existing `.py`, `json.load` on `model-catalog.json`, marker scan on every file.

## Per-file decisions

- AGENTS.md → merged (kept CN fork's detailed test-runner env list `env -i`/`PYTHONHASHSEED=0` + upstream's "worker count auto-scaled"; took upstream's `-k test_x` runner usage examples, matching the merged `run_tests.sh` interface).
- README.md → merged (kept CN Chinese feature table + all 7 CN rows; added upstream's 4 new capability rows translated to Chinese; CN fork/desktop/install references intact).
- optional-skills/creative/kanban-video-orchestrator/scripts/bootstrap_pipeline.py → merged (kept ours' `orjson`/`agent.re_compat` per fork usage elsewhere; adopted upstream's explicit `encoding="utf-8"` read; kept fork's "Python 3.14+" EXT_DEPS per P-048).
- optional-skills/creative/kanban-video-orchestrator/scripts/monitor.py → took upstream (quote-style only).
- optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py → merged (took upstream's `load_yaml_file` ConfigReadError raising + `parse_env_file` OSError handling, matching the common docstring/callers; kept ours' `orjson` at all load sites incl. `except (orjson.JSONDecodeError, OSError)` — file uses orjson throughout).
- optional-skills/research/osint-investigation/scripts/build_findings.py → merged (kept orjson; added `encoding="utf-8"` to read/write).
- optional-skills/research/osint-investigation/scripts/timing_analysis.py → merged (kept orjson; added `encoding="utf-8"` write).
- scripts/check-windows-footguns.py → merged (kept ours' 3 creationflags/cmd.exe Footgun rules per P-038/P-019; added upstream's 2 encoding Footgun rules — helpers `_is_likely_subprocess_call`/`_looks_like_string_literal` already in common region; took upstream single-quote style).
- scripts/ci/lockfile_diff.py → took upstream (kwargs formatting only).
- scripts/contributor_audit.py → took upstream (quote style only).
- scripts/install.ps1 → kept-ours+merged (P-019: dropped upstream's `Set-GitBashEnvVar`/`HERMES_GIT_BASH_PATH` block — no callers; kept ours' Vite-8 comment merged with upstream's Node-floor comment matching common `Test-NodeVersionOk`; Install-Git/Stage-Git retained as common).
- scripts/profile-tui.py → merged (kept orjson + upstream's `encoding="utf-8"`; upstream quote style).
- scripts/release.py → merged (LEGACY_AUTHOR_MAP = union of ours' 6 fork entries + upstream's ~69 entries, no dups; took upstream for ACP-registry hunk — upstream deleted `acp_registry/` + `tests/acp/test_registry_manifest.py` + the `ACP_REGISTRY_MANIFEST` constant and `build_release_artifacts`, so our block was dead code referencing an undefined name).
- scripts/run_tests_parallel.py → kept-ours (PYTHONDONTWRITEBYTECODE=1 env for parallel per-file pytest subprocesses — fork optimization to avoid .pyc contention).
- setup.py → kept-ours (upstream rewrote setup.py to block all wheel/sdist builds outside Nix; our fork's README documents `pip install "git+https://github.com/Eynzof/Hermes-CN-Core.git"` which requires buildable wheels, and the read-only-source build + `data_files` skills/optional-skills bundling is fork behavior — kept ours, restored imports, dropped upstream's `_GuardedSdist`/`_GuardedBdistWheel` tail, rewrote the stale docstring. NOTE: upstream's Nix build-guard is intentionally not ported).
- skills/autonomous-ai-agents/claude-code/SKILL.md → kept-ours (version 2.3.0 per P-047).
- skills/autonomous-ai-agents/codex/SKILL.md → kept-ours (version 1.1.0 + `--json`/`--full-auto`/notify_on_complete delegation pattern per P-047).
- skills/autonomous-ai-agents/hermes-agent/SKILL.md → merged (kept our detailed Hard Invariants/Windows-Specific Quirks sections per P-050; adopted upstream's 5 canonical Key-Rules bullets, replacing our old bullet set).
- skills/creative/comfyui/scripts/auto_fix_deps.py → merged (kept orjson; upstream `encoding="utf-8"` read; upstream quote style).
- skills/creative/comfyui/scripts/hardware_check.py → took upstream (quote style only; P-020 `refresh_env_from_registry` import already common).
- skills/creative/comfyui/tests/conftest.py → merged (kept orjson; upstream `encoding="utf-8"`).
- skills/productivity/google-workspace/scripts/google_api.py → merged (kept orjson; upstream `encoding="utf-8"` read/write; upstream quote style).
- skills/productivity/google-workspace/scripts/gws_bridge.py → merged (kept orjson; upstream `encoding="utf-8"` read/write).
- skills/productivity/google-workspace/scripts/setup.py → merged (7 conflicts, all orjson↔json: kept orjson everywhere + upstream's `encoding="utf-8"`).
- skills/productivity/powerpoint/scripts/add_slide.py → took upstream (upstream rewrote the powerpoint skill: new `office/helpers/pptx_*.py` + `duplicate_slide(after=...)`; common region is already upstream's new code; dropped ours' old duplicate_slide block and `agent.re_compat` import — file now uses stdlib `import re`; NOTE: `agent.re_compat` preference lost here, it defaults to stdlib re anyway).
- skills/productivity/powerpoint/scripts/clean.py → took upstream (upstream rewrote: `_slide_rids` + `office.helpers` imports needed by common code; dropped ours' `agent.re_compat` line; our `errors="replace"` read adaptation preserved in common region).
- skills/productivity/powerpoint/scripts/office/helpers/merge_runs.py → took-upstream (staged deletion — upstream replaced these with `pptx_chart.py`/`pptx_slide.py`/`pptx_theme.py`; no conflict, `D` staged).
- skills/productivity/powerpoint/scripts/office/helpers/simplify_redlines.py → took-upstream (staged deletion, no conflict).
- skills/productivity/powerpoint/scripts/office/pack.py → took-upstream (staged deletion, no conflict).
- website/docs/developer-guide/contributing.md → merged (kept Python 3.14 per P-048; Node 22+ per root package.json engines `>=22.22.0`; upstream table formatting).
- website/docs/reference/optional-skills-catalog.md → took upstream (catalog descriptions match the actual SKILL.md frontmatter in the merged tree: sherlock, page-agent).
- website/docs/reference/skills-catalog.md → took upstream (descriptions + `dogfood` moved to `software-development/dogfood`, matching actual skill location; touchdesigner/hermes-agent-skill-authoring descriptions match SKILL.md).
- website/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent.md → merged (auto-generated page; took upstream base — Version 3.1.0, "What makes Hermes different", Scope, Quick Start, Key Paths, theming — mirroring the merged SKILL.md; kept our Hard Invariants block incl. Windows-Specific Quirks per P-050/P-019, with upstream's 5 Key-Rules bullets appended).
- website/docs/user-guide/skills/bundled/software-development/software-development-hermes-agent-skill-authoring.md → took upstream (matches merged SKILL.md description; comment placement).
- website/docs/user-guide/windows-native.md → kept-ours (P-019: PowerShell-as-default-shell dep table kept; Python 3.14 per P-048; Node 22; dropped upstream's "bash.exe for terminal tool" wording).
- website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/getting-started/installation.md → kept-ours (CN pip-install prerequisite note — fork supports `pip install git+...`; upstream dropped it).
- website/i18n/zh-Hans/.../autonomous-ai-agents-hermes-agent.md → kept-ours (2 conflicts: Python 3.14 + pytest-xdist test instructions per P-048/fork policy, matching the EN page's kept Windows testing section).
- website/static/api/model-catalog.json → took upstream (only `updated_at` timestamp differed; newer date).

## Notes for the test phase
- `setup.py`: upstream's Nix-only build guard intentionally NOT adopted — fork's `pip install git+...` and data_files bundling require buildable wheels. If the fork ever adopts upstream's distribution policy, revisit.
- `scripts/release.py`: ACP registry version bumping removed (upstream deleted the `acp_registry/` assets + manifest test); `build_release_artifacts` also gone upstream — if the fork needs sdist/wheel building for GitHub releases, re-add.
- Powerpoint skill: ours' `agent.re_compat` import dropped in `add_slide.py`/`clean.py` (upstream rewrite uses stdlib `re`; re_compat defaults to stdlib re so no behavior change).
- `scripts/install.ps1`: confirmed no remaining `HERMES_GIT_BASH_PATH`/`Set-GitBashEnvVar` references (P-019) and no callers.

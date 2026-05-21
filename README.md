# hermes-agent-cn

English · [简体中文](./README.zh-CN.md)

[![Tests](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/tests.yml/badge.svg)](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/tests.yml)
[![Runtime Release](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/release-runtime.yml/badge.svg)](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/release-runtime.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

`hermes-agent-cn` is a Chinese community fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). It tracks upstream while carrying a small, documented patch set for Chinese provider metadata, the Hermes desktop runtime, and Dashboard APIs used by [hermes-cn-desktop-v2](https://github.com/Eynzof/hermes-cn-desktop-v2).

This repository is not a clean-room reimplementation of Hermes Agent. It is a long-lived downstream fork that keeps upstream attribution, upstream licensing, and a clear maintenance path back to `NousResearch/hermes-agent`.

## What is different from upstream?

The fork-specific changes are documented in [FORK_NOTES.md](./FORK_NOTES.md). In short, this fork adds or maintains:

- **Chinese provider metadata** for Dashboard environment configuration, including ARK, Qianfan, Hunyuan, SiliconFlow, ModelScope, AI302, CompShare, and LongCat.
- **Dashboard endpoints used by the desktop client**, such as attachment uploads, workspace directory listing, MCP server summaries, and active profile read/write APIs.
- **SSE + POST gateway transport** for browser and desktop shells that prefer EventSource plus HTTP JSON-RPC over WebSocket-only transport.
- **Runtime release packaging** that builds signed PyInstaller artifacts consumed by `hermes-cn-desktop-v2`.
- **Fork maintenance automation** for upstream tracking, runtime release signing, lockfile checks, and supply-chain scanning.

If you want the official upstream project, use [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). If you want the Chinese community fork or the desktop runtime consumed by the CN desktop app, use this repository.

## Relationship to the desktop app

`hermes-agent-cn` is the runtime and Dashboard backend used by [Hermes Agent CN Desktop](https://github.com/Eynzof/hermes-cn-desktop-v2). The desktop app downloads signed runtime artifacts from this repository's GitHub Releases and runs the Dashboard locally.

## Desktop app preview

These screenshots show the CN desktop shell backed by this runtime, including the workspace, light and dark themes, built-in skills, model configuration, memory, and chat/review flows.

| Workspace light theme | Workspace dark theme |
| --- | --- |
| ![Hermes Agent CN Desktop workspace light theme](./assets/screenshots/cn-desktop-workbench-light.png) | ![Hermes Agent CN Desktop workspace dark theme](./assets/screenshots/cn-desktop-workbench-dark.png) |

| Built-in skills | Model configuration |
| --- | --- |
| ![Hermes Agent CN Desktop built-in skills](./assets/screenshots/cn-desktop-skills.png) | ![Hermes Agent CN Desktop model configuration](./assets/screenshots/cn-desktop-models.png) |

| Memory management | Chat response |
| --- | --- |
| ![Hermes Agent CN Desktop memory management](./assets/screenshots/cn-desktop-memory.png) | ![Hermes Agent CN Desktop chat response](./assets/screenshots/cn-desktop-chat.png) |

![Hermes Agent CN Desktop code review conversation](./assets/screenshots/cn-desktop-review.png)

Current runtime release:

- [`runtime-v0.14.0-cn.1`](https://github.com/Eynzof/hermes-agent-cn/releases/tag/runtime-v0.14.0-cn.1)

Runtime tags follow this pattern:

```text
runtime-v<upstream-version>-cn.<revision>
```

For example, `runtime-v0.14.0-cn.1` means the first CN runtime revision based on the Hermes Agent `0.14.0` line.

## Installation

The fork exposes the same `hermes` CLI entry point as upstream. Do not install upstream `hermes-agent` and this fork into the same Python environment.

Install from GitHub:

```bash
pip install "git+https://github.com/Eynzof/hermes-agent-cn.git"
```

For native Windows testing, the repository also includes the PowerShell installer at [`scripts/install.ps1`](./scripts/install.ps1):

```powershell
iex (irm https://raw.githubusercontent.com/Eynzof/hermes-agent-cn/main/scripts/install.ps1)
```

Then start Hermes:

```bash
hermes
```

For desktop users, the recommended path is to install the desktop client from [hermes-cn-desktop-v2 releases](https://github.com/Eynzof/hermes-cn-desktop-v2/releases). The desktop app manages the runtime for you.

## Quick start

```bash
hermes              # Start the interactive CLI
hermes model        # Select an LLM provider and model
hermes tools        # Configure enabled tools
hermes config set   # Set individual config values
hermes gateway      # Start the messaging gateway
hermes setup        # Run the setup wizard
hermes update       # Update Hermes
hermes doctor       # Diagnose common issues
```

Upstream user documentation is available at [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/). Fork-specific notes are kept in this repository.

## Development setup

Clone the fork and install it in editable mode:

```bash
git clone git@github.com:Eynzof/hermes-agent-cn.git
cd hermes-agent-cn

python -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

If you use `uv`, this is also supported:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
```

Run the main test suite:

```bash
python -m pytest tests/ -q --ignore=tests/integration --ignore=tests/e2e --tb=short -n auto
```

Run the Dashboard smoke check used by the fork maintainers:

```bash
hermes dashboard --no-open
```

Then verify the fork-only Dashboard APIs listed in [MAINTAINING.md](./MAINTAINING.md).

## Repository layout

```text
hermes_cli/          CLI, Dashboard server, setup, profile, update, and gateway commands
agent/               Agent loop, message handling, memory, prompts, and session logic
tui_gateway/         Gateway server and transports, including the CN SSE transport
providers/           Model provider integrations and provider metadata
tools/               Tool implementations and execution backends
skills/              Bundled skills
optional-skills/     Optional skill packs
web/                 Dashboard web frontend assets
ui-tui/              Terminal UI frontend assets
website/             Documentation website inherited from upstream
docs/                Fork/runtime documentation
.github/workflows/   Tests, upstream watch, runtime release, and security workflows
```

## Branch and release model

This fork uses `main` as the stable product branch for CN runtime releases.

- `origin/main` is the stable fork branch.
- `upstream/main` tracks `NousResearch/hermes-agent` and must be treated as read-only.
- `chore/sync-*` branches are used for upstream sync pull requests.
- `cn/P-xxx-*` branches are used for fork-specific patches.
- `upstream-pr/*` branches are clean branches based on upstream for official upstream PRs.
- `runtime-v*` tags publish signed runtime artifacts for the desktop client.

See [MAINTAINING.md](./MAINTAINING.md) for the full maintenance workflow.

## Contributing

Issues and pull requests are welcome. Please keep these rules in mind:

1. Fork-specific behavioral changes should be documented in [FORK_NOTES.md](./FORK_NOTES.md).
2. Broadly useful fixes should be prepared as clean `upstream-pr/*` branches when possible.
3. Do not squash unrelated upstream syncs and CN fork patches together.
4. Do not include real API keys, user configs, local `.env` files, or private runtime signing keys.

Please read [CONTRIBUTING.md](./CONTRIBUTING.md) and [MAINTAINING.md](./MAINTAINING.md) before submitting larger changes.

## Security

Security-sensitive issues should not be reported in public issues. Please follow [SECURITY.md](./SECURITY.md).

Runtime release manifests are signed. The private signing key must only live in the repository secret `RUNTIME_SIGN_PRIVATE_KEY_PEM`; never commit it to the repository.

## License and attribution

This fork is licensed under the [MIT License](./LICENSE), inherited from upstream Hermes Agent.

Original project: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). This fork preserves upstream attribution and documents fork-specific changes separately.

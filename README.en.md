# Hermes-CN-Core

English · [简体中文](./README.md)

[![Tests](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/tests.yml/badge.svg)](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/tests.yml)
[![Runtime Release](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/release-runtime.yml/badge.svg)](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/release-runtime.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

<p>
  <a href="https://hermesagent.org.cn"><strong>Hermes Agent Chinese community site: hermesagent.org.cn</strong></a>
</p>

`Hermes-CN-Core` is the Chinese community core runtime fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). It tracks upstream while carrying a small, documented patch set for Chinese provider metadata, the Hermes desktop runtime, and Dashboard APIs used by [hermes-agent-cn-desktop](https://github.com/Eynzof/hermes-agent-cn-desktop).

This repository is not a clean-room reimplementation of Hermes Agent. It is a long-lived downstream fork that keeps upstream attribution, upstream licensing, and a clear maintenance path back to `NousResearch/hermes-agent`. The Python package still exposes the upstream-compatible `hermes` CLI entry point.

<table>
<tr><td><b>Hermes Agent Chinese community</b></td><td><a href="https://hermesagent.org.cn">hermesagent.org.cn</a> provides the Chinese community site, community entry points, desktop downloads, and localized guides.</td></tr>
<tr><td><b>Chinese-first core runtime</b></td><td>Main repository documentation is now Chinese-first, with an English README available through the language switch.</td></tr>
<tr><td><b>Desktop-ready Dashboard backend</b></td><td>Maintains the attachment, workspace, MCP summary, profile, and SSE/POST transport APIs used by the CN desktop app.</td></tr>
<tr><td><b>Local and native Windows support</b></td><td>Includes the PowerShell installer and runtime packaging needed by native Windows and the desktop distribution flow.</td></tr>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, Email, and CLI are all available through one gateway process.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation and trajectory compression remain available for tool-calling model research.</td></tr>
</table>

## Hermes Agent Chinese community

The Hermes Agent Chinese community site is [hermesagent.org.cn](https://hermesagent.org.cn). It collects Chinese tutorials, desktop downloads, provider setup notes, runtime release information, and community updates.

To discuss installation, Chinese model providers, desktop usage, plugin development, or upstream sync work, scan the QR code below to join the Hermes Agent Chinese community WeChat group.

<p align="left">
  <a href="https://hermesagent.org.cn">
    <img src="./assets/community/wechat-qr.jpg" alt="QR code for joining the Hermes Agent Chinese community WeChat group" width="240">
  </a>
</p>

## What is different from upstream?

Fork-specific changes are documented in [FORK_NOTES.md](./FORK_NOTES.md). In short, this fork adds or maintains:

- **Chinese provider metadata** for Dashboard environment configuration, including ARK, Qianfan, Hunyuan, SiliconFlow, ModelScope, AI302, CompShare, and LongCat.
- **Dashboard endpoints used by the desktop client**, such as attachment uploads, workspace directory listing, MCP server summaries, and active profile read/write APIs.
- **SSE + POST gateway transport** for browser and desktop shells that prefer EventSource plus HTTP JSON-RPC over WebSocket-only transport.
- **Runtime release packaging** that builds signed PyInstaller artifacts consumed by `hermes-agent-cn-desktop`.
- **Fork maintenance automation** for upstream tracking, runtime release signing, lockfile checks, and supply-chain scanning.

If you want the official upstream project, use [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). If you want the Chinese community core runtime or the desktop runtime consumed by the CN desktop app, use this repository.

## Relationship to the desktop app

`Hermes-CN-Core` is the runtime and Dashboard backend used by [Hermes Agent CN Desktop](https://github.com/Eynzof/hermes-agent-cn-desktop). The desktop app downloads signed runtime artifacts from this repository's GitHub Releases and runs the Dashboard locally.

Current runtime release:

- [`runtime-v0.16.0-cn.5`](https://github.com/Eynzof/Hermes-CN-Core/releases/tag/runtime-v0.16.0-cn.5)

Runtime tags follow this pattern:

```text
runtime-v<upstream-version>-cn.<revision>
```

For example, `runtime-v0.16.0-cn.5` means the fifth CN runtime revision based on the Hermes Agent `0.16.0` line.

## Installation

The fork exposes the same `hermes` CLI entry point as upstream. Do not install upstream `hermes-agent` and this fork into the same Python environment.

Install from GitHub:

```bash
pip install "git+https://github.com/Eynzof/Hermes-CN-Core.git"
```

For native Windows testing, the repository also includes the PowerShell installer at [`scripts/install.ps1`](./scripts/install.ps1):

```powershell
iex (irm https://raw.githubusercontent.com/Eynzof/Hermes-CN-Core/main/scripts/install.ps1)
```

The installer handles uv, Python 3.14, Node.js, ripgrep, ffmpeg, and a portable MinGit (for `git` VCS operations). On Windows, PowerShell is the default shell for running commands (PowerShell 7+ preferred, with automatic fallback to Windows PowerShell 5.1). Git Bash is available as an optional shell — set `terminal.shell: bash` in config.yaml if you have Git for Windows pre-installed. The installer also installs Microsoft Coreutils (providing POSIX CLI tools like `cat`, `cp`, `mv`, `ls`, `sort`, `wc`) for cross-platform script and skill compatibility.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://hermes-agent.nousresearch.com/docs/getting-started/termux). On Termux, Hermes installs a curated `.[termux]` extra because the full `.[all]` extra currently pulls Android-incompatible voice dependencies.
>
> **Windows:** Native Windows is supported by the PowerShell one-liner above. If you prefer WSL2, the Linux workflow also works there. Native Windows installs under `%LOCALAPPDATA%\hermes`; WSL2 installs under `~/.hermes` as on Linux.

After installation:

```bash
hermes
```

For desktop users, the recommended path is to install the desktop client from [hermes-agent-cn-desktop releases](https://github.com/Eynzof/hermes-agent-cn-desktop/releases). The desktop app manages the runtime for you.

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

## Skip the API-key collection — Nous Portal

Hermes works with whatever provider you want. If you would rather not collect separate API keys for the model, web search, image generation, TTS, and a cloud browser, **[Nous Portal](https://portal.nousresearch.com)** covers them under one subscription:

- **300+ models** — pick any of them with `/model <name>`.
- **Tool Gateway** — web search through Firecrawl, image generation through FAL, text-to-speech through OpenAI, and cloud browser through Browser Use.

One command from a fresh install:

```bash
hermes setup --portal
```

That logs you in via OAuth, sets Nous as your provider, and turns on the Tool Gateway. Check what is wired up any time with `hermes portal info`. Full details are available on the [Tool Gateway docs page](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway).

You can still bring your own keys per tool whenever you want. The gateway is per-backend, not all-or-nothing.

## CLI vs Messaging quick reference

Hermes has two entry points: start the terminal UI with `hermes`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you are in a conversation, many slash commands are shared across both interfaces.

| Action | CLI | Messaging platforms |
| ------ | --- | ------------------- |
| Start chatting | `hermes` | Run `hermes gateway setup` + `hermes gateway start`, then send the bot a message |
| Start a fresh conversation | `/new` or `/reset` | `/new` or `/reset` |
| Change model | `/model [provider:model]` | `/model [provider:model]` |
| Set a personality | `/personality [name]` | `/personality [name]` |
| Retry or undo the last turn | `/retry`, `/undo` | `/retry`, `/undo` |
| Compress context or check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]` |
| Browse skills | `/skills` or `/<skill-name>` | `/skills` or `/<skill-name>` |
| Interrupt current work | `Ctrl+C` or send a new message | `/stop` or send a new message |
| Platform-specific status | `/platforms` | `/status`, `/sethome` |

For the full command lists, see the [CLI guide](https://hermes-agent.nousresearch.com/docs/user-guide/cli) and the [Messaging Gateway guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging).

## Development setup

Clone the fork and install it in editable mode:

```bash
git clone git@github.com:Eynzof/Hermes-CN-Core.git
cd Hermes-CN-Core

python -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

If you use `uv`, this is also supported:

```bash
uv venv --python 3.14
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

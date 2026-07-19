# Hermes-CN-Core

简体中文（兼容入口：[README.zh-CN.md](./README.zh-CN.md)） · [English](./README.en.md)

[![Tests](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/tests.yml/badge.svg)](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/tests.yml)
[![Runtime Release](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/release-runtime.yml/badge.svg)](https://github.com/Eynzof/Hermes-CN-Core/actions/workflows/release-runtime.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

<p>
  <a href="https://hermesagent.org.cn"><strong>Hermes Agent 中文社区官网：hermesagent.org.cn</strong></a>
</p>

`Hermes-CN-Core` 是 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的中文社区核心 runtime fork。这个仓库持续跟踪上游，同时维护一组小而明确的补丁，用于中文模型服务商元数据、Hermes 桌面端 runtime，以及 [hermes-agent-cn-desktop](https://github.com/Eynzof/hermes-agent-cn-desktop) 依赖的 Dashboard API。

这个项目不是对 Hermes Agent 的重新实现，而是一个长期维护的下游 fork。我们保留上游归属、上游许可证和清晰的上游同步流程。Python 包仍然保持上游兼容的 `hermes` CLI 入口。
<table>
<tr><td><b>Hermes Agent 中文社区</b></td><td>通过 <a href="https://hermesagent.org.cn">hermesagent.org.cn</a> 提供中文官网、社区入口、桌面端下载与本地化说明。</td></tr>
<tr><td><b>中文优先的核心 runtime</b></td><td>仓库首页文档现在以中文为主，英文版通过顶部语言切换访问。</td></tr>
<tr><td><b>面向桌面端的 Dashboard 后端</b></td><td>维护 CN 桌面端需要的附件、workspace、MCP 摘要、profile 以及 SSE/POST transport API。</td></tr>
<tr><td><b>本地与原生 Windows 友好</b></td><td>包含 PowerShell 安装器和桌面分发链路需要的 runtime 打包逻辑。</td></tr>
<tr><td><b>真正可用的终端界面</b></td><td>完整 TUI，支持多行编辑、斜杠命令补全、会话历史、中断重定向和流式工具输出。</td></tr>
<tr><td><b>能出现在你常用的平台里</b></td><td>Telegram、Discord、Slack、WhatsApp、Signal、Email 和 CLI 都可以通过同一个 gateway process 接入。</td></tr>
<tr><td><b>保留研究能力</b></td><td>继续保留 batch trajectory generation 和 trajectory compression，方便工具调用模型训练与研究。</td></tr>
</table>

## Hermes Agent 中文社区

Hermes Agent 中文社区官网是 [hermesagent.org.cn](https://hermesagent.org.cn)。这里会汇总中文教程、桌面端下载、模型服务商接入说明、runtime 发布信息和社区动态。

如果你想交流安装配置、国产模型接入、桌面端使用、插件开发或上游同步，可以扫描下面二维码加入 Hermes Agent 中文社区微信群。

<p align="left">
  <a href="https://hermesagent.org.cn">
    <img src="./assets/community/wechat-qr.jpg" alt="加入 Hermes Agent 中文社区微信群二维码" width="240">
  </a>
</p>

## 和上游有什么不同？

所有 fork 专属改动都记录在 [FORK_NOTES.zh-CN.md](./FORK_NOTES.zh-CN.md)；英文版记录见 [FORK_NOTES.md](./FORK_NOTES.md)。简单说，这个 fork 主要维护：

- **中文模型服务商元数据**：让 Dashboard 环境变量面板识别 ARK、千帆、混元、SiliconFlow、ModelScope、AI302、CompShare、LongCat 等配置项。
- **桌面端依赖的 Dashboard API**：包括附件上传、workspace 目录浏览、MCP server 摘要、active profile 读写等接口。
- **SSE + POST gateway transport**：为浏览器和桌面壳提供 EventSource + HTTP JSON-RPC 传输，减少对 WebSocket-only 模式的依赖。
- **桌面 runtime 发布链路**：构建签名后的 PyInstaller runtime artifact，供 `hermes-agent-cn-desktop` 下载和验证。
- **fork 维护自动化**：包括 upstream watch、runtime release、lockfile 检查和供应链扫描。

如果你需要官方上游项目，请使用 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。如果你需要中文社区核心 runtime，或需要 Hermes Agent CN Desktop 使用的 runtime，请使用本仓库。

## 和桌面端的关系

`Hermes-CN-Core` 是 [Hermes Agent CN Desktop](https://github.com/Eynzof/hermes-agent-cn-desktop) 使用的 runtime 和 Dashboard 后端。桌面端会从本仓库的 GitHub Releases 下载签名 runtime，并在本机运行 Dashboard。

当前 runtime release：

- [`runtime-v0.16.0-cn.5`](https://github.com/Eynzof/Hermes-CN-Core/releases/tag/runtime-v0.16.0-cn.5)

runtime tag 使用下面的格式：

```text
runtime-v<上游版本>-cn.<修订号>
```

例如 `runtime-v0.16.0-cn.5` 表示基于 Hermes Agent `0.16.0` 版本线的第五个中文社区 runtime 修订版。

## 安装

这个 fork 和上游一样暴露 `hermes` CLI 入口。不要把上游 `hermes-agent` 和本 fork 安装到同一个 Python 环境里。

从 GitHub 安装：

```bash
pip install "git+https://github.com/Eynzof/Hermes-CN-Core.git"
```

如果你要测试原生 Windows 安装，本仓库也提供 PowerShell 安装脚本 [`scripts/install.ps1`](./scripts/install.ps1)：

```powershell
iex (irm https://raw.githubusercontent.com/Eynzof/Hermes-CN-Core/main/scripts/install.ps1)
```

安装器会处理 uv、Python 3.14、Node.js、ripgrep、ffmpeg，以及仓库克隆、虚拟环境和 `hermes` 命令的配置。在 Windows 上，默认 shell 是 **PowerShell**（优先使用 pwsh 7.x，自动回退到 Windows PowerShell 5.1）。如果你已安装 Git for Windows，可在 config.yaml 中设置 `terminal.shell: bash` 使用 Git Bash 作为可选 shell。安装器还会安装 Microsoft Coreutils（提供 `cat`、`cp`、`mv`、`ls` 等 POSIX 命令行工具），确保跨平台脚本和技能能够正常工作。Git 仍用于仓库操作：如果系统里已经安装 Git，安装器会直接使用现有 Git；否则会把隔离的 PortableGit 下载到 `%LOCALAPPDATA%\hermes\git`，不需要管理员权限，也不会污染系统 Git。

> **Android / Termux：** 已验证的手动安装路径见 [Termux guide](https://hermes-agent.nousresearch.com/docs/getting-started/termux)。在 Termux 上，Hermes 会安装裁剪后的 `.[termux]` extra，因为完整的 `.[all]` extra 目前会拉取 Android 不兼容的语音依赖。
>
> **Windows：** 原生 Windows 可以直接使用上面的 PowerShell 一行命令。如果你更偏好 WSL2，也可以在 WSL2 中使用 Linux 流程。原生 Windows 安装目录是 `%LOCALAPPDATA%\hermes`，WSL2 与 Linux 一样使用 `~/.hermes`。
安装完成后启动：

```bash
hermes
```

如果你是桌面端用户，更推荐直接从 [hermes-agent-cn-desktop Releases](https://github.com/Eynzof/hermes-agent-cn-desktop/releases) 安装桌面客户端，由桌面端自动管理 runtime。

## 快速开始

```bash
hermes              # 启动交互式 CLI
hermes model        # 选择 LLM 服务商和模型
hermes tools        # 配置启用的工具
hermes config set   # 设置单个配置项
hermes gateway      # 启动消息网关
hermes setup        # 运行完整设置向导
hermes update       # 更新 Hermes
hermes doctor       # 诊断常见问题
```

上游用户文档位于 [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)。fork 专属说明保存在本仓库中。

## 省去到处收集 API Key — Nous Portal

Hermes 始终允许你使用任意服务商。如果你不想为模型、网页搜索、图像生成、TTS、云浏览器分别申请 API Key，**[Nous Portal](https://portal.nousresearch.com)** 可以用一个订阅覆盖这些能力：

- **300+ 模型**：用 `/model <name>` 随时切换。
- **Tool Gateway**：网页搜索通过 Firecrawl，图像生成通过 FAL，文本转语音通过 OpenAI，云浏览器通过 Browser Use。

全新安装时一条命令即可：

```bash
hermes setup --portal
```

它会通过 OAuth 登录、把 Nous 设为推理服务商，并启用 Tool Gateway。随时用 `hermes portal info` 查看路由状态。完整说明见 [Tool Gateway 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway)。

你仍然可以按工具单独切回自己的 API Key。Gateway 是按 backend 粒度生效的，不是一刀切。

## CLI 与消息平台快速对照

Hermes 有两种入口：用 `hermes` 启动终端 UI，或运行 gateway 从 Telegram、Discord、Slack、WhatsApp、Signal 或 Email 与之对话。进入对话后，许多斜杠命令在两种界面中通用。

| 操作 | CLI | 消息平台 |
| ---- | --- | -------- |
| 开始对话 | `hermes` | 运行 `hermes gateway setup` + `hermes gateway start`，然后给机器人发消息 |
| 开始新对话 | `/new` 或 `/reset` | `/new` 或 `/reset` |
| 更换模型 | `/model [provider:model]` | `/model [provider:model]` |
| 设置人格 | `/personality [name]` | `/personality [name]` |
| 重试或撤销上一轮 | `/retry`、`/undo` | `/retry`、`/undo` |
| 压缩上下文或查看用量 | `/compress`、`/usage`、`/insights [--days N]` | `/compress`、`/usage`、`/insights [days]` |
| 浏览技能 | `/skills` 或 `/<skill-name>` | `/skills` 或 `/<skill-name>` |
| 中断当前工作 | `Ctrl+C` 或发送新消息 | `/stop` 或发送新消息 |
| 平台特定状态 | `/platforms` | `/status`、`/sethome` |

完整命令列表请参阅 [CLI 指南](https://hermes-agent.nousresearch.com/docs/user-guide/cli) 和 [消息网关指南](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)。

## 开发环境

克隆仓库并以 editable 模式安装：

```bash
git clone git@github.com:Eynzof/Hermes-CN-Core.git
cd Hermes-CN-Core

python -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

如果你使用 `uv`，也可以这样安装：

```bash
uv venv --python 3.14source .venv/bin/activate
uv pip install -e ".[all,dev]"
```

运行主要测试：

```bash
python -m pytest tests/ -q --ignore=tests/integration --ignore=tests/e2e --tb=short -n auto
```

运行 fork 维护者常用的 Dashboard 冒烟检查：

```bash
hermes dashboard --no-open
```

随后按 [MAINTAINING.zh-CN.md](./MAINTAINING.zh-CN.md) 中的说明验证 fork 专属 Dashboard API。

## 仓库结构

```text
hermes_cli/          CLI、Dashboard server、setup、profile、update 和 gateway 命令
agent/               Agent loop、消息处理、memory、prompt 和 session 逻辑
tui_gateway/         Gateway server 和 transports，包括 CN SSE transport
providers/           模型服务商集成和服务商元数据
tools/               工具实现和执行后端
skills/              内置 skills
optional-skills/     可选 skill packs
web/                 Dashboard Web 前端资源
ui-tui/              终端 UI 前端资源
website/             继承自上游的文档站点
docs/                fork/runtime 文档
.github/workflows/   测试、upstream watch、runtime release 和安全扫描流水线
```

## 分支和发布模型

本 fork 使用 `main` 作为中文社区 runtime release 的稳定产品分支。

- `origin/main` 是稳定 fork 分支。
- `upstream/main` 跟踪 `NousResearch/hermes-agent`，只读，不要推送。
- `chore/sync-*` 用于 upstream 同步 PR。
- `cn/P-xxx-*` 用于 fork 专属补丁。
- `upstream-pr/*` 用于基于上游 `main` 准备官方 upstream PR。
- `runtime-v*` tag 用于发布桌面端使用的签名 runtime artifact。

完整维护流程见 [MAINTAINING.zh-CN.md](./MAINTAINING.zh-CN.md)。

## 贡献

欢迎提交 Issue 和 Pull Request。请注意：

1. fork 专属行为变更需要写入 [FORK_NOTES.zh-CN.md](./FORK_NOTES.zh-CN.md)。
2. 适合贡献给上游的通用修复，尽量整理成干净的 `upstream-pr/*` 分支。
3. 不要把无关的 upstream 同步和 CN fork 补丁 squash 到一个提交里。
4. 不要提交真实 API key、用户配置、本地 `.env` 文件或 runtime 签名私钥。

较大变更请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md) 和 [MAINTAINING.zh-CN.md](./MAINTAINING.zh-CN.md)。

## 安全

请不要在公开 Issue 中披露安全问题。安全报告请遵循 [SECURITY.md](./SECURITY.md)。

runtime release manifest 会进行签名。私钥只能保存在仓库 secret `RUNTIME_SIGN_PRIVATE_KEY_PEM` 中，绝不能提交到仓库。

## 许可证与归属

本 fork 使用继承自上游 Hermes Agent 的 [MIT License](./LICENSE)。

原始项目：[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。本 fork 保留上游归属，并将 fork 专属改动单独记录。

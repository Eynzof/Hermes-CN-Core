# hermes-agent-cn

[English](./README.md) · 简体中文

[![Tests](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/tests.yml/badge.svg)](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/tests.yml)
[![Runtime Release](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/release-runtime.yml/badge.svg)](https://github.com/Eynzof/hermes-agent-cn/actions/workflows/release-runtime.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

`hermes-agent-cn` 是 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的中文社区 fork。这个仓库持续跟踪上游，同时维护一组小而明确的补丁，用于中文模型服务商元数据、Hermes 桌面端 runtime，以及 [hermes-cn-desktop-v2](https://github.com/Eynzof/hermes-cn-desktop-v2) 依赖的 Dashboard API。

这个项目不是对 Hermes Agent 的重新实现，而是一个长期维护的下游 fork。我们保留上游归属、上游许可证和清晰的上游同步流程。

## 和上游有什么不同？

所有 fork 专属改动都记录在 [FORK_NOTES.zh-CN.md](./FORK_NOTES.zh-CN.md)。简单说，这个 fork 主要维护：

- **中文模型服务商元数据**：让 Dashboard 环境变量面板识别 ARK、千帆、混元、SiliconFlow、ModelScope、AI302、CompShare、LongCat 等配置项。
- **桌面端依赖的 Dashboard API**：包括附件上传、workspace 目录浏览、MCP server 摘要、active profile 读写等接口。
- **SSE + POST gateway transport**：为浏览器和桌面壳提供 EventSource + HTTP JSON-RPC 传输，减少对 WebSocket-only 模式的依赖。
- **桌面 runtime 发布链路**：构建签名后的 PyInstaller runtime artifact，供 `hermes-cn-desktop-v2` 下载和验证。
- **fork 维护自动化**：包括 upstream watch、runtime release、lockfile 检查和供应链扫描。

如果你需要官方上游项目，请使用 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。如果你需要中文社区 fork，或需要 Hermes Agent CN Desktop 使用的 runtime，请使用本仓库。

## 和桌面端的关系

`hermes-agent-cn` 是 [Hermes Agent CN Desktop](https://github.com/Eynzof/hermes-cn-desktop-v2) 使用的 runtime 和 Dashboard 后端。桌面端会从本仓库的 GitHub Releases 下载签名 runtime，并在本机运行 Dashboard。

当前 runtime release：

- [`runtime-v0.14.0-cn.1`](https://github.com/Eynzof/hermes-agent-cn/releases/tag/runtime-v0.14.0-cn.1)

runtime tag 使用下面的格式：

```text
runtime-v<上游版本>-cn.<修订号>
```

例如 `runtime-v0.14.0-cn.1` 表示基于 Hermes Agent `0.14.0` 版本线的第一个中文社区 runtime 修订版。

## 安装

这个 fork 和上游一样暴露 `hermes` CLI 入口。不要把上游 `hermes-agent` 和本 fork 安装到同一个 Python 环境里。

从 GitHub 安装：

```bash
pip install "git+https://github.com/Eynzof/hermes-agent-cn.git"
```

然后启动 Hermes：

```bash
hermes
```

如果你是桌面端用户，更推荐直接从 [hermes-cn-desktop-v2 Releases](https://github.com/Eynzof/hermes-cn-desktop-v2/releases) 安装桌面客户端，由桌面端自动管理 runtime。

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

## 开发环境

克隆仓库并以 editable 模式安装：

```bash
git clone git@github.com:Eynzof/hermes-agent-cn.git
cd hermes-agent-cn

python -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

如果你使用 `uv`，也可以这样安装：

```bash
uv venv --python 3.11
source .venv/bin/activate
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

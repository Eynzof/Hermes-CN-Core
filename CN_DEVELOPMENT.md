# Hermes CN 双仓开发流程

以下流程适用于 Core 与 Desktop 的开发、集成及收尾，与根 AGENTS.md 一同遵循。

### 开发前预检（双仓同步 + Worktree 隔离）

Hermes CN 的需求与 bug 修复通常**同时横跨 Core 与 [Desktop](https://github.com/Eynzof/Hermes-CN-Desktop) 两个仓库**。正式动手写代码前，两个仓库都必须先过这道预检，**不要直接在 `main` 上改**：

1. **确认主分支已与远端同步**。对 Core 与 Desktop 分别 `git fetch origin`，确认本地 `main` 与 `origin/main` 一致（`git rev-list --left-right --count main...origin/main` 应为 `0  0`）；落后就先快进，工作区脏就先收拾干净。Core 是 fork——**永远不要把 `upstream/main` 直接并进 `main`**，上游同步走 `./scripts/sync-upstream.sh`。
2. **为每个仓库开独立的功能分支 + git worktree**，让 Core 与 Desktop 的改动互不干扰、可并行：
   ```bash
   git -C <repo> fetch origin
   git -C <repo> worktree add ../wt/<repo>-<topic> -b <branch> origin/main
   ```
   分支命名沿用各仓库既有约定：Core 的 fork 行为补丁用 `cn/P-xxx-*`（并登记进 `FORK_NOTES.md`），干净上游 PR 用 `upstream-pr/*`，文档/杂项用 `docs/` `chore/`；Desktop 沿用 Conventional 风格。注意 worktree 默认共享主 checkout 的 venv（见上一段的 venv 探测顺序），不必每个 worktree 重装依赖。
3. 不要在同一个工作目录里来回 `git checkout` 切分支——双仓并行时极易串味；每条线一个 worktree。

**收尾流程（每个仓库都要走完，缺一不可）**：改完 → 跑各自校验（Core：`scripts/run_tests.sh` 全套 + `ruff check .`；Desktop：`pnpm typecheck && pnpm test:unit && cargo check`）→ commit → push → 开 PR → **盯 PR 上 GitHub Actions 全绿**（Core：`lint.yml` + 测试切片），没过就回去修，别把任务当完成。

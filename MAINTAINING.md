# 维护 hermes-agent-cn

`hermes-agent-cn` 是 `NousResearch/hermes-agent` 的长期 downstream fork。这个 fork 的目标是让 `hermes-cn-desktop-v2` 依赖一个稳定、可签名发布、可定期同步上游的 `hermes-agent-cn` runtime，而不是依赖用户机器上全局安装的官方 `hermes`。

## 分支模型

```
origin/main       fork 的稳定产品分支
upstream/main     官方上游，只读
chore/sync-*      临时上游同步分支
cn/P-xxx-*        fork 专属补丁分支
upstream-pr/*     基于 upstream/main 的干净上游 PR 分支
runtime-v*        desktop-v2 使用的签名 runtime release tag
```

`desktop-v2` 只能消费从已验证 `origin/main` 切出的 runtime release。不要让 desktop-v2 依赖移动中的分支头，也不要依赖用户全局安装的上游 `hermes`。

## remote 布局

```
origin    https://github.com/Eynzof/hermes-agent-cn.git    fetch + push
upstream  https://github.com/NousResearch/hermes-agent.git fetch only
```

本地必须阻止向 `upstream` push，并且只抓官方 `main`：

```bash
git remote set-url --push upstream no_push
git config --unset-all remote.upstream.fetch
git config --add remote.upstream.fetch '+refs/heads/main:refs/remotes/upstream/main'
git config branch.main.remote origin
git config branch.main.merge refs/heads/main
git config pull.ff only
git config remote.origin.prune true
git config remote.upstream.prune true
```

## 日常上游同步

`upstream-watch.yml` 会在官方上游领先时创建或更新 issue。收到提醒后运行：

```bash
cd ~/Documents/GithubProjects/hermes/hermes-agent-cn
./scripts/sync-upstream.sh
```

脚本会做这些事：

1. 拒绝在 dirty working tree 上运行。
2. 检查 `upstream` push URL 是否为 `no_push`。
3. fetch `origin/main` 和 `upstream/main`。
4. 将本地 `main` fast-forward 到 `origin/main`。
5. 创建 `chore/sync-upstream-YYYYMMDD`。
6. 在同步分支上 merge `upstream/main`。

干净合并后：

1. 跑下面的 smoke test。
2. 推送同步分支：`git push -u origin chore/sync-upstream-YYYYMMDD`。
3. 开 PR 合回 `main`。
4. 测试通过后再 merge。

不要直接在 `main` 上合并上游，也不要 rebase 已发布的 `main`。

## Smoke test

每次同步上游后、每次打 `runtime-v*` tag 前都要跑：

```bash
python -m venv /tmp/hermes-cn-test
source /tmp/hermes-cn-test/bin/activate
pip install -e .

hermes dashboard --no-open &
sleep 3

TOKEN=$(curl -sS http://127.0.0.1:9119/ | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | sed 's/.*="\(.*\)"/\1/')
HEADER="X-Hermes-Session-Token: $TOKEN"

curl -sS -H "$HEADER" http://127.0.0.1:9119/api/mcp-servers | jq
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/profiles/active | jq
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/fs/list | jq
curl -sS http://127.0.0.1:9119/openapi.json | jq '.paths | has("/api/v2/events") and has("/api/v2/rpc")'

# /api/upload 尽量通过 desktop-v2 或 web composer 实测。
# 如果改到 gateway 相关代码，也要覆盖 provider.probe 和 model.options slug_filter。

kill %1
deactivate
rm -rf /tmp/hermes-cn-test
```

如果 smoke test 失败，不要合并同步 PR，也不要发布 runtime。

## fork 补丁规则

每个 fork-only 行为改动都要能追踪：

1. commit message 使用 `[CN-fork] P-NNN: 简短说明`。
2. 在 `FORK_NOTES.md` 总览表增加一行。
3. 在 `FORK_NOTES.md` 增加补丁详情。
4. 如果有运行时行为，补充 smoke test 覆盖点。

如果某个补丁适合贡献给官方上游，从 `upstream/main` 新建 `upstream-pr/*` 分支，只 cherry-pick 该补丁的最小必要改动。不要直接从 `origin/main` 给官方开 PR，因为 `origin/main` 携带 CN 专属和 runtime 发布相关改动。

## 冲突处理

同步分支 merge `upstream/main` 发生冲突时，先看双方都改了什么：

```bash
git log --oneline main..upstream/main -- <conflicted-file>
git log --oneline upstream/main..main -- <conflicted-file>
```

处理原则：

1. 优先保留上游的新行为。
2. 在新上游代码形态上重新实现 fork 补丁意图。
3. 如果上游已经提供等价能力，就删除本地重复实现。
4. 同步更新 `FORK_NOTES.md`。

## Runtime 发布

runtime release 是 `hermes-cn-desktop-v2` 首次启动和升级时下载的签名产物。只从验证过的 `main` 打 tag：

```bash
git switch main
git pull --ff-only origin main
git tag runtime-v0.14.0-cn.1
git push origin runtime-v0.14.0-cn.1
```

`release-runtime.yml` 会构建 PyInstaller 产物，用 `RUNTIME_SIGN_PRIVATE_KEY_PEM` 签 per-platform manifest，并发布到 GitHub Releases。manifest schema、签名字段和密钥轮换见 `docs/RUNTIME_RELEASES.md`。

## 禁止事项

- 不要 push 到 `upstream`。
- 不要 rebase 已发布的 `main`。
- 不要把不相关的 fork 补丁 squash 到一起。
- 不要从未审阅的同步分支打 `runtime-v*` tag。
- 不要让 desktop-v2 依赖用户全局安装的上游 `hermes`。
- 不要在公开 API 响应中暴露 MCP `command`、`args`、`env`。

## 参考

- Upstream: https://github.com/NousResearch/hermes-agent
- Fork: https://github.com/Eynzof/hermes-agent-cn
- Desktop consumer: https://github.com/Eynzof/hermes-cn-desktop-v2
- Runtime release docs: `docs/RUNTIME_RELEASES.md`
- Fork patch notes: `FORK_NOTES.md`

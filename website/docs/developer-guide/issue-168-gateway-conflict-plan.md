# Issue #168 — 桌面端无法启动飞书消息服务（已存在其它 Hermes Agent 时）

分支：`fix/desktop-feishu-gateway-wsl-conflict-issue168`
方案：**检测 + 清晰提示 + 一键接管**（混合方案）

---

## 1. 根因（已在代码中核实）

桌面端（Electron）会**自己拉起并托管**一个 dashboard（随机空闲端口 `9120–9199`，固定 `HERMES_HOME`，带 `HERMES_DASHBOARD_SESSION_TOKEN`）。点击"保存并启动飞书消息服务"时调用 `POST /api/gateway/restart`（`hermes_cli/web_server.py:1395`），后者 spawn `hermes gateway restart`。失败有**两种触发源**，本质都是"真正（没）跑起来的网关不是桌面端托管的那个"：

- **T1 — Windows 上已存在网关*服务*（计划任务/服务）**
  `hermes gateway restart` 在 Windows 分支会先判断 `gateway_windows.is_installed()`（`hermes_cli/gateway.py:6393–6408`），若已安装服务，就去 `gateway_windows.restart()` 重启**那个**服务网关。该服务网关跑在它自己的 `HERMES_HOME` 下，**永远看不到桌面端刚保存的飞书参数** → "参数已存但服务起不来"。这正是"当前网关不是桌面端托管进程"的字面症状。
  - 关键简化：桌面端从不安装 Windows 网关服务（它把网关当 dashboard 的子进程托管），因此**只要 `gateway_windows.is_installed()` 为真，对桌面端而言就是外部服务**。

- **T2 — WSL 中另有一个 Hermes / 第二个 Windows 安装（不同 HERMES_HOME）**
  重复实例守卫（`gateway/run.py:19507`）按 `HERMES_HOME` 隔离，跨 Windows↔WSL 边界看不到对方的 `gateway.pid`/锁。冲突通过**共享 localhost**暴露（WSL2 会把 `127.0.0.1` 转发）：飞书 webhook 端口默认 `8765`（`gateway/platforms/feishu.py:201`）被对方占用 → `connect()` 异常被吞成 `"Feishu startup failed: {exc}"`（`feishu.py:1686`，含 `Address already in use`）。
  - 注意：飞书若用 websocket 长连接模式则不绑定本地端口，此时 T2 走 `acquire_scoped_lock("feishu-app", app_id)`（`feishu.py:1663`），但该锁在 `~/.local/state/hermes/gateway-locks/`，跨边界亦不可见 → 难以从 Windows 侧可靠探测。本方案对"端口冲突"做精确检测，对其余并发情形给出通用提示。

### 现状链路（用于落点）
- 网关把每平台状态写入 `gateway_state.json`：`write_runtime_status(platform=…, error_code=…, error_message=…)`（`gateway/status.py`）。
- `/api/messaging/platforms` 经 `_messaging_platform_payload`（`web_server.py:3249`）透出 `state / error_code / error_message`。
- 桌面端 `MessagingPlatformInfo` 已带 `error_code`/`error_message`；消息页 `apps/desktop/src/app/messaging/index.tsx:508–513` 目前只把 `error_message` 原样塞进一个红框。

---

## 2. 设计总览

在 `gateway_state.json` 引入**结构化冲突信息**，在网关启动/重启时分类写入，经已有的 `/api/messaging/platforms` 透出，由桌面消息页渲染成**可操作的冲突面板**（中文）：

- 「**强制由桌面端接管并重启**」——仅在**同机**冲突（T1 服务，或端口被本机可见 PID 占用）可用。
- 「**查看如何停止其他实例**」——用于跨 VM（WSL）等无法接管的情形。

### 桌面端托管信号
桌面端在 spawn dashboard 时设 `HERMES_DESKTOP_MANAGED=1`；dashboard 继承后传给它 spawn 的 `hermes gateway restart` 子进程，后者据此判定"我应当托管网关"。

### 结构化字段（新增）
在每平台运行态里增加 `error_detail`：
```jsonc
"error_detail": {
  "kind": "service" | "port" | "other",
  "can_takeover": true,            // 同机可接管才为 true（跨 VM/WSL 为 false）
  "port": 8765,                    // kind=="port" 时
  "owner_pid": 1234,               // 本机可解析时
  "owner_home": "C:\\Users\\..."   // 已知时
}
```
`error_code` 取值：`gateway_conflict_service` / `gateway_conflict_port`（其余沿用旧值）。

---

## 3. 分文件改动计划

### Phase 1 — Python：分类冲突并写结构化状态

1. **`gateway/status.py`**
   - 扩展 `write_runtime_status(...)` 增加可选 `error_detail: dict | None`，落到 `platforms[platform]["error_detail"]`。
   - 新增 `classify_port_conflict(port: int) -> dict`：用 `psutil.net_connections()` 找本机占用该端口的 PID/cmdline；找到 → `{can_takeover: True, owner_pid, owner_home?}`；找不到（WSL2 relay 隐藏真实 PID）→ `{can_takeover: False}`。无 psutil 时优雅降级为 `can_takeover: False`。

2. **`gateway/platforms/feishu.py`（及 `gateway/platforms/base.py` 共用 webhook 平台）**
   - 在 `_connect_webhook`/`connect` 异常处理（`feishu.py:1684–1689`）识别 `OSError` 且 `errno in (EADDRINUSE, WSAEADDRINUSE/10048)`：置 `error_code="gateway_conflict_port"`，`error_message` 用更友好的文案，`error_detail=classify_port_conflict(self._webhook_port)`（补 `kind:"port"`,`port`）。
   - app-lock 分支（`feishu.py:1671`）保持，但归一到 `error_detail={kind:"other", can_takeover:False}`。

3. **`hermes_cli/gateway.py` — restart 分支（`6293–6450`）**
   - 读取 `HERMES_DESKTOP_MANAGED`（新 helper `_is_desktop_managed()`）。
   - **Windows 且 desktop-managed**：不再无条件 `gateway_windows.restart()`。
     - 若 `gateway_windows.is_installed()`（=外部服务）且未带强制标志 → 写 `gateway_conflict_service`（含 `error_detail{kind:"service",can_takeover:True}`）并以清晰中/英文退出（非 0），**不**重启外部服务。
     - 若带强制标志（`HERMES_GATEWAY_FORCE_TAKEOVER=1`）→ `gateway_windows.stop()` 停掉外部服务，然后 `run_gateway(replace=True)` 跑桌面端托管网关。
   - desktop-managed 的**手动重启路径**（`6441–6449`）改用 `run_gateway(verbose=0, replace=True)`，确保接管自身陈旧网关。
   - 非 desktop-managed 行为保持不变（回归零风险）。

4. **`hermes_cli/web_server.py`**
   - `/api/gateway/restart`（`1395–1407`）接收可选 `force`（query/body）；为真时给 spawn 的子进程注入 `HERMES_GATEWAY_FORCE_TAKEOVER=1`。确认 `_spawn_hermes_action` 透传/合并 env。
   - `_messaging_platform_payload`（`3307–3328`）在返回里带上 `error_detail`。

### Phase 2 — Desktop：透出冲突 + 动作

5. **`apps/desktop/electron/main.cjs`**
   - `startHermes`（spawn env `4374–4388`）与 `spawnPoolBackend`（`4241–4247`）的 env 增加 `HERMES_DESKTOP_MANAGED: '1'`。

6. **`apps/desktop/src/hermes.ts`** — `restartGateway(force = false)`：`force` 时 POST `/api/gateway/restart?force=1`。

7. **`apps/desktop/src/types/hermes.ts`** — `MessagingPlatformInfo`/`PlatformStatus` 增加可选 `error_detail`（`kind`,`can_takeover`,`port?`,`owner_pid?`,`owner_home?`）。

8. **`apps/desktop/src/app/messaging/index.tsx`（`508–513`）**
   - 当 `error_code ∈ {gateway_conflict_service, gateway_conflict_port}` 时，渲染冲突面板：标题 + 说明 + 按钮：
     - 「强制由桌面端接管并重启」→ `restartGateway(true)` 后 `refreshPlatforms()`；仅 `error_detail.can_takeover` 为真时可点，否则禁用并解释。
     - 「查看如何停止其他实例」→ 展开/跳转帮助（停止 WSL 内 Hermes / 停止 Windows 网关服务的步骤）。
   - 其余 `error_code` 保持原红框展示。

9. **`apps/desktop/src/i18n/{zh,en}.ts` + `types.ts`** — 新增 messaging 文案 key：`conflictTitle` / `conflictServiceBody` / `conflictPortBody` / `forceTakeover` / `howToStopOthers` / `takingOver` 等（zh 为主，en 同步，types 补类型）。

### Phase 3 — 测试

10. **Python**：`tests/hermes_cli/`（或 `tests/gateway/`）新增
    - `classify_port_conflict` 的本机命中/未命中分支（mock psutil）。
    - restart 的 desktop-managed 分支：mock `gateway_windows.is_installed()` → 验证未强制时写 `gateway_conflict_service` 且不调用 `restart()`；强制时调用 `stop()` + `run_gateway(replace=True)`。
    - `write_runtime_status` 持久化 `error_detail` 往返。

11. **Desktop**：渲染层测试覆盖冲突面板对 `error_code`/`can_takeover` 的映射（按钮可点/禁用）；`main.cjs` env 注入可加进现有 connection/bootstrap cjs 测试。

---

## 4. 风险与边界
- **强制接管**会停掉用户已安装的 Windows 网关服务——仅经用户显式点击且面板有明确说明；非 desktop-managed/未强制时绝不动外部服务（默认零副作用）。
- **跨 VM（WSL）**无法从 Windows 杀进程：`can_takeover:false`，只给停止指引，不做破坏性操作。
- **websocket 模式的 WSL 并发**不可靠探测：本方案不强行猜测，端口冲突精确报、其余给通用并发提示。
- 待实现时复核的假设：桌面端确实从不安装 Windows 网关服务（=已安装服务即外部）；`_spawn_hermes_action` 的 env 合并方式。

---

## 5. 验收对照（Issue 期望）
- ✅ 不再停留在"参数已存但服务起不来"：要么一键接管成功，要么给出明确冲突+处置入口。
- ✅ 明确处理"当前 Dashboard/网关非桌面端托管"场景（T1 服务 / T2 端口）。
- ✅ 提供处置入口：强制接管（同机）/ 停止其他实例指引（WSL）。

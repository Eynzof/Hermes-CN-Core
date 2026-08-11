# Fork notes - Eynzof/hermes-agent-cn

英文版：[`FORK_NOTES.md`](./FORK_NOTES.md)

本文记录 `main` 分支相对官方上游 `NousResearch/hermes-agent` 的 fork 专属改动。新的行为补丁应使用 `[CN-fork] P-NNN: ...` 提交信息，并在本文登记。

## 补丁总览

| ID | 目标文件 | 做了什么 | 为什么需要 | 上游状态 |
|---|---|---|---|---|
| **P-060** | `scripts/sign_macos_runtime_payload.sh`、`scripts/macos-runtime.entitlements.plist`、runtime 发版回归测试 | 仅给 macOS PyInstaller 主程序签入最小的 `com.apple.security.cs.allow-unsigned-executable-memory` hardened-runtime 例外，并在签名后校验 entitlement 确实嵌入；内置 dylib/framework 会被签名，但不附加可执行内存权限。 | Python 3.14 的 `_ctypes`/系统 libffi 在 macOS 13 上分配闭包跳板时，若 hardened 主程序没有可执行内存权限会无限循环。因此 `runtime-v0.19.0-cn.7` 在 Desktop 安装阶段执行 `dashboard --help` 时超过 60 秒，而较新的 macOS CI runner 没有复现。 | 仅 CN runtime 发版打包链路；上游不发布这套签名 PyInstaller 产物。 |
| **P-059** | `hermes_cli/web_server.py`、`tests/hermes_cli/test_web_server.py` | 将已认证 `GET /api/media` 的 data URL 响应从图片扩展到常见视频（MP4、WebM、MOV、MKV、AVI、M4V、MPEG、OGV），视频使用独立 25 MiB 回退上限；新增支持单段 Range、最大 4 GiB 的 `GET /api/media/file` 流式接口。档案媒体根内路径继续直接信任；只有 Desktop-managed 后端收到本机请求时才可转发根外路径，且仍必须通过共享的网关媒体投递 denylist/strict-mode 策略。远程与 OAuth dashboard 仍严格限制在媒体根内。 | Desktop 回复可以引用用户桌面上的图片或视频 `MEDIA:<path>`，但 Core 过去拒绝所有视频扩展名和 Hermes 媒体根外路径；大视频若继续走 JSON/base64 + Tauri 字符串 IPC 还会放大内存且无法拖动进度，因此需要受控直流式传输。 | 通用远程媒体能力，适合上游化。 |
| **P-058** | `tools/environments/local.py`、`tools/terminal_tool.py`、`tools/process_registry.py`、`agent/prompt_builder.py`、`website/docs/user-guide/windows-native.md`、`website/docs/reference/environment-variables.md`、`website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/windows-native.md`、`website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/reference/environment-variables.md`、README 系列、`tests/tools/test_shell_resolution.py`、`tests/tools/test_terminal_dynamic_description.py`、`tests/tools/test_process_registry.py`、`tests/tools/test_find_bash_optional_probe.py`（新增）、`tests/agent/test_prompt_builder.py`、`FORK_NOTES.md`、`FORK_NOTES.zh-CN.md` | **Windows 默认 shell 偏好：git-bash → PowerShell 7 → Windows PowerShell 5.1。** 当 `terminal.shell` 未设置（`auto`）时，`_resolve_shell()` 现在优先选用 **git-bash**（经既有 `_find_bash` 发现链——env 覆盖 → 托管便携 Git → 标准安装位置 → PATH——加 `_bash_starts()` 冒烟测试，确为可用安装），再回退 `pwsh` 7.x，最后 `powershell.exe` 5.1。显式 `shell: bash` / `shell: pwsh` / `shell: powershell` 照旧尊重。`_find_bash` 新增非抛错探测（`raise_if_missing=False` → `None`）；`_detect_shell_for_description` 镜像同一顺序（bash→pwsh→powershell；显式 bash 现在返回 `"bash"`，未知值按 auto 处理）；`process_registry` 的后台/PTY 拉起变为 shell 感知，新增 `_build_bash_background_script`（bash 用 `-lc`，PowerShell 保留 `-NoProfile`）；prompt_builder 的 auto shell 提示优先 git-bash 提示。 | P-050 把 Git Bash 恢复为 Windows 上*可选显式* shell，但默认仍是 PowerShell。装了 Git for Windows 的用户（很常见——装 git 就带 bash）即使机器上摆着一个完整可用的 POSIX shell，默认也拿到 PowerShell 语法提示和 PowerShell 后台包装脚本。有 git-bash 时默认启用，让 agent 零配置获得 POSIX 语法（`pwd -P`、`sed/awk/grep` 管道），而 PowerShell 7 / 5.1 仍是每台 Windows 10/11 的保底回退。 | Windows 默认是刻意分叉（上游 shell 故事见 P-016/P-019/P-050）；非抛错 `_find_bash` 探测与 shell 感知的后台拉起是通用改进，可上游。Desktop 预检（`apps/desktop/electron/main.ts`）留作后续，本次不做。 |
| **P-057** | `plugins/memory/__init__.py`、`agent/system_prompt.py`、`.github/workflows/release-runtime.yml`、聚焦测试 | 让内置记忆 provider 发现逻辑兼容 PyInstaller 的代码/数据分离布局：manifest 仍在磁盘，provider 代码则由冻结模块加载器提供。runtime 现在收集全部 `plugins.memory` 子模块，并用实际冻结二进制冒烟 OpenViking 与 Hindsight。Managed Desktop 的系统提示会钉住当前精确 `HERMES_HOME`，修正命名档案路径说明，并要求通过 managed CLI/API 验证记忆配置，而不是只探测外部服务。 | `runtime-v0.19.0-cn.6` 虽然带了 OpenViking/Hindsight manifest，却没有物理 `__init__.py`；原来的纯目录发现因此让 `/api/memory` 完全看不到 provider。受影响的 Desktop 会话随后检查了全局 `~/.hermes`，并把 OpenViking 服务健康误判成 managed 档案已经配置成功。 | 可导入模块发现与活动档案路径修正是通用修复；冻结打包闸门和 Managed Desktop 指引属于 CN 发版链路。 |
| **P-056** | `hermes_cli/web_server.py`、`tui_gateway/server.py`、网关/API 聚焦测试 | 加固 Desktop 请求边界：会话记录分页参数不再接受负数，`prompt.submit` 拒绝非字符串文本，即使文件名伪装成图片也必须通过魔数校验，cron 工具返回的校验失败会转换为 JSON-RPC 错误。 | 畸形或伪造的 Desktop 请求过去可能绕过会话记录上限、以错误类型进入回合处理、把任意字节落盘成图片，或把失败的 cron 变更误报为成功。 | 通用网关/API 正确性修复，适合上游化。 |
| **P-055** | `agent/memory_provider.py`、`hermes_cli/web_server.py`、`tools/memory_tool.py`、`plugins/memory/openviking/__init__.py`、`plugins/memory/hindsight/__init__.py`、聚焦测试 | 新增可选只读运行状态 hook 与 `GET /api/memory/providers/{name}/status`；OpenViking/Hindsight 分别聚合健康、就绪、组件、模型、队列、记忆或 bank/runtime 指标，且不返回秘密。Provider 配置保存支持 `activate:false`，省略时仍保持原有“保存并启用”兼容行为。直接写入档案 `memory.openviking` 的 endpoint 也会参与连接解析与可用性判断。内置 `MEMORY.md` 配置继续默认 2200 字符，并统一发布和执行 1–8000 字符范围。 | Desktop managed runtime 使用隔离的 `HERMES_HOME`；旧记忆页只会激活、不会保存连接字段，导致 OpenViking 一直是 `needs_config`，两个后端也都无法在 Hermes 内监控；内置记忆编辑器也需要与 Core 共享同一套容量边界。 | 通用 provider 可观测与配置接口，待 UI 评审后适合上游。 |
| **P-054** | `model_tools.py`、`tools/environments/base.py`、`tools/environments/local.py`、`tools/environments/modal_utils.py`、`tools/terminal_tool.py`、`tui_gateway/cli_delegation.py`、`tui_gateway/server.py`、Claude Code/Codex 技能及聚焦测试 | 在 P-047 上补齐前台 terminal 委派：agent 调度层把 `tool_call_id` 透传到工具注册表，pipe 型环境再按该 id 提供失败开放的实时输出回调；gateway 将前后台 chunk 都以 ≤2 Hz 合并为 `delegation.cli.output`。普通文本输出归一化为脱敏 `raw` 进度事件；终态从 Claude/Codex JSON 汇总 model/session/workdir 与 Token，并兼容解析 Codex 人类可读头部和 `tokens used` 尾部。terminal 结果补充 shell 执行后的真实 cwd，动态 shell 表达式不再被错误展示为字面量 `$`，内置技能在前台也优先请求 stream-json/`--json`。 | 前台 Claude Code/Codex 过去只有 started/completed，Desktop 会静默转圈数分钟；completed 事件一旦丢失就永久显示运行中，而且旧 Codex 技能未加 `--json` 时目录与 Token 始终为空。 | 通用 terminal/gateway 可观测性可上游；事件名与分类器仍由 CN Desktop 驱动。关联 P-047。 |
| **P-053** | `scripts/update_thirdparty.py`、`tests/scripts/test_update_thirdparty.py` | 新增维护脚本，检查 **ripgrep** 与 **rtk** 的 GitHub 最新 release，自动同步 `scripts/install.sh`、`scripts/install.ps1`、`tools/rtk_provision.py`、`.github/workflows/tests.yml` 中**每一处**版本钉子（tests.yml 里 `test` 与 `e2e` 两个 job 各钉一次 ripgrep），并重新计算 ripgrep 在 CI 工作流里的 SHA256；支持 `--china-mirror` / `--mirror` / `HERMES_THIRDPARTY_MIRROR` 显式开启的国内镜像回退。 | Hermes-CN 把这些外部二进制版本钉在多种文件格式（Bash、PowerShell、Python、YAML）中，且国内访问 GitHub 经常慢或不通；统一 updater 可保持多文件版本一致，并提供镜像回退。 | CN 专属维护工具；待上游引入 `_ripgrep_common.py` / `_rtk_common.py` 后可考虑 upstream |
| **P-025** | `hermes_cli/web_server.py` | `/api/providers/oauth` 现在：(1) 命中 20s 的按 profile 进程内 TTL 缓存；(2) 用 `asyncio.to_thread` 并发跑各 provider 的状态检查（移出 FastAPI 事件循环），不再串行内联；(3) 在每次连接/断开时失效缓存（断开的两条清理路径、PKCE submit、设备码/loopback 轮询到 `approved`）。另加 `refresh=true` 逃生阀。 | 桌面端模型页每次打开、以及每次窗口重新聚焦都会串行枚举所有 OAuth provider 的状态；部分检查会联网/起子进程，而该 handler 是 `async`，于是阻塞了同时服务聊天网关 WebSocket 的事件循环——模型页要等好几秒，还会拖累实时会话。 | 建议上游（通用响应性修复） |
| **P-001** | `tui_gateway/server.py` | provider 配置 dict/list 不一致修复 | 早期 fork 需要兼容用户配置形态 | 已由上游修复，本 fork 不再携带 |
| **P-002** | `hermes_cli/web_server.py` | 增加 `POST /api/upload` 附件上传接口 | desktop / web composer 拖拽上传依赖它 | 未进入上游 |
| **P-003** | `hermes_cli/web_server.py` | 去掉 `/api/ws` 的 `_DASHBOARD_EMBEDDED_CHAT_ENABLED` 门禁 | desktop 以 headless dashboard 方式运行，不带 `--tui` 时仍需要 gateway WS | **基本被上游解决** —— v0.16.0(#38591)默认把该标志设为 `True` 并移除了 `--tui`；fork 仍保留 `/api/ws` 上的显式去门禁作为纵深防御 |
| **P-004** | `hermes_cli/web_server.py` | 增加 `GET /api/fs/list` 文件夹浏览接口 | web 工作区选择器需要列目录，避免让用户手输路径 | **已与上游收敛** —— 上游后来自己也提供了 `/api/fs/list`（camelCase、无 home 限制）；fork 的旧助手已作为死代码删除 |
| **P-005** | `hermes_cli/web_server.py` | 增加 `GET /api/mcp-servers` 只读 MCP 列表 | desktop 健康检查需要 MCP 数量，但不能泄露 command/args/env | 可考虑上游 |
| **P-006** | `hermes_cli/config.py` | 为 CN provider 注册 `OPTIONAL_ENV_VARS` | 模型设置页需要展示 ARK、QIANFAN、HUNYUAN、SiliconFlow 等密钥项 | CN 专属，通常不向上游提交 |
| ~~**P-007**~~ | `tui_gateway/ws.py` | ~~捕获并记录 gateway dispatch 异常，返回 JSON-RPC error~~ | 否则前端只看到 WebSocket closed，缺少诊断信息 | **已被上游取代** —— 2026-06-04 同步中已删除 |
| **P-008** | `hermes_cli/web_server.py`、`hermes_cli/main.py`、档案相关定向测试 | 保留 `GET/PUT /api/profiles/active` 兼容层，并让 Desktop 托管启动信任显式 `HERMES_HOME`，避免命名档案切回默认档案时被旧 sticky 档案重新劫持 | desktop 切换器需要兼容读写 sticky active profile；实时切回默认档案时，旧 `active_profile` 会保留到替换进程就绪，Core 不能把新进程重新导向旧档案 | 上游已提供 GET/POST；其余为 Desktop 兼容与重启语义 |
| **P-009** | `hermes_cli/web_server.py`, `tui_gateway/sse.py` | 增加 `/api/v2/events` SSE 和 `/api/v2/rpc` POST transport | ~~desktop 默认使用 EventSource + POST~~ → desktop ≥ 0.4 已改用原生 `/api/ws` WebSocket（与官方桌面端一致），此 transport 只服务旧版外壳 | **已弃用** —— 为 ≤ 0.3.x 旧外壳保留（外壳无自更新而 runtime 热更新），旧外壳 EOL 后移除。不上游。 |
| **P-010** | `hermes_cli/config.py` | 注册 `LONGCAT_API_KEY` | CN 模型设置需要 LongCat 密钥入口 | CN 专属，除非上游支持 LongCat |
| **P-011** | `tui_gateway/server.py` | 给 `model.options` 增加 `slug_filter`，并增加 `provider.probe` RPC | desktop 需要过滤模型选择器，并轻量探测 provider 状态 | 可考虑上游 |
| **P-012** | `hermes_cli/main.py` | `_model_flow_anthropic()` 支持保留或自定义 `base_url`，不再无条件删除 | 使用 Anthropic 兼容代理或私有端点的用户需要在模型设置流程中保留自定义 `base_url` | 建议上游 |
| **P-013** | `model_tools.py`, `tests/run_agent/test_repair_tool_arg_keys.py` | 在 `handle_function_call` 中增加自动参数键修复：全局别名表、工具级覆盖、模糊匹配、嵌套对象/数组递归修复，以及可选回调通知 | LLM 经常把参数名写错（如 `file`→`path`、`cmd`→`command`），此前会直接报 "unknown parameter"；该补丁在不放宽 JSON Schema 的前提下提高工具调用的容错率 | 建议上游 |
| **P-014** | `.github/workflows/release-runtime.yml`, `tools/mcp_tool.py`, `hermes_cli/config.py`, `docs/RUNTIME_RELEASES.md`, `tests/tools/test_mcp_tool.py` | 把原生 MCP 客户端 SDK 打进冻结 runtime（安装入口后并入 `cn-desktop` extra，见 P-015；外加 `--collect-submodules/--copy-metadata mcp` + CI 断言 `mcp-*.dist-info` 存在）；并让 `discover_mcp_tools()` 在已配置 `mcp_servers` 但 SDK 缺失时输出一次 WARNING，而不是在 debug 级别静默跳过 | issue #16：desktop runtime 打包时缺少 `mcp` extra，导致 `_MCP_AVAILABLE=False`，已配置的 `mcp_servers` 不注册任何工具且 INFO 日志无任何提示。打包改动是 CN 特有，诊断日志与已知根键则是通用改进 | 打包改动 CN 特有；`mcp_tool.py` 告警与 `mcp_servers` 根键建议上游 |
| **P-015** | `pyproject.toml`, `.github/workflows/release-runtime.yml`, `docs/RUNTIME_RELEASES.md`, `uv.lock` | 新增 `cn-desktop` 聚合 extra，把冻结 runtime 暴露的所有后端预打包（`web`、`anthropic`、`mcp`、`feishu`、`dingtalk`、`wecom`，以及微信用的 `aiohttp`/`qrcode`/`cryptography`）。发布流程改为安装 `.[cn-desktop]`，收集各 IM SDK 子模块与元数据，新增"构建环境 import 冒烟"，并断言每个后端的 `dist-info` 出现在冻结产物中 | 桌面反馈：飞书/钉钉/企微/微信适配器因 SDK（`lark-oapi`、`dingtalk-stream` 等）从未被打包、且冻结环境无法懒安装而静默降级为"不可用"。根因同 P-014，推广到所有桌面后端 | 打包 CN 特有；不上游（上游不构建这些产物） |
| **P-016 / P-019 / P-XXX** | `tools/environments/local.py`、`tools/environments/process_pwsh.py`、`tools/environments/windows_env.py`、`tools/terminal_tool.py`、`agent/prompt_builder.py`、`model_tools.py`、`cli.py`、`apps/desktop/electron/main.cjs`、`scripts/install.ps1`、`hermes_cli/uninstall.py`、`cron/scheduler.py`、`tools/environments/base.py`、`tools/file_operations.py`、`tools/browser_tool.py`、`hermes_bootstrap.py`、`scripts/check-windows-footguns.py`、`scripts/verify_windows_utf8.py`、相关测试、网站文档、`FORK_NOTES*.md` | **Windows shell 演进史——Git Bash → PowerShell。** P-016（已被取代）加入 PowerShell 原生执行：Windows 上 `pwsh.exe`（PS7）为主、`powershell.exe`（PS5.1）回退，完整生命周期（`_run_pwsh`、`_wrap_command_pwsh`、`init_session`、cwd 跟踪），运行时自适应的 terminal 工具描述（把 Linux/bash 命令引用替换为 PowerShell cmdlet），工具定义缓存键加 shell 指纹，`pwsh_transform` 警告传递，并删除 Git Bash 自动安装。P-019 完成迁移：移除全部 Git Bash 发现逻辑（7 策略 `_find_bash`、WSL 启动器过滤、`HERMES_GIT_BASH_PATH`）；Windows 上**仅使用 Windows PowerShell 5.1**（`powershell.exe`，每套 Windows 10/11 自带）——无 `pwsh.exe` 探测、无下载、无安装；`HERMES_SHELL_TYPE=bash` 在 Windows 上抛 RuntimeError；重命名 `_find_pwsh_simple`→`_find_powershell`、`_run_pwsh`→`_run_powershell`、`_wrap_command_pwsh`→`_wrap_command_powershell`、`_normalize_git_bash_path`→`_normalize_msys_path`；`pwsh_transform` 始终开启；桌面端 `findGitBash()`→`findPowerShell()`；移除 `Install-Git`/`Set-GitBashEnvVar`/`Stage-Git`；cron 在 Windows 拒绝 `.sh`/`.bash`；PowerShell UTF-8 编码加固（`ps_with_utf8()`、控制台 CP_UTF8 引导、仅 PowerShell 子进程保留 `encoding='utf-8'`）。P-XXX（未编号后续）恢复 **pwsh 优先检测**：`_find_pwsh()` 多步检测（PATH、ProgramFiles、注册表、LocalAppData）；`_resolve_shell()` 优先 pwsh；原生 pwsh 时跳过 `pwsh_transform`；所有分发点同时接受 `"powershell"` 与 `"pwsh"`；pwsh 感知的动态终端描述与 `_WINDOWS_PWSH_SHELL_HINT`。 | Windows 上 agent 原本硬编码为 Git Bash；PowerShell 启动更快、路径处理更原生、免 POSIX 翻译，且随每套 Windows 10/11 自带（零安装零下载）。P-019 删除约 400 行死代码，让 agent 在 Windows 上拥有唯一、可预测、始终可用的 shell——P-016 的 `pwsh.exe` 探测是不必要的复杂度（5.1 全覆盖）；P-XXX 则让装了 pwsh 的用户完全跳过 PS7→PS5.1 降级转换。 | 应上游化（P-019 取代 P-016；P-XXX 是其后续） |
| **P-017** | `agent/tool_dedup.py`, `agent/agent_init.py`, `agent/conversation_loop.py`, `agent/tool_executor.py` | 增加 `ToolDedupTracker`，在跨 API 迭代间检测重复的相同工具调用，并在重复次数达到 3、5、8 次时注入逐级升级的 `<system-reminder>` 提示以打破无限循环 | Agent 在处理复杂任务时可能陷入无限循环，反复调用相同工具和参数——现有同轮去重 `_deduplicate_tool_calls` 无法检测跨迭代模式 | 内部机制——解决行为健壮性缺口；机制通用，但集成点与 fork 架构耦合 |
| **P-018** | `agent/agent_init.py`, `tests/run_agent/test_init_fallback_on_exhausted_pool.py` | 增加 `_api_key_required` 辅助函数，并在 OpenAI / Anthropic SDK 客户端构造前加入空 key 保护；当 api_key 为空且 provider 需要密钥时，抛出 `RuntimeError: no API key (param empty, env vars unset)` | 此前空 key（参数为空且环境变量未设置）会触发底层 SDK 认证异常，在 TUI/gateway 后台线程中表现为 panic 且无堆栈信息 | 建议上游 |
| **P-020** | `tools/environments/windows_env.py`（新建）, `tools/environments/local.py`, `hermes_cli/claw.py`, `hermes_cli/managed_uv.py`, `hermes_cli/gateway.py`, `hermes_cli/dep_ensure.py`, `hermes_cli/clipboard.py`, `skills/creative/comfyui/scripts/hardware_check.py` | 新增 `refresh_env_from_registry()` 函数，从 Windows 注册表（HKLM + HKCU）刷新 `os.environ["PATH"]` 和 `os.environ["PATHEXT"]`，在每次 PowerShell 子进程调用前执行，使进程启动后安装的工具（如 WinGet、MSI）可被发现。参考 `kimi-cli/src/kimi_cli/utils/environment.py` 的实现。非 Windows 平台无操作。 | 如果不刷新，agent 无法发现进程启动后安装的二进制文件（例如通过 WinGet 安装的工具）— `shutil.which` 和 `subprocess.Popen` 只能看到进程创建时捕获的 PATH。当 agent 在会话中安装自己的依赖（node、uv 等）时尤其痛苦。 | 建议上游 |
| **P-021** | `gateway/run.py`、`cron/scheduler.py`、`cron/jobs.py`、`hermes_time.py` | 四项 cron "静默停摆" 根因修复：(1) `_start_cron_ticker` 初始化包在 try/except 中，防止 daemon 线程静默死亡；(2) 僵尸 `.tick.lock` 自动清理——锁文件 mtime 超过 `lock_stale_seconds`（默认 120s）则删除；(3) `_validate_cron_startup()` 启动前校验 `jobs.json` 可解析性；(4) `_ensure_aware` 按配置时区解释无时区时间戳；修复 `hermes_time.py` 缺失的 `def now()`；每次 tick 调用 `reset_cache()` 使时区配置热生效。 | `jobs.json` 损坏 → ticker 线程崩溃 → daemon 静默死亡。僵尸 `.tick.lock` → 所有后续 tick 永久阻塞。ticker 初始化 `ImportError` → 线程零日志死亡。服务器时区 ≠ 配置时区 → 调度时间静默偏移。 | 建议上游 |
| **P-022** | `agent/chat_completion_helpers.py`、`agent/anthropic_adapter.py`、`agent/httpx_clients.py`、`run_agent.py`、`tests/run_agent/test_streaming_stale_timeout.py` | 修复流式 stale-stream 检测器，让静默断掉的服务商连接不可能永久卡死一个回合：(1) 检测器现在中止**活的** transport——`anthropic_messages` 模式下跨线程 `shutdown(SHUT_RDWR)` Anthropic 客户端的 socket（#29507 安全）并重建，而不是只碰 OpenAI 请求客户端（Anthropic 流因此一直挂着）；(2) 有界升级：间隔 `HERMES_STREAM_STALE_KILL_GRACE` 的 `HERMES_STREAM_STALE_MAX_KILLS` 次中止后，合成 `TimeoutError` 放弃 daemon worker，而不是重置自己的计时器空转；(3) 卡住期间用**实时** `_emit_status` 上报，不再走回合结束才 flush 的缓冲通道；(4) 给 Anthropic httpx 客户端补 TCP keepalive（与 OpenAI 主客户端对齐），共享 `keepalive_socket_options()`。 | 桌面/网关长会话永久卡死（"计时器一直走，任务已经死了"）：Anthropic 流静默（半开 socket）后从未被中止，worker 线程阻塞在 `recv()`，检测器重置自己的 `last_chunk_time` 空转，缓冲状态也从不 flush——后端和桌面端都看不到任何错误。 | 建议上游（通用可靠性修复）；2026-07 同步：上游自行演化出 close()+重置计时器 的变体，fork 保留 FD-safe socket 击杀 + 有界升级（刻意不重置 last-chunk 时间戳）；2026-07-10 v0.18.2 同步：并入上游跨回合 stale 熔断（#58962），熔断计数在 FD-safe 击杀前累加；上游 httpx 连接池主动回收（keepalive_expiry=20s）尚未移植进 `agent/httpx_clients`，待跟进 |
| **P-024** | `agent/agent_runtime_helpers.py`、`tests/run_agent/test_agent_guardrails.py`、`tests/run_agent/test_session_meta_filtering.py` | 在 `sanitize_api_messages` 中增加空内容过滤：丢弃 `content` 为 `""` 且无有效载荷的 `assistant`/`user`/`function` 消息；保留仍携带 `tool_calls`、`codex_reasoning_items`、`codex_message_items` 或 `reasoning_content` 的 `assistant` 消息。 | MiMo v2.5 及严格的 OpenAI 兼容网关会拒收空 `content` 消息（HTTP 400 / "text is not set"）。长会话（如飞书 3-13h）在上下文压缩/截断后可能留下这类消息。 | 建议上游；2026-07-10 v0.18.2 同步：上游给清洗管线新增三项防御（#58168 损坏参数修复用规范 `call_id||id` 优先级、#58755 空 `tool_calls` 数组规整、#58327 `tool_call_id` 去重），已全部移植进 fork 的单趟融合版 `sanitize_api_messages`（经 `agent.message_utils`，保持不导入 `run_agent` 的性能特性） |
| **P-027** | `cli.py`、`tests/cli/test_cli_save_config_value.py` | `save_config_value()` 只在项目级 `cli-config.yaml` **已存在**时才写入它；否则写入（并创建）**用户**配置——绝不在已安装包/源码树里创建配置文件。 | 旧的 `else project_config_path` 分支在 `HERMES_HOME` 没有 `config.yaml` 时（如测试隔离 home）会创建 `<repo>/cli-config.yaml`；并行测试跑批下该文件泄漏给 8 个 worker，污染走项目配置的读取路径（`HERMES_IGNORE_USER_CONFIG` 下的 `load_cli_config`），使 `test_ignore_user_config_flags` 变 flaky。生产环境往包目录写配置同样是错的。 | 建议上游 |
| **P-023** | `tui_gateway/server.py` | 网关回合执行器现在会把"漏接"的 `/steer` 作为下一轮用户输入投递。`run_conversation()` 只能把 steer 注入到*后续*的工具结果里；落在最后一个工具批次之后（或纯文本回合）的 steer 会以 `result["pending_steer"]` 返回。`cli.py` 会重新投递它，但网关此前直接丢弃——导致桌面端（运行时输入行为默认 "引导/steer"）发出的 steer 静默丢失。仿照已有的 `goal_followup` 链路：在 `finally` 释放 `session["running"]` 后，用 steer 文本发起一次嵌套 `_run_prompt_submit`（受 `running` 保护，真实用户输入优先；优先级高于 goal 续跑）。 | 桌面端反馈（#193）："引导功能不好用……等到任务执行完，我引导的东西也没插入进去"——晚到的 steer 被 `agent.steer()` 接受却从未生效，因为网关忽略了 `pending_steer`。 | 建议上游（通用可靠性修复） |
| **P-026** | `hermes_constants.py`、`hermes_bootstrap.py`、`tests/test_managed_runtime_caches.py` | 桌面以托管运行时方式启动时（`HERMES_DESKTOP_MANAGED=1`），`configure_managed_runtime_caches()` 用 `setdefault` 把第三方缓存/临时目录环境变量指向 `<HERMES_HOME>/cache` 的子目录：`HF_HOME`、`HUGGINGFACE_HUB_CACHE`、`TORCH_HOME`、`TIKTOKEN_CACHE_DIR`、`MPLCONFIGDIR`、`NLTK_DATA`、`PLAYWRIGHT_BROWSERS_PATH`，以及（仅当三者都未设置时）`TMPDIR/TEMP/TMP`。从 `hermes_bootstrap`（每个入口的第一个 import）调用，确保早于 transformers/tiktoken/playwright 加载。 | Windows 桌面占盘：即使桌面端已把自身运行时树锚定到所选安装盘，这些库仍默认把缓存写进 `~/.cache`（C 盘），导致用户选了 D 盘安装、C 盘照样被撑满。`setdefault` + `HERMES_DESKTOP_MANAGED` 门控让独立 CLI 安装与用户显式覆盖不受影响。 | CN 桌面收敛；环境变量钩子通用，可考虑上游 |
| **P-028** | `agent/models_dev.py`、`agent/models_dev_snapshot.json`（新增）、`agent/model_metadata.py`、`hermes_cli/model_cost_guard.py`、`hermes_cli/web_server.py`、`tui_gateway/server.py`、`gateway/slash_commands.py`、`cli.py`、`hermes_cli/auth.py`、`scripts/refresh_models_dev_snapshot.py`（新增）、`pyproject.toml`、`MANIFEST.in`、`.github/workflows/release-runtime.yml` | 让 models.dev 元数据离线优先，使模型保存/切换不再阻塞在网络上。(1) 随包预置 `models_dev_snapshot.json`，并在 `fetch_models_dev` 补上真正的 Stage 0/4 兜底，缓存永不为空。(2) 新增 `allow_network=False` 非阻塞读模式，贯穿 `get_model_capabilities`/`get_model_info`/`lookup_models_dev_context`/`get_model_context_length`/`expensive_model_warning`；所有模型保存/切换热路径（网关 `config.set`、REST `/api/model/set`、`/api/model/info`、`/model` 斜杠命令、CLI 切换、模型保存告警）都走该模式——只读缓存/快照、fail-open。(3) `MODELS_DEV_URL` 与超时可用环境变量覆盖（`HERMES_MODELS_DEV_URL` 国内镜像、`HERMES_MODELS_DEV_TIMEOUT`，默认 15s→3s）；`prewarm_models_dev_async` 在 web 启动时后台预热缓存（`HERMES_DISABLE_MODELS_DEV_PREWARM` 可关）。 | 国内访问 `https://models.dev/api.json` 慢/被墙；同步的 15s 超时拉取就卡在 `/models` 页"设为当前模型"/"保存"的关键路径上，而缓存只有请求成功才会写入——于是每次操作都重吃满 15s（桌面端反馈：模型操作要"十几秒"）。 | 离线优先快照 + 非阻塞读模式建议上游；国内镜像开关与打包属 CN 专有 |
| **P-029** | `hermes_cli/main.py`、`cron/jobs.py`、`.github/workflows/release-runtime.yml` | 在 `cmd_dashboard()`（同步主流程）里、`start_server()` 之前**额外**启动一遍 desktop cron tick 线程——不再只依赖 FastAPI lifespan handler，于是 lifespan 静默失败也不会让 cron 死掉；新路径失败时用 `logger.exception()` 显式记录。lifespan 里的原 ticker 保留作双保险；`cron/.tick.lock` 的 flock 让两者互斥（同一进程内、不同 fd 也会拒绝第二把锁），任务不会重复触发。另把 `cron/jobs.json` 的读取改为 `utf-8-sig`（容忍 BOM）。并修复发布工作流的 `Sign manifest` 门控：把 `RUNTIME_SIGN_PRIVATE_KEY_PEM` 提到 **job 级** `env:`，用 `if: env.RUNTIME_SIGN_PRIVATE_KEY_PEM != ''` 门控——step 级 env 变量对该 step 自己的 `if:` 不可见,这样真实发布会签名、无 secret 的运行会干净跳过。 | CN Desktop 跑的是 `hermes dashboard` 后端(没有 gateway),只能自己跑 cron。v0.17.0-cn.1:微信 iLink 断网导致 gateway 崩溃 + 桌面重启后,dashboard 恢复了,但 lifespan 没走到 cron 启动代码——scheduler 未初始化、`.tick.lock` 不存在、两个 cron 任务停摆约 14 小时且**毫无错误日志**。移植自 #46(@ytukids),并重做其 workflow 改动:原改动把 step 级 env 变量用在该 step 自己的 `if:` 里,会**静默关掉每次运行时构建的发布签名**。 | `cmd_dashboard` 启动 cron + `utf-8-sig` 读取属通用可靠性(可上游);workflow 签名门控属 CN 专有(仅 fork 发布运行时)。相关:P-021(cron 静默失效系列)、P-028。 |
| **P-030** | `tools/file_operations.py`、`tests/tools/test_search_python_fallback.py` | `search_files` 在**本地后端**且无 ripgrep 时改为进程内纯 Python 搜索（文件名用 os.walk + fnmatch，内容用逐行 regex），不再直接报错。本地后端不再 shell 出 POSIX `command -v` 探测与 `find`/`grep` 管线（它们在 Windows PowerShell 下根本跑不了），`test -e` 路径存在性检查改走本地 Python，并给 ripgrepy 的 subprocess 调用钉上 `encoding="utf-8"`（修 cp936 乱码）。回退会剪掉 vendored/缓存与隐藏目录并限制扫描文件数；对含 `\n` 的正则沿用 rg/grep 的逐行语义。远程后端 shell 路径保持不变。 | GitHub #334：Windows 上 `_has_command` 的 `command -v rg` 探测在 PowerShell 下无法执行，于是 rg/grep/find 即便装了也被判为缺失；都没装时搜索直接返回"requires ripgrep"——而 terminal 工具又禁止模型直接用 grep/rg/find，等于没有可用搜索。 | 建议上游（通用可移植性修复；P-019 把 PowerShell 设为 Windows 唯一 shell 后更必要） |
| **P-031** | `agent/agent_init.py`、`tests/agent/test_model_extra_body.py`、`website/docs/user-guide/configuring-models.md`（含 zh-Hans） | `init_agent` 通过新增的 `_merge_model_extra_body`（仿 `_merge_custom_provider_extra_body`）把主 `model.extra_body` 配置块并入 `request_overrides['extra_body']`，于是内建 provider（DeepSeek 等）也会应用用户设置的 OpenAI 兼容采样参数（`frequency_penalty`/`presence_penalty`/`top_p`）。优先级 `caller > custom_providers > model.extra_body`；走 transport 既有的 `request_overrides` 最后合并，因此也会盖过 provider profile 自带的同名键（如 DeepSeek 的 `thinking`）。 | GitHub #336：顶层 `model.extra_body` 对所有一等 provider 被静默丢弃——只有 `custom_providers` 能携带 `extra_body`，用户只能改 provider 源码（升级即丢）。 | 建议上游（通用配置缺口） |
| **P-032** | `.github/workflows/release-runtime.yml`、`hermes_cli/main.py`、`hermes_cli/colors.py`、`tests/hermes_cli/test_colors_force_color.py`（新增） | 冻结桌面 runtime 载荷内置 Node.js LTS（完整发行版含 npm/npx）与预构建 Ink TUI（`tui/dist/entry.js`）：`release-runtime.yml` 先构建 `ui-tui`，把 node 下载并 stage 进 `dist/$NAME/node` 再做 macOS normalize/签名（node 的 Mach-O 由 `sign_macos_runtime_payload.sh` 签名，Ed25519 manifest 签整个 zip 自然覆盖）；桌面端经 `HERMES_NODE`/`HERMES_TUI_DIR` 指向内置产物；`colors.py` 支持 `FORCE_COLOR` 保持冻结环境彩色输出。 | 桌面用户机器上没有 Node 时，`hermes --tui` 及一切依赖 Node 的路径不可用；现场安装 Node 慢且易失败。 | CN 桌面发布链路专属，不上游。相关：P-014/P-015、P-028/P-029。 |
| **P-033** | `tools/file_operations.py`、`tests/tools/test_file_ops_windows_inprocess.py`（新增） | `ShellFileOperations` 在**本地 Windows** 后端把磁盘 I/O 改为**进程内** Python stdlib，不再 shell-out POSIX `wc`/`sed`/`head`/`mktemp`/`cat`/`ls`：新增 `_prim_stat_size`/`_prim_read_sample`/`_prim_read_all`/`_prim_read_page`/`_prim_count_lines`/`_prim_list_dir`/`_prim_mkdirs` 磁盘原语与 `_local_atomic_write`（临时文件 + `os.replace`），由 `_use_inproc_io()`（`_IS_WINDOWS and _is_local_env()`）门控；非 Windows 本地与所有远程后端走完全不变的 shell 路径。 | P-016/P-019 之后 Windows 唯一 shell 是 PowerShell 5.1，这些 POSIX 工具不存在，文件工具在 Windows 本地后端大面积失效。 | 建议上游（Windows 正确性）。相关：P-030、P-033b、P-037。 |
| **P-034** | `gateway/status.py`、`tests/gateway/test_gateway_command_line_matcher.py` | 网关进程识别器 `_gateway_command_subcommand` 把 argv[0] basename 以 `hermes-agent-cn-runtime` 开头的命令行（冻结桌面 PyInstaller 二进制，如 `hermes-agent-cn-runtime-win32-x64.exe`）识别为 hermes CLI 入口；只加进 `has_gateway_entry`（保留真实 `gateway` 子命令解析），不进 dedicated-entrypoint 扫描（后者无条件返回 `run`，会误读冻结版的 `gateway status`/`stop`/`restart`）。 | 桌面端以 `<bin> gateway run --replace` 运行冻结 runtime，旧识别器只认 `hermes_cli.main`/`hermes`/`hermes-gateway`——`get_running_pid()` 把活网关当"非网关"，删其 `gateway.pid`/`gateway.lock`，`--replace`、防重实例锁、微信 token 锁的过期判定全部失效，多网关竞争同一 iLink 会话（#42 周期性 Session expired/未连接）。Rust 侧本就按 `"gateway run"` 子串识别，Core/Desktop 对"什么是网关"曾判断不一致。 | 以通用"冻结二进制识别"形式可上游。相关：P-014/P-015、P-016/P-019。 |
| **P-035b** | `.github/workflows/release-runtime.yml`、`tests/test_runtime_release_workflow.py` | 运行时发布构建环境门（"Verify platform backends importable"）改从插件迁移后的位置导入飞书/钉钉/企微适配器（`plugins.platforms.feishu.adapter`、`plugins.platforms.dingtalk.adapter`、`plugins.platforms.wecom.adapter` + `callback_adapter`）；weixin（微信个人号，CN 专属）仍从 `gateway.platforms.weixin` 导入。新增 pytest 回归测试在每次 CI 从 `plugins/platforms/` 加载各迁移后适配器，并断言门里不再引用被删模块。 | 上游同步（PR #57，`560010547`）把 IM 适配器从 `gateway/platforms/*.py` 迁到 bundled 插件后，CN 发布门仍 `import gateway.platforms.feishu`，所有平台的 `release-runtime.yml` 在 PyInstaller 之前就 ModuleNotFoundError——`runtime-v0.17.0-cn.3` 因此挂掉；该门只在 `runtime-v*` tag 上运行，常规 CI 从不触达，新测试把检查搬进每次 CI。 | CN 发布工具专属，不上游。相关：P-014/P-015、P-028/P-029/P-032。 |
| **P-036** | `tui_gateway/server.py`、`tests/gateway/test_provider_models_rpc.py`（新增） | 新增 `provider.models` RPC：返回服务商**完整** `/models` id 列表（probe 只采样 5 个）且**容忍空 api_key**（本地自建服务不需要密钥）；与 `provider.probe` 共用的 URL 候选抓取重构为 `_fetch_provider_model_ids`，probe 的响应逐字节不变。 | 桌面端反馈：局域网自建 Ollama（`http://192.168.31.11:11434/v1`）"测试连接"通过（走后端 probe），但模型选择器"刷新"失败——桌面直连走 SSRF 防护的 `external_request` 代理，拦非环回私网 IP 的 http。改由后端列模型（与 probe 同路径）让 LAN/自建服务商可刷新，也绕开 web 壳的浏览器 CORS。 | 可上游（P-011 `provider.probe` 的姊妹) |
| **P-037** | `tools/environments/local.py`、`tools/file_operations.py`、`tests/tools/test_local_pwsh_warnings.py`、`tests/tools/test_file_ops_p037.py`（新增） | 对 P-016/P-019/P-030/P-033 的三个 Windows 进程内 I/O 正确性收尾。**(1)** 把 `pwsh_transform`（PS7→PS5.1 降级）的调用从 `_run_powershell`（拿到的是**拼装后**的 wrapper）移到 `_wrap_command_powershell`，在用户命令被嵌入单引号 `Invoke-Expression '<cmd>'` 字面量**之前**对**原始**命令做降级。**(2)** 把 `patch_replace` 的读取、写后校验重读、以及 `_check_lint` 的磁盘读改走 `_prim_read_all`，不再直接 `cat … 2>/dev/null` shell-out。**(3)** 新增 `_decode_file_bytes` + 模块级 `_INPROC_FALLBACK_ENCODINGS`（Windows 上 = `("mbcs",)`），用于进程内整文件读取（`_prim_read_all`、`_prim_read_page`）：先试 UTF-8，再试系统 ANSI 代码页，最后才有损替换。 | **(1)** `pwsh_transform` 的 region mask 会跳过单引号字符串内容，所以对 wrapper 做降级根本碰不到用户命令——这条 PS7→PS5.1 关键兼容桥在真实执行路径上是**静默 no-op**，PS5.1 的 `Invoke-Expression` 遇到 `&&`/`||`/`??`/三元会直接 ParserError；单元测试因直接调 `pwsh_transform` 才误以为通过。**(2)** 在 PowerShell 5.1（P-016/P-019：Windows 唯一 shell）下没有 POSIX `cat`、`2>/dev/null` 会被当作写入字面文件 `\dev\null`，所以 `patch_replace` 很可能在 P-030/P-033 主打的平台上直接坏掉。**(3)** 被替换的 shell 读取按系统代码页解码；硬编码 UTF-8 会把 GBK/cp936 文件（中文 Windows 常态，本 fork 受众）的每个非 ASCII 字节变成 U+FFFD，而一次 读→patch→写 回环会把这种损坏**持久化**（静默数据丢失）。 | 三条都是通用跨平台正确性修复，应上游；紧迫性是 CN-fork 专有（P-016/P-019 让 PowerShell 5.1 成为 Windows 唯一 shell）。相关：P-016/P-019（PowerShell-only）、P-030/P-033（进程内 I/O）。 |
| **P-033b** | `tools/file_operations.py`、`tests/tools/test_file_ops_windows_inprocess.py`、`tests/tools/test_file_tools_live.py`、`tests/tools/test_file_operations.py` | 给 `write_file` 加后端无关的写后验证：`_atomic_write` 报成功后用 `_prim_read_all` 重读并与目标内容比对（剥 BOM、归一化行尾）；`_local_atomic_write` 返回前也验证文件存在且字节一致。堵住"写入器 exit 0 但字节未落盘"的静默成功窗口。 | P-033 修了 PowerShell/POSIX 不匹配，但 `write_file` 仍只信写入器退出码；剩余边缘情况（后端 FS 怪癖、竞态、管道截断）仍可能文件缺失/未变却报成功。 | 建议与 P-033 一起上游。 |
| **P-038** | `agent/lsp/client.py`、`agent/lsp/install.py`、`gateway/run.py`、`gateway/platforms/qqbot/adapter.py`、`gateway/platforms/whatsapp_cloud.py`、`hermes_cli/claw.py`、`hermes_cli/clipboard.py`、`plugins/platforms/telegram/adapter.py`、`plugins/teams_pipeline/pipeline.py`、`tools/file_operations.py`、相关 tests | 把既有 `hermes_cli/_subprocess_compat` 的 Windows creation flags 铺到剩余辅助子进程 spawn 点：LSP 服务器用 `windows_detach_flags_without_breakaway()`；ffmpeg/ffprobe 转换（QQ 机器人、WhatsApp Cloud、Teams 管线、网关音频时长探测）、网关看板 `exec_cmd`、`claw` 进程扫描、剪贴板 PowerShell、LSP 的 `npm install`（改走 `resolve_node_command`）用 `windows_hide_flags()`。另在 Windows 下把 `ShellFileOperations._exec` 的 POSIX `/dev/null` 重定向翻译为 PowerShell `*>$null`/`2>$null`/`>$null`；三个 POSIX-only 测试标记 win32 跳过。 | Windows 上这些 spawn 点要么闪控制台窗口、要么挂进父进程 console/job（父进程收场时连带被杀）——此前 `creationflags` 只在终端工具主路径生效。`/dev/null` 重定向在 PowerShell 5.1 下会生成字面 `\dev\null` 文件（或直接失败），与 P-037 的 `cat` shell-out 同类。 | 与上游本窗口的 Windows 控制台闪窗加固（如 #52340）有重叠；下次同步按"上游优先、fork 补缺"归并。相关：P-016/P-019、P-033/P-037。 |
| **P-039** | `agent/auxiliary_client.py`、`hermes_cli/config.py`（auxiliary 文案）、`hermes_cli/main.py`、`tests/agent/test_auxiliary_client.py`、`tests/agent/test_auxiliary_main_first.py` | 辅助模型 "auto" 解析**从不隐式探测** OpenRouter 与 Nous Portal。文本链：主 provider → 本地/自定义端点 → 直连 API-key 服务商 → None（`_get_provider_chain` 去掉 openrouter/nous 两级）。视觉 auto 只回退原生 Anthropic（`_VISION_AUTO_PROVIDER_ORDER = ("anthropic",)`），`_VISION_EXPLICIT_PROVIDER_ORDER` 负责显式请求与主 provider 严格后端。用户主 provider 是 OpenRouter/Nous、或显式 `auxiliary.<task>.provider` 指定时仍正常可用。 | 国内用户通常连不上 openrouter.ai / Nous Portal；隐式回退探测给每次压缩/标题生成/视觉调用平添死等超时，辅助任务"慢慢失败"而不是快速回退到可达服务商。 | CN 专属默认，不上游。2026-06-07（`bc40674ac`）落地时未编号；v0.18.0 同步中险些被合并丢弃后补登记。 |
| **P-040** | `hermes_cli/web_server.py`、`tests/hermes_cli/test_web_server_platforms_offload.py`（新增） | `/api/messaging/platforms` 的目录构建改走 `run_in_executor`（且移出 profile scope，scope 内保持无 await）；lifespan 启动时把 `_warm_platform_registry` 发进工作线程预热（与 `_warm_gateway_module` 同款）。 | `_messaging_platform_catalog()` 会触发平台插件发现，首次调用同步 import 所有内置 IM 适配器——仅 discord.py 冷启动就 10s+——而且是在 async handler 里**内联**执行。桌面端首屏就调这个端点，事件循环被卡死、所有启动期 API 排队：每次 runtime 更新后（新进程）用户会看到 15s+ 的白屏/"连接中"；Playwright E2E 对全新 v0.18.0 后端也因 15s 断言窗口而失败。 | 建议上游（官方桌面/dashboard 同样中招；与上游自己的冷启动 offload 惯例 #54448/#54523 同款）。 |
| **P-041** | `agent/agent_init.py`、`agent/conversation_loop.py`、`tui_gateway/server.py`、`apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts`、`apps/desktop/src/lib/chat-messages.ts`、`hermes_cli/config.py`、`tests/run_agent/test_tool_call_streaming_convergence.py`（新增）、`tests/tui_gateway/test_tool_call_committed_event.py`（新增）、`apps/desktop/src/lib/chat-messages.test.ts` | 修复 Windows 桌面端"工具调用后卡死"：当 `write_file` 工具调用前无正文、随后紧跟 `terminal` 工具调用时，UI 可能卡在"正在运行终端命令"无法恢复。新增显式的 `assistant.tool_calls_committed` 事件、会话级事件追踪、回合不活动看门狗、强化相邻工具调用匹配，并吞掉多余的 `None` 流式增量。 | 桌面端流状态机从 `message.start` 启动，期望收到文本增量或最终的 `message.complete`。纯工具调用的助手消息两者都不提供，导致连续工具调用时 UI 在 `tool.start` 边界上失去清晰状态切换。 | 建议上游 |
| **P-042** | `tools/environments/windows_env.py`、`tools/environments/powershell_session.py`（新增）、`tools/environments/local.py`、`hermes_cli/config.py`、`tests/tools/test_windows_env.py`、`tests/tools/test_powershell_session.py`（新增）、`tests/tools/test_local_pwsh_session.py`（新增）、`tools/file_operations.py`、`tests/tools/test_windows_perf_optimizations.py`（新增）、`tests/performance/test_windows_perf.py` | **子进程 spawn + 写路径开销（性能热点 #6）。** Windows 终端 spawn + 进程内写路径优化。**(1) 注册表刷新缓存：** `refresh_env_from_registry()`（P-020）此前在**每次** PowerShell spawn 前都重读 HKLM+HKCU 的 `Path`/`PATHEXT`，现在按两个 Environment 键的最后写入时间签名（`QueryInfoKey`）缓存——签名未变时跳过读取+`%展开%`+合并，一旦安装工具改动键的 mtime 立刻重读，因此对新装工具**没有陈旧窗口**（不像固定 TTL）。新增 `force=` 与 `_reset_registry_env_cache()`。**(2) 复用 PowerShell 会话（可选开启）：** 新增 `PowerShellSession`，通过 stdin 向同一个常驻 `powershell/pwsh -Command -` 喂多条命令（base64 包裹 + marker 终止，`try/catch` 保证 marker 必然发出），暖命令 ~1-5ms，而非一次 spawn 的 ~80-100ms。`LocalEnvironment.execute()` 在 `terminal.powershell_session_reuse`（由内部 `HERMES_PWSH_SESSION_REUSE` 环境变量桥接）开启时走会话，逐命令重置 `$LASTEXITCODE` 以与 spawn 完全一致的退出码、逐命令刷新 `$env:PATH` 以保持 P-020 语义；需要 stdin 的命令及任何失败都回退到不变的 spawn 路径。默认**关闭**（会话会在命令间保留 shell 状态）。**(3) cmd.exe 快路径（可选开启，`terminal.cmd_fast_path`）：** 一小撮无元字符的平凡内建（`dir`/`echo`/`type`/`copy`/`move`/`del`/`mkdir`/`rmdir`/`whoami`/`ver`…）改走一次性 `cmd.exe /c`（~10-20ms）而非 `powershell.exe`（~80-100ms）。`is_simple_command()` 粗分类，`_cmd_fast_path_eligible()` 严格执行门（额外拒绝任何 shell 元字符与改 cwd/env 的内建），保证路由后与 PowerShell 逐字节一致；输出按 UTF-8→系统 ANSI 代码页解码（兼容中文），退出码直取进程。默认**关闭**且只在会话路径未接手时才考虑——会话复用更快（暖 ~1-5ms）且保留 PowerShell 语义，cmd.exe 只是无状态 spawn 选项；不合格命令一律回退不变的 spawn 路径。**(4) CRC-32 写后校验：** `_local_atomic_write`（P-033）在原子 `os.replace` 前重读刚写的临时文件，用流式 CRC-32 + 字节长度与预期比对，不一致则中止且原文件不动——抓得到"大小相同但内容损坏"（同长度位翻转，调用方仅 stat 大小的 P-033b 看不到）。廉价（临时文件在页缓存，比两个 4 字节摘要而非再规范化整份内容做 `==`）。由 `_WRITE_VERIFY_CRC`（`HERMES_WRITE_VERIFY_CRC`，默认开）门控；承重的调用方 `_prim_stat_size` 校验保留，是加强而非替换。**(5) FILE_ATTRIBUTE_TEMPORARY 提示（可选开启）：** `set_file_temporary()`/`mark_as_temporary()`（windows_env.py）给临时文件打 temporary 属性；由 `_MARK_TEMP_FILES`（`HERMES_MARK_TEMP_FILES`，默认关）接入 `_local_atomic_write`：创建后打标、**改名前清标**，落地的永久文件绝不残留 temporary。非 Windows 无操作。 | 每次 Windows PowerShell spawn 在终端热路径上要付 ~31-100ms+ 的进程创建 + DLL 加载 + 解释器初始化（root-cause-analysis.md 热点 #6）；P-020 的注册表刷新又给每次调用加了 ~5-15ms 且无缓存。缓存刷新 + 复用解释器可在暖路径上去掉这两项。 | 注册表刷新缓存是通用 Windows 修复，应上游；会话复用是 Windows-shell 专有（建立在 P-016/P-019 的 PowerShell-only 之上），但模式可推广。相关：P-016/P-019（Windows 唯一 shell；含 pwsh 检测）、P-020（注册表 PATH 刷新）。 |
| **P-043** | `model_tools.py`、`tools/registry.py`、`run_agent.py`、`cli.py`、`tests/performance/test_tool_dispatch.py`、`tests/tools/test_registry_schema_json.py`（新增） | **首次分发延迟（性能热点 #8/#9）。** 把 ~4,486ms 的冷启动首次分发开销挪出用户可见热路径。新增 `warm_dispatch_path()`——一个幂等、线程安全、发射即忘的预热原语：完成延迟发现、为某个工具集选择构建并缓存 schema 目录、并预序列化每个工具的 schema。接入 `AIAgent.warmup()`/`awarmup()` 以及 CLI 横幅空闲期预热（现统一走该原语）。新增 `registry.get_schema_json()`——按条目惰性计算的 JSON 缓存（无导入期成本，重新注册即失效）。 | 首次工具分发/首次 API 请求会同步触发完整工具发现（模块导入 + `check_fn` 探测 + schema 组装）——冷 ~4,486ms vs 暖 ~2ms，导致第一次工具调用像卡死（root-cause-analysis.md 热点 #8/#9）。 | 预热原语 + 惰性 schema-JSON 缓存是通用的，应上游；冷启动量级为 Windows/py3.14 专有。相关：P-040（冷启动卸载）、P-042（子进程 spawn 开销）。 |
| **P-044** | `platform_utils.py`（新增）、`hermes_cli/config.py`、`hermes_cli/dep_ensure.py`、`tools/code_execution_tool.py`、`tools/environments/powershell_session.py`、`tools/environments/local.py`、`tools/process_registry.py`、`plugins/platforms/whatsapp/adapter.py`、`agent/prompt_builder.py`、`agent/ssl_guard.py`、`tools/environments/windows_env.py`、`hermes_cli/doctor.py`、`scripts/precompile.py`（新增）、`pyproject.toml`、`tests/tools/test_wmi_ssl_windows_overhead.py`（新增）、`tests/agent/test_ssl_ca_guard.py` | **agent 初始化期的 WMI + SSL + import 开销（.plans/15，热点 `_wmi.exec_query` 2.91% / `_ssl.set_default_verify_paths` 8.19%）。** **(1) WMI：** Python 3.12+ 上 `platform.system()`/`platform.release()` 会构造 `platform.uname()`，后者在 Windows 上发起 `_wmi.exec_query`（`win32_ver` + `_get_machine_win32`，~45ms）。多个模块在**模块作用域**执行 `_IS_WINDOWS = platform.system() == "Windows"`，导入级联里 WMI 探测被付了两次，`prompt_builder` 的 `platform.release()` 每次初始化构建 system prompt 时又付一次。新的无 WMI `platform_utils`（`is_windows()` 走 `sys.platform` 常量、`windows_release()` 走 `sys.getwindowsversion()`）替换模块级标志与主机行；导入级联与 prompt 构建现在 **0** 次 WMI 查询（已验证）。**(2) SSL：** `verify_ca_bundle()`（每次 `AIAgent` 构造都跑，agent_init.py）每次重建一次性 `ssl.create_default_context()`（Windows 上 ~225ms）；现在按 CA 环境变量 + certifi bundle（path/size/mtime）的廉价指纹缓存成功结论，未变时每个进程只校验一次（重复 ~0.05ms），任何变更都会重新校验并重新抛出。**(3) import：** `scripts/precompile.py`（`compileall`）把 `.pyc` 预热移出热路径（源码运行/CI/更新后），首次 import 不再付 `builtins.compile` + `_io.open_code` + Defender 扫描新写的 `.pyc`。**(4) Defender 提示：** `suggest_defender_exclusion()` 经 `hermes doctor` 给出 HERMES_HOME 排除建议（仅提示，不改任何设置）。 | Windows/Python 3.12+ 上无害的 `platform.system()`/`release()` 惯用法会悄悄访问 WMI 服务（每次冷调 ~40-90ms），而 SSL guard 每次构造 agent 都重载 CA 存储——网关批量起 agent/subagent 时两者都被重复支付。计划的字面 `import wmi` / `pywin32` 前提不成立（本树没有 `wmi` 包）；真正的 WMI 来源是 stdlib `platform` 模块，因此修复是移除 WMI 触发点而非懒加载不存在的依赖。 | `platform_utils` 与 ssl_guard 记忆化是 provider/OS 通用改进，应上游（处处正确；WMI *成本*是 py3.12+ Windows 专属）。相关：P-042（Windows 子进程 spawn 开销）、P-043（首次分发延迟）。 |
| **P-045** | `import_accelerator.py`（新增）、`hermes_bootstrap.py`、`run_agent.py`、`scripts/precompile.py`、`pyproject.toml`、`agent/message_utils.py`、`agent/agent_runtime_helpers.py`、`tools/registry.py`、`tests/test_import_accelerator.py`（新增）、`tests/test_precompile.py`（新增）、`tests/agent/test_message_utils.py` | **Flame-graph import 系统优化（.plans/16，import 系统约占冷启动 agent-init 的 71%）。** **(1) 一方 import 加速器：** `sys.meta_path` finder（`import_accelerator`）用一次 dict 查找解析 Hermes 自身*精选*的顶层模块/包（集合镜像 pyproject `py-modules` + `packages.find`），跳过逐条 `sys.path` 目录扫描（`nt.stat` / `nt._path_exists`）。包优先于模块（仓库根 `agent.py` harness 永不遮蔽真正的 `agent/` 包）、`.pyc` 保留的 `spec_from_file_location`、其他名字 O(1) 透传、`HERMES_DISABLE_IMPORT_ACCELERATOR` 可关。从 `hermes_bootstrap`（每个入口的第一个 import）安装，并从 `run_agent` 幂等安装。映射构建期校验一次，不做逐 resolve 的 re-stat，因此绝不拖慢暖树（实测中性到更快）。**(2) precompile 幂等：** `scripts/precompile.py` 新增 `precompile_if_needed`（按源码+解释器指纹盖章）、`precompile_in_background`（daemon 线程）、`[tool.hermes.precompile]` 目标读取与 `--if-needed`/`--force`；可选的 `HERMES_PRECOMPILE_ON_START` 后台预热（默认关，pytest/冻结下 no-op）把 `builtins.compile`（冷启动 ~10.82%）移出源码运行布局的热路径。**(3) 每请求微优化：** `sanitize_api_messages`（每次 LLM 调用前都跑）把每个 tool_call 的两次类型分发合并为一次 `get_tool_call_function_and_id`；`registry.get_definitions` 只在短暂锁内快照*请求的*条目，不再每次物化整个 ~250 工具注册表。 | import 系统约占冷启动 agent-init 的 71%。计划字面的 `type()` 换 `isinstance` 被基准否定（成本相同；组合形式更慢），其"34.4 万次 isinstance 调用"是第三方**import 期**类构造（pydantic/SDK），对策是**不急切导入**（懒代理 + 懒工具索引 + 本加速器），而非改写我们自己的 isinstance 调用。因此修复打在真正的杠杆上：跳过冗余路径扫描、预编译字节码、砍掉真正冗余的每请求分发。 | import 加速器、precompile 幂等与 sanitizer/registry 微优化 OS 通用，应上游；冷启动*量级*是 Windows/源码运行专属。相关：P-043（首次分发延迟）、P-044（WMI/SSL/import 开销）。 |
| **P-046** | `tui_gateway/server.py`、`tests/gateway/test_provider_models_rpc.py` | `provider.probe` / `provider.models` 新增可选 `api_mode` 参数：`"anthropic_messages"` 时共享的 `_fetch_provider_model_ids` 切换到 Anthropic 协议——URL 候选镜像 SDK 自动追加 `/v1/messages` 的规则（裸域名 → `{base}/v1/models`，嵌套路径回退 host 根），鉴权改用 `x-api-key` + `anthropic-version`（对齐 `hermes_cli.models.probe_api_models`）而非 `Authorization: Bearer`；未知/缺省 `api_mode` 行为逐字节不变。 | CN 桌面端供应商目录新增了一批 Anthropic 协议的 Claude Code 中转站（PackyCode、AICodeMirror 等）与 MiniMax `/anthropic` 端点；它们的「测试连接」走 OpenAI 风格探测（Bearer + `/models` 启发式），严格的 Anthropic 网关会拒绝——有效密钥被误报为鉴权失败。 | 或可上游（P-011/P-036 的姊妹） |
| **P-047** | `tui_gateway/cli_delegation.py`（新增）、`tui_gateway/server.py`、`skills/autonomous-ai-agents/claude-code/SKILL.md`、`skills/autonomous-ai-agents/codex/SKILL.md`、`tests/tui_gateway/test_cli_delegation_classifier.py`（新增）、`tests/tui_gateway/test_cli_delegation_events.py`（新增）、`tests/tui_gateway/test_cli_delegation_stream.py`（新增） | 新增三个 gateway 事件——`delegation.cli.started` / `delegation.cli.output` / `delegation.cli.completed`——识别经 `terminal` 工具委派给外部编码代理 CLI 的调用（Claude Code `claude -p …` / Codex `codex exec …`）。纯函数命令分类器逐层剥壳 `cd X &&`、env 前缀、`timeout`、`bash -lc`、管道，排除 tmux/ssh 与运维调用（`--version`、`claude mcp`、`codex login`），提取 mode/prompt/workdir/flags。`delegation_id == tool_call_id`，客户端据此把既有工具卡「升级」而非双渲染。后台委派在既有 `process_registry.on_output` sink 里加第二个消费者，watcher 线程 500ms 合并冲刷实时输出（ANSI 剥离、`redact_sensitive_text(force=True)`、单次 4KB / 每委派 256KB 上限），进程退出映射为 `completed/failed/killed/lost`，并解析 Claude `--output-format json\|stream-json` / Codex `--json` 结果（`session_id`、`num_turns`、`total_cost_usd`）。技能升版（claude-code 2.3.0、codex 1.1.0）引导委派走 `background=true` + 结构化输出。显式非目标：tmux 交互式委派不跟踪；`process submit` 输入不回显。 | CN 桌面端要可视化 hermes 对 Claude Code / Codex 的多 Agent 委派（"我的代理的子代理现在在干什么"）。技能把所有委派都汇入 `terminal` 工具且没有任何结构化标记，UI 只能对 80 字符截断预览做模式匹配——前台调用完成前更是零输出。gateway 是唯一能统一分类、关联并推流的收口点。 | CN 桌面端驱动，但事件本身与客户端无关；待上游桌面端有委派视图后或可上游。分类器 fixture 与 Hermes-CN-Desktop `web/src/lib/cli-delegation.test.ts` 字面镜像——改一处必改两处。 |

| **P-048** | `pyproject.toml`、`uv.lock`、`FORK_NOTES.md` | **要求 Python ≥3.14** — 将 `requires-python` 从 `>=3.11,<3.14` 更新为 `>=3.14`。上调 `pywinpty` 上限至 `<4`（v3.0.5 包含 cp314 wheels）；`uv lock` 升级 `pywin32` v311→v312（包含 cp314 wheels）。修复下游测试/代码不兼容：(1) Python 3.14 的 tomllib 拒绝 TOML 基础字符串中未转义的 `\U`/`\T` 等——修复 nemo_relay 测试使用 `.as_posix()` 生成正斜杠 Windows 路径；(2) `shlex.split(cmd, posix=True)` 在 Windows 上吃掉反斜杠——在 disk-cleanup 插件路径提取中增加 `posix=False` 通道；(3) Windows 上 `os.path.expanduser` 读取 `USERPROFILE` 而非 `HOME`——修复波浪号展开测试；(4) `signal.SIGKILL` / `socket.AF_UNIX` / `os.uname` 在 Windows 上不存在——添加 `pytest.mark.skipif` 标记；(5) 子进程探测字符串缺少 `orjson`——添加 import。`uv sync` + `uv build` 已在 Windows Python 3.14.3 上通过验证。相关测试文件 274 通过、6 跳过。 | Python 3.11/3.12/3.13 已在本 fork 达到生命周期终点；上游仍支持 ≥3.11。tomllib 反斜杠问题是 py3.14+ 的严格性变更；shlex 吃反斜杠问题在旧 Python 上也存在但被 Git-Bash 掩盖（P-019 已移除）。 | `requires-python` 调整属 fork 策略，不与上游兼容。各单项 bug 修复（TOML f-string、shlex posix 标志、USERPROFILE 回退）属通用改进，应单独上游化。 |
| **P-049** | `tools/terminal_post_process.py`（新增）、`tools/terminal_command_rewrite.py`（新增）、`tools/rtk_provision.py`（新增）、`tools/terminal_tool.py`、`hermes_constants.py`、`hermes_cli/dep_ensure.py`、`scripts/install.ps1`、`scripts/install.sh`、`scripts/install_coreutils.py`、`tests/tools/test_terminal_post_process.py`（新增）、`tools/file_operations.py`、`tools/tirith_security.py`、`hermes_cli/commands.py`、`tests/tools/test_file_operations.py`、`tests/tools/test_search_error_guard.py`、`tests/tools/test_search_hidden_dirs.py`、`tests/tools/test_tirith_security.py` | **终端输出后处理管线 + rtk (reasoning toolkit) 集成。** (1) 新增 `terminal_post_process.py`：多阶段管线——ANSI 剥离 + `
`→`
` 归一化、重复行去重（单行+多行块模式、可配阈值）、基于行数的头/尾截断加折叠标记、超长输出导出到会话文件、YAML 风格元数据块组装。(2) 新增 `rtk_provision.py`：`rtk` 二进制的运行时检测与路径解析（仿 `_find_rg()` 模式），依次查找 managed tools 目录 → 旧版 `$HERMES_HOME/bin` → PATH，带 `functools.lru_cache`。(3) 新增 `terminal_command_rewrite.py`：Shell 命令感知的重写，为已知的高输出命令（`git`、`cargo`、`npm`、`ls`、`grep`、`cat`、`python`、`docker`、PowerShell cmdlet 等）前置 `rtk`，正确解析 `;`/`&&`/`||`/`|` 分割，尊重引号和 subshell。(4) `terminal_tool.py` 新增 `token_kill`（默认 True）和 `max_lines` 参数；执行前可重写命令通过 rtk，执行后走完整后处理管线（替换了旧的内联 ANSI 剥离 + 字符级截断）。(5) `hermes_constants.py`：新增 `get_managed_tools_dir()` 返回 `<HERMES_HOME>/tools`（兼容旧版 `<HERMES_HOME>/bin` 兜底）。(6) `dep_ensure.py`：将 `rtk` 加入 `_DEP_CHECKS`/`_DEP_DESCRIPTIONS`；重构 `_find_rg()` 和 coreutils 检查使用 `get_managed_tools_dir()` 加旧版兜底。(7) `file_operations.py`/`commands.py`：使用 `_find_rg()` 而非裸 `shutil.which("rg")`，优先使用托管副本。(8) `tirith_security.py`：将自动安装目标从旧版 `$HERMES_HOME/bin/tirith` 迁移到 `get_managed_tools_dir()`，保留向后兼容的 PATH 兜底。(9) `scripts/install.ps1`/`install.sh`：增加 rtk 下载和 `hermes doctor` 检查。(10) `scripts/install_coreutils.py`：使用 `get_managed_tools_dir()` 获取管理工具路径。 | 旧的终端输出处理仅仅是一段 inline ANSI 剥离 + 硬编码的 40%/60% 字符级截断，没有去重、没有行级截断、模型也无法控制输出行数。像 `git log`、`cargo test`、`docker ps`、`npm install` 这样的命令可能产生成千上万行重复输出——模型为每一行重复内容买单。rtk 是一个外部 CLI，能在数据到达 agent 之前原生折叠重复行；后处理管线在 rtk 不可用或禁用时提供第二道防线（去重 + 行截断）。新的 `max_lines` 参数让模型可以指定行数（头+尾加折叠标记），比旧的字节级截断更直观。`get_managed_tools_dir()` 的整合把外部二进制移到了 `<HERMES_HOME>/tools/`（从通用的 `bin/` 迁出），避免污染 PATH 且更易管理。 | 建议上游（通用终端输出品质改进；去重/截断/导出管线是纯 Python 无外部依赖；managed-tools-dir 模式是通用维护改进）。 |

| **P-050 / P-052 / P-054** | `tools/environments/local.py`、`tools/terminal_tool.py`、`agent/prompt_builder.py`、`hermes_cli/config.py`、`hermes_cli/gateway.py`、`tools/environments/base.py`、`scripts/keystroke_diagnostic.py`、`apps/desktop/electron/main.ts`、`apps/desktop/electron/windows-hermes-resolution.test.ts`、`tests/tools/test_shell_resolution.py`、`tests/tools/test_terminal_dynamic_description.py`、`tests/tools/test_local_git_bash_port.py`（新增）、README 文件、网站文档、`skills/autonomous-ai-agents/hermes-agent/SKILL.md`、`FORK_NOTES*.md` | **重新开放 `HERMES_SHELL_TYPE=bash` 作为可选的显式 Windows shell（仅限预装 Git Bash、不自动下载），并逐步加固。** P-050 重新允许 Windows 上的 `HERMES_SHELL_TYPE=bash`：`_resolve_shell()` 用 `_find_bash_posix()` 查找预装 bash 而非抛错；恢复 `_WINDOWS_BASH_SHELL_HINT` 与 bash 分发分支；两个 PowerShell 提示从 5 条扩到 14 条（Verb-Noun cmdlet、.NET 管道、比较/逻辑运算符、字符串引号、splatting、`$LASTEXITCODE`、反引号规避）；桌面端 `findGitBash()` 简化（移除 PortableGit 自动下载候选），预检按配置条件检查 bash（`shell:bash`）或 PowerShell（默认）；README/文档更新（PowerShell 默认，Git Bash 可选 opt-in）。P-052 移植 kimi `bash_tool` 的 win32 对等实现：win32 门控 `fix_bash_command`（POSIX 主机逐字节空操作）、git.exe 发现链（`where.exe git` / `git --exec-path`）作为 `_find_bash` 的**最后**候选源、`_is_git_bash_install` 标记 + `_with_msystem_neutralized`（`export MSYSTEM=; `）让子进程看到空 MSYSTEM 而非 `MINGW64`、`_encode_startup_script`（base64+gzip，为将来交互式 `bash -i` 备好）、macOS bash 候选、以及 `_run_bash` 启动解析出的 `self._shell_path`。P-054 让 Git Bash 缺失/损坏时**回退**到 PowerShell 链路（pwsh → powershell.exe）并告警，不再启动失败：`_resolve_shell()` 的 bash 分支不再让 `_find_bash()` 的 `RuntimeError` 外传；`_detect_shell_for_description()` 仅在真正找到可用 Git Bash 时报告 `"bash"`；桌面预检（`ensureRuntime`）改为告警而非抛错。 | P-019 让 PowerShell 5.1 成为 Windows 唯一 shell 并禁止 `HERMES_SHELL_TYPE=bash`，但本 fork 用户可能仍为 VCS 操作安装 Git for Windows，且部分工作流合法需要 POSIX 语法；以显式 opt-in（不自动下载）重新开放 bash 可在不重引自动安装复杂度的前提下恢复灵活性。P-052 修复了该路径上的 MSYSTEM `MINGW64` 平台误判与漏掉 per-user/choco/scoop/并排安装的问题。P-054 确保过期的 `shell: bash` 配置或未安装/被策略禁用的 Git 优雅降级到始终可用的 PowerShell，而不是卡死整个 terminal 工具。 | 建议上游（用户选择权、无自动下载风险；优雅降级是通用能力） |
| **P-051** | 全仓库——shipped 代码中 269 处文本模式 `subprocess.run/Popen/call/check_call/check_output` 调用点（`agent/coding_context.py`、`agent/system_prompt.py`、`hermes_cli/web_server.py`、`tools/tts_tool.py`、`tools/transcription_tools.py`、`hermes_cli/_subprocess_compat.py`、…）、`tests/test_subprocess_text_pipe_decoding.py`（新增） | **给每一条文本模式子进程管道钉死显式解码——消灭 GBK `_readerthread` UnicodeDecodeError 这一类 bug。** 按约定输出 UTF-8 的子进程（git、gh、rg、python/pip/uv、node/npm、docker、ssh/scp、systemctl、tmux、cosign、codex/claude CLI、hermes 自身、…）钉 `encoding="utf-8", errors="replace"`；输出 OEM/ANSI 代码页的 Windows 原生工具（`tasklist`/`taskkill`/`netstat`/`where`）只钉 `errors="replace"`；三处 `**kwargs` dict 展开点（`_fs_git_branch`、`_run_command_tts`、`_run_command_stt`）在 dict 内补同样钉法；`_subprocess_compat.py` 的 drop-in wrapper 缺 `import subprocess` 的潜在 `NameError` 一并修复。 | zh-CN Windows（cp936、py3.14 无 UTF-8 模式）上 `subprocess.run(..., text=True)` 用 `locale.getpreferredencoding(False)` 包子进程管道；一个 GBK 非法字节（如实 = `E5 AE 9E` 的中间字节 `0xAE`）就会杀死 CPython 的 daemon `_readerthread`，调用方拿到 `stdout is None` 且无任何异常——会话开始的 workspace 快照因此从 system prompt 里静默消失。 | 应上游——逐点钉法与新的 AST 不变式回归测试 OS 无关。相关：c210b9621（slash worker 管道钉法）、P-042、P-019。 |
| **P-053** | `tests/` —— 170 个文件（8 个整文件删除、162 个文件编辑） | **测试套件卫生清理：移除 ad-hoc / magic-mock / 防 LLM 幻觉测试。** 基于仓库自身对 change-detector 测试的禁令（AGENTS.md「Don't write change-detector tests」），通过子代理审查对 1,203 个候选测试文件（63 个强信号 + 39 个中等信号分块）进行三分类：删除 8 个整文件（复制生产逻辑的自我指涉测试、只测第三方库的测试、纯 schema/enum 字面量钉死文件）以及 **162 个文件中的 546 个测试**——包括 change-detector 字面量钉死（模型列表、工具数量、schema 形状、版本/常量冻结）、magic-mock 调用签名钉死、mock 自指同义反复、`inspect.getsource`/AST 源码形状钉死、内联重写生产逻辑而从不调用真实代码的测试、以及批量凑覆盖率的冒烟测试。共删除约 10,467 行；保留 1,033 个文件（行为 / 回归 / 不变量 / E2E 测试）。 | 测试套件积累了一类测试，其唯一目的是冻结当前值或代码形状，防止编辑代码库的 LLM 静默改动它们。这类测试在任何有意改动时都会失败、徒增维护噪音、且从不验证行为——与仓库「Behavior contracts over snapshots」的原则背道而驰。 | 测试质量清理，非行为补丁——删除标准与 AGENTS.md 自身指引一致，可为上游测试卫生提供参考 |
| **P-055** | `tools/runtime_compat.py`（新增）、`gateway/platforms/webhook_filters.py`、`gateway/run.py`、`gateway/slash_commands.py`、`hermes_cli/main.py`、`hermes_cli/gateway.py`、`hermes_cli/gateway_windows.py`、`hermes_cli/kanban_db.py`、`hermes_cli/relaunch.py`、`hermes_cli/web_server.py`、`hermes_cli/uninstall.py`、`hermes_cli/profiles.py`、`hermes_cli/tools_config.py`、`hermes_cli/codex_runtime_plugin_migration.py`、`tui_gateway/server.py`、`tui_gateway/host_supervisor.py`、`tools/todo_tool.py`、`tools/tts_tool.py`、`tools/mcp_tool.py`、`tools/code_execution_tool.py`、`tools/lazy_deps.py`、`plugins/google_meet/*`、`plugins/memory/{hindsight,mem0}/*`、`skills/productivity/google-workspace/scripts/setup.py`、`tests/` | **把每一个"把 python 当解释器 spawn"的调用点收口到冻结运行时咽喉。** CN 便携桌面 runtime 是 PyInstaller 冻结的 exe，没有独立的 `python.exe`——内部 `sys.executable` 就是 Hermes CLI 本体，`[sys.executable, script.py]` / `[sys.executable, "-m", mod]` / `[sys.executable, "-c", code]` 会变成 `hermes <arg>` 并以 argparse "invalid choice" 死掉。新 `tools/runtime_compat.py` 集中提供 `is_frozen_runtime()`、`hermes_cli_argv()`（冻结下丢掉 `-m hermes_cli.main` 前缀）与 `run_python_script_in_process()`（进程内 `runpy` 运行器，带超时/stdin 捕获，泛化 cron 修复）。每个生产 spawn 点现在：(1) 经 `hermes_cli_argv` 重新调用 CLI（kanban dispatcher、relaunch、dashboard reexec、web-server 分离动作、uninstall、gateway run/restart argv 构造、`gateway_windows` cmd/vbs/argv 构造）；(2) 冻结时进程内运行可信脚本（webhook 路由脚本、todo 校验、NeuTTS 合成、Piper 语音下载）；(3) 需要真 python 时跳过或明确拒绝（MCP stdio watchdog wrapper、execute_code 沙箱、Google Meet bot、Codex 迁移、uv/pip 懒安装、`hermes update`）；(4) TUI slash-worker / compute-host / gateway restart-watch / update-gateway-helper 改走新的隐藏 CLI 子命令（`__slash-worker`、`__compute-host`、`__gateway-restart-watch`、`__update-gateway-helper`）。 | 与 cron 修复同根因（`#whitecat_inspect`）：冻结桌面 runtime 没有独立 python，所有假设 `sys.executable` 是解释器的子进程点在桌面端全挂。只修 cron 会留下 ~30 个同类调用点继续坏。 | 打包 CN 专属（上游不发冻结 exe）；`hermes_cli_argv`/进程内运行器模式通用，可作为 compat helper 上游。 |
| **P-056** | `agent/prompt_builder.py`、`tests/agent/test_prompt_builder.py`、`tests/agent/test_system_prompt.py`、`tests/agent/test_platform_hint_desktop.py` | **压缩并精简 `prompt_builder.py` 中的核心 system prompt 指引。** `HERMES_AGENT_INTRODUCTION`、`HERMES_AGENT_HELP_GUIDANCE`、`MEMORY_GUIDANCE`、`SESSION_SEARCH_GUIDANCE`、`SKILLS_GUIDANCE`、`KANBAN_GUIDANCE`、`TOOL_USE_ENFORCEMENT_GUIDANCE`、`TASK_COMPLETION_GUIDANCE`、`PARALLEL_TOOL_CALL_GUIDANCE`、`OPENAI_MODEL_EXECUTION_GUIDANCE`、`GOOGLE_MODEL_OPERATIONAL_GUIDANCE`、`STEER_CHANNEL_NOTE`、`computer_use_guidance`、`PLATFORM_HINTS` 以及 Windows PowerShell/pwsh shell hint 全部重写为简洁、指令式表述，同时保留原有行为契约。移除冗长的 XML 标签骨架、长篇示例和重复解释；消息平台共用 `_MEDIA_DELIVERY_HINT`。测试从字面短语检查改为行为不变量断言（标题、核心概念），避免微调措辞即失败。 | 过长的 system prompt 消耗 token 并稀释模型注意力；更短的指令在保持同样规则（用行动代替计划、验证、并行独立调用、事实用工具、不伪造、平台媒体投递、PowerShell 语法）的前提下提升信噪比。 | 建议上游（通用提示词质量 / token 效率改进） |
| **P-057** | `tools/delegate_tool.py`、`tools/session_search_tool.py`、`tools/code_execution_tool.py`、`tools/terminal_tool.py`、`tools/skill_manager_tool.py`、`tools/memory_tool.py`、`tools/todo_tool.py`、`tools/clarify_tool.py`、`tools/browser_tool.py`、`tools/file_tools.py`、`tools/process_registry.py`、`tools/vision_tools.py`、`tools/tts_tool.py`、`tools/skills_tool.py`、`tools/agent_swarm.py`、`tools/web_tools.py`、`agent/coding_context.py`、`agent/prompt_builder.py` | **第二轮：压缩工具 schema 描述与剩余 system-prompt 块以节省 token。** 工具描述是每次 API 调用最大的固定成本（每个工具都随每次调用发送）——默认 28 个核心工具的总 schema 从 47,535 字符降至 ~29,964（降 37%；描述 25,237→13,330，参数 22,298→16,634）。重点：delegate_task 4002→1458、session_search 3547→1246、execute_code 2454→1559、terminal 2398→1364、skill_manage 1800→1001、memory 1476→884、todo 1299→760、clarify 1185→745；browser/file/vision/process/tts/skills/web 各 −40%。保留所有影响行为的关键事实：调用形态、动态限额（delegate 并发/嵌套深度从配置渲染）、必填参数、何时不用及替代工具、PowerShell/bash 命令映射。system-prompt 块：`CODING_AGENT_GUIDANCE` 2626→1387、`KANBAN_GUIDANCE` 5481→2798、`computer_use_guidance` 815→581、`PLATFORM_HINTS["yuanbao"]` 1072→667。未改任何 schema 结构、handler、函数名或测试文件；动态 schema 机制（delegate_task/execute_code/terminal）保持完好。验证：定向套件全绿（856 passed），完整 `tests/tools` 3,672 passed（4 个 MSYS pathconv 环境类失败在纯净基线同样失败），ruff 干净。 | 延续 P-056：指引常量之后，工具描述是下一个 token 杠杆——每次 API 调用都会重发全部模型工具 schema，减 37% 字符直接降成本并提升信噪比。 | 建议上游（通用 token 效率改进，无 CN 专属行为） |

### P-050 / P-052 / P-054：Git Bash 重新开放为显式 Windows opt-in，并逐步加固

**现象 / 需求。** P-019 让 Windows PowerShell 5.1 成为唯一受支持的 Windows shell，并禁止 `HERMES_SHELL_TYPE=bash`。但本 fork 用户可能仍为 VCS 操作安装 Git for Windows，且部分工作流合法需要 POSIX shell 语法。以显式 opt-in（仅预装、不自动下载）重新允许 bash 可恢复灵活性，而不重引自动安装复杂度或 PortableGit 下载。然而 P-019 时代 Git Bash 缺失（或无法启动——Mandatory-ASLR 失败类）时的硬 `RuntimeError`，意味着过期的 `terminal.shell: bash` 配置、未安装 Git for Windows、或被策略禁用的 Git Bash 会直接卡死整个 terminal 工具——尽管 PowerShell 5.1 随每个 Windows 系统自带，且 `auto` 模式本就使用 pwsh → powershell.exe 链路。另外，在重新开放的 bash 路径上，一次性 `bash -c` 的子进程会误判平台（`MSYSTEM=MINGW64`）、Git Bash 发现会漏掉 per-user/choco/scoop/并排安装、`fix_bash_command` 在 POSIX 主机上无条件运行、且 `_run_bash` 可能启动一个与 `_resolve_shell` 所选不同的 bash。

**P-050 做了什么**（重新开放 `HERMES_SHELL_TYPE=bash` 为显式 opt-in）：

1. **`tools/environments/local.py`** — `_resolve_shell()` 用 `_find_bash_posix()` 查找预装 bash，不再抛 `RuntimeError`。
2. **`agent/prompt_builder.py`** — 保留 `_WINDOWS_BASH_SHELL_HINT` 与 `bash` 分发分支；`_WINDOWS_POWERSHELL_SHELL_HINT` 更新为提及 `pwsh_transform` 的自动降级；两个提示从 5 条扩到 14 条（Verb-Noun cmdlet、.NET 管道、比较/逻辑运算符、字符串引号、splatting、`$LASTEXITCODE`、反引号规避）。
3. **`apps/desktop/electron/main.ts`** — `findGitBash()` 简化（移除 PortableGit 自动下载候选）；预检按配置条件检查 bash（`shell:bash`）或 PowerShell（默认）。
4. **测试** — `test_windows_bash_found_returns_bash` 与 `test_windows_bash_not_found_raises_helpful_error` 替代旧的 `test_windows_bash_raises_runtime_error`。
5. **文档** — 所有 README 与网站文档把 PowerShell 描述为默认 shell、Git Bash 为可选 opt-in。跨平台测试修复：`TestCwdHandling` 先查原始 `docker_cwd_source`、`TestExtractImageRefs` 正则扩展支持 Windows 盘符路径 + `os.path.normpath`、3 个测试文件的平台无关路径断言、易闪 Windows 测试标 `xfail(strict=False)`。

**P-052 做了什么**（kimi `bash_tool` win32 对等移植，全部在 `tools/environments/local.py`）：

1. **调用处 win32 门控** — `_wrap_command` 在 `fix_bash_command` 前检查 `sys.platform == "win32"`（POSIX 主机逐字节空操作）；仅当修复器真正改动命令时才记录 `_bash_fix_warnings`。
2. **git.exe 发现链** — `_where_git_executables`（`where.exe git`）、`_git_bash_candidate_from_git_path`（`<gitDir>/../bin/bash.exe`）、`_git_exec_path`（带超时 `git --exec-path`）、`_git_install_root_from_exec_path`（从 `mingw*/libexec/git-core` 上溯）、`_git_bash_candidates_from_exec_path`；接入 `_find_bash` 作为**最后**候选源（env 覆盖 → 托管便携 Git → 已知位置 → PATH 之后）；`where.exe`/`git` 子进程用 `windows_hide_flags()`。
3. **MSYSTEM 中和** — `_is_git_bash_install`（盘符锚定 `<root>/cmd/git.exe` 标记；`bin/bash.exe` 与 `usr/bin/bash.exe` 两种布局；真实 MSYS2 安装永不匹配）+ `_MSYSTEM_NEUTRALIZE_PREFIX = "export MSYSTEM=; "` + `_with_msystem_neutralized`，在 `_wrap_command` 的 win32 分支、bash-fix 之后逐命令应用，让子进程即使快照导出 `MINGW64` 也看到空 `MSYSTEM`。
4. **`_encode_startup_script`** — base64+gzip 自解码单行封装（stdlib `base64`/`gzip`；与 kimi 的 `pybase64` 输出一致）。暂无生产调用者——它是交互式 `bash -i` 引导集的三分之一，为将来的交互式 Git Bash 模式备好。
5. **macOS bash 候选** — `_bash_candidates_macos`（`/opt/homebrew`、`/usr/local`、`/opt/local`）、`_bash_candidates_system`、`_git_bash_for_macos`；`_find_bash_posix` 增加 darwin 分支，优先新 Homebrew/MacPorts bash 而非系统 bash 3.2。Linux 顺序不变。
6. **一致性修复** — `_run_bash` 的 bash 分支启动 `self._shell_path`（环境初始化时解析出的 Git Bash，已含 git.exe 链），不再重新 `_find_bash_posix()`，保证 `init_session` 与实际执行用同一个二进制。

**P-054 做了什么**（Git Bash 缺失/损坏 → 优雅回退 PowerShell）：

1. **`tools/environments/local.py` — `_resolve_shell()`。** `bash` 分支用 `try/except RuntimeError` 包住 `_find_bash()`（Git Bash 未找到、或 ASLR 修复提示类），并通过 `_bash_starts()` 缓存复查把"候选存在但探针全失败"也判为不可用。bash 不可用时记录携带原因的 warning，返回与 `auto` 相同的 `pwsh` → `powershell.exe` 链路。`_find_bash()` 本身不变（对需要硬错误的调用者仍抛错，例如 `_git_bash_bin_dirs` 的 try/except 路径）。
2. **`tools/terminal_tool.py` — `_detect_shell_for_description()`。** bash 模式仅在 `_IS_WINDOWS` 为真 且 `_find_bash()` 返回的路径通过 `_bash_starts()` 时才报告 `"bash"`；否则 `"powershell"`（terminal 实际回退到的 shell）。探测以真实 OS 标志门控，mock `platform.system()` 不会触发真实 bash 搜索。旧的 P-019 过期注释已删除。
3. **`apps/desktop/electron/main.ts` — 预检。** `HERMES_SHELL_TYPE=bash` 且 `findGitBash()` 失败时，`ensureRuntime` 改为 `console.warn` 并继续走 PowerShell 可用性校验（与核心回退一致），不再抛错。
4. **测试。** `test_shell_resolution.py`：`test_windows_bash_found_returns_bash` 增加 `_bash_starts=True` mock；旧的 `test_windows_bash_not_found_raises_helpful_error` 被 `test_windows_bash_missing_falls_back_to_pwsh`、`test_windows_bash_missing_falls_back_to_powershell`、`test_windows_bash_broken_falls_back_to_powershell` 取代。`test_terminal_dynamic_description.py`：新增 `test_detect_windows_explicit_bash_returns_bash_when_found`，原测试改名为 `test_detect_windows_explicit_bash_returns_powershell_when_bash_unavailable`。

**有意未改。** `_find_bash()` 对其直接调用者仍保留 `RuntimeError`（"Git Bash is not found" 与 ASLR 修复提示契约在那里仍有价值）。Windows 上 `auto` 默认不变（本就 PowerShell 优先）。POSIX 行为不变。不新增 Git 自动下载——回退只切换到始终存在的 PowerShell 链路。

**测试情况。** `tests/tools/test_local_git_bash_port.py`（新增，44 个用例：编码往返 + 载荷安全性；`_is_git_bash_install` 布局/标记/盘符锚定；`_with_msystem_neutralized` win32+Git Bash / 非 Git Bash / 非 win32 / None；发现链纯函数 + 子进程 mock；`_find_bash` 接线；macOS darwin 偏好；`_wrap_command` MSYSTEM 接线；`_run_bash` 启动解析出的 shell 路径）与 `tests/tools/test_shell_resolution.py` + `tests/tools/test_terminal_dynamic_description.py`（47 通过），以及相关套件 `test_local_env_windows_msys.py`、`test_find_shell.py`、`test_bash_fix.py`、`test_local_shell_init.py`、`test_process_registry.py`。完整 `tests/tools` 套件：8687 通过、330 跳过（均为平台专属跳过）。编辑的 Python 文件 `ruff check` 干净。

**是否上游？** 建议——显式选中的 shell 缺失时优雅降级是通用能力；PowerShell 随每个 Windows 系统自带，回退始终安全。MSYSTEM 中和、git.exe 发现链、标记检查与 macOS 候选是 kimi 的设计且通用；win32 门控与 `_run_bash` shell 路径一致性是正确性修复。POSIX 或 Git Bash 健康时无行为变化。有意不移植：`_bash_runs`（已被 Hermes 更强的 `_bash_starts` 取代）与 Windows shell 默认策略（刻意分叉）。


### P-053：测试套件卫生清理——移除 ad-hoc / magic-mock / 防 LLM 幻觉测试

**现象 / 需求。** 测试套件积累了一类测试，其唯一目的是冻结当前值或代码形状，防止编辑代码库的 LLM 静默改动它们（"防幻觉"护栏）：精确的模型/命令/工具列表（`assert models == ["gpt-4.1-mini", ...]`）、枚举计数（`len(tools) == 42`）、schema 形状钉死（`"query" in schema["parameters"]["properties"]`）、版本/常量字面量冻结、`inspect.getsource`/AST 源码形状钉死、mock 自指同义反复（`assert x == MagicMock()` 一类）、内联重写生产逻辑却从不调用真实代码的测试、以及批量凑覆盖率的冒烟测试。AGENTS.md 明确禁止这类测试（"Don't write change-detector tests" / "Behavior contracts over snapshots——不要冻结当前值（模型列表、配置版本字面量、枚举计数）"）。它们在任何有意改动时都会失败、从不验证行为、还给每次全量回归增加噪音。

**改动内容**（全部在 `tests/` 内）：

1. **信号扫描**——按 change-detector 信号（schema 形状钉死、字面量列表钉死、版本钉死、`len()==N` 钉死、`assert_called_once_with` 载荷钉死、仅 isinstance/hasattr/is-not-None 检查）对全部 2,257 个测试文件逐文件打分；1,203 个有信号的文件拆成 102 个分块（63 强信号 + 39 中等信号）。
2. **子代理三分类**——每个分块按书面规则（`TEST_TRIAGE_RUBRIC.md`）审查，偏保守（"拿不准就保留"）。保留 1,033 个文件（行为 / 回归 / 不变量 / E2E / 性能测试，含 wire-contract 测试与带 issue 编号的回归复现）。
3. **删除**——**8 个整文件**（复制生产逻辑的自我指涉测试如 `test_step_callback_compat.py`、只测第三方库的测试如 `test_xxhash_migration.py` / `test_process_loop_event_loop_warning.py`、纯 schema/enum 字面量钉死文件如 `test_hindsight_config_schema.py`）以及 **162 个文件中的 546 个测试**（约 10,467 行）。
4. **类感知 AST 删除**——`delete_tests2.py` 仅在类的全部方法都被命中（且类内方法数 > 0）时才整类删除，支持整类目标，并在 verdict 中类名前缀拼写错误时回退到非限定名匹配；删除后折叠多余空行，保存前逐个重新解析校验。

**回归防护。** 全套仍可收集（44,538 个测试）；所有被编辑文件均已重跑：**8,809 通过、398 跳过、0 失败**（覆盖全部 162 个编辑文件）；跨目录 32 文件抽样亦通过（862 通过、0 失败）。被删除模块无跨文件引用，被删除测试名无悬空调用。

**是否可上游？** 删除*标准*与 AGENTS.md 自身指引一致，可为上游测试卫生提供参考；但本次清理本身是 fork 维护动作，不是行为补丁。`deletion_plan.json`（每个被删测试及其原因的审计清单）与 `TEST_TRIAGE_RUBRIC.md`（规则）保留在仓库根目录备查。

### P-049：终端输出后处理管线 + rtk (reasoning toolkit) 集成
**现象。** `git log`、`cargo test`、`npm install`、`docker ps` 或 `ls -R` 等终端命令会输出数千行重复内容——重复的错误行、进度条、状态行——而模型为每一行重复内容支付 token，浪费上下文和 API 预算。旧的管线只是一段内联的 ANSI 剥离 + 硬编码的 40%/60% 字符级头尾截断，完全没有去重功能。也没有 `max_lines` 参数，模型只能通过字节上限控制输出大小。

**改动内容。**

1. **`tools/terminal_post_process.py`（新增）。** 多阶段输出后处理管线：
   - **阶段 1 — `filter_output()`：** 剥离 ANSI 转义序列（Rich 版更彻底），归一化 `\r\n`→`\n` / 孤立的 `\r`→`\n`。
   - **阶段 2 — `_save_original_output()`：** 在破坏性过滤前，将已过滤但未去重的原始输出保存到 `~/.hermes/sessions/<uuid>/terminal_output_original.txt`。
   - **阶段 3 — `_dedup_output()`：** 单行去重（首次出现加注 `"  (N repeats)"`）和多行块去重（贪婪检测连续重复的线块，最多 3 行，例如重复的 `Downloading 45%`→`Downloading 100%` 进度序列）。可配 `_DEFAULT_DEDUP_THRESHOLD=3`。
   - **阶段 4 — `_truncate_lines()`：** 基于行数的头/尾截断，插入 `[... N lines omitted ...]` 折叠标记。保留前 `floor(max_lines/2)` 和后 `ceil(max_lines/2)-1` 行。
   - **阶段 5 — `_token_filter_output()`：** 将上述阶段组合为单一管线，返回结构化 `TerminalOutputResult`，附带元数据标志（`dedup_applied`、`lines_truncated`、`original_path` 等）。当 `rtk` 已处理过去重时跳过去重。
   - **阶段 6 — `_maybe_export_output_async()`：** 输出超过 `_DEFAULT_EXPORT_CHARS`（4096）时导出到 `~/.hermes/sessions/<uuid>/terminal_output_exported.txt`，返回替换消息。
   - **阶段 7 — `_build_session_output_block()`：** 组装 YAML 风格元数据块（task_id、status、exit_code、elapsed_seconds、output、截断标志）供工具结果使用。

2. **`tools/rtk_provision.py`（新增）。** `rtk` 二进制的运行时检测与路径解析——完全仿照 `_find_rg()` 模式：
   - 优先级：托管 `<HERMES_HOME>/tools/rtk` → 旧版 `<HERMES_HOME>/bin/rtk` → `PATH` 通过 `shutil.which`。
   - 每个候选都运行 `rtk --version` 验证可用性。
   - 通过 `functools.lru_cache` 进程级缓存。

3. **`tools/terminal_command_rewrite.py`（新增）。** Shell 命令感知的重写：
   - 维护 `_RTK_KNOWN_COMMANDS`（git、cargo、pytest、npm、pnpm、yarn、docker、kubectl、ls、grep、rg、find、cat、head、tail、python、pip、go、rustc、make、cmake、curl、wget、ps、df、du、netstat、ss、systemctl、journalctl，以及 PowerShell cmdlet 如 Get-ChildItem、Get-Content、Select-String 等）。
   - `_rewrite_shell_segment()`：找到第一个真正的可执行 token（跳过环境变量 `KEY=VALUE` 赋值和 `sudo`），若匹配已知命令列表则前置 `rtk`。
   - `_split_shell_segments()`：正确解析 `;`/`&&`/`||`/`|` 分割，尊重引号、转义和 `$(...)` subshell。
   - Shell 元字符读取始终推进游标，避免 `2>&1` 等重定向在命令重写时卡死 Gateway 事件循环；`tests/tools/test_terminal_command_rewrite.py` 覆盖该回归。
   - 尊重 `RTK_DISABLED=1` 前缀和已经以 `rtk` 开头的命令。

4. **`tools/terminal_tool.py`** — 集成：
   - 新增参数：`token_kill: bool = True`（默认开启）和 `max_lines: Optional[int] = None`。
   - 执行前：当 `token_kill=True` 且 `rtk` 可用时，通过 `_maybe_rewrite_shell_command_with_rtk()` 重写命令；`rtk_rewritten` 标志传给后处理，让去重知道 rtk 已处理。
   - 执行后：旧的内联 ANSI 剥离 + 字符级截断完全被新管线取代（`filter_output` → `_token_filter_output` → `_maybe_export_output_async` → ANSI 双层保护）。
   - 新参数 schema 已加入 `TERMINAL_SCHEMA`。

5. **`hermes_constants.py`** — 新增 `get_managed_tools_dir()`：返回 `<HERMES_HOME>/tools`，对现有安装保留旧版 `<HERMES_HOME>/bin` 兜底。

6. **`hermes_cli/dep_ensure.py`** — 将 `rtk` 加入 `_DEP_CHECKS`/`_DEP_DESCRIPTIONS`；重构 `_find_rg()` 和 coreutils 检查使用新的 `get_managed_tools_dir()` 加旧版兜底（此前硬编码为 `<HERMES_HOME>/bin`）。

7. **安装脚本**（`scripts/install.ps1`、`scripts/install.sh`）：增加从 GitHub releases（`rtk-ai/rtk`，版本 0.43.0）下载 `rtk` 二进制，安装到 managed tools 目录，以及 `hermes doctor` 检查。

8. **伴生改动**（使用新的 `get_managed_tools_dir()` / `_find_rg()` from dep_ensure）：`tools/file_operations.py`（搜索使用 `_find_rg()`）、`tools/tirith_security.py`（自动安装目标迁移到 managed tools 目录）、`hermes_cli/commands.py`（ripgrepy 路径解析）、`scripts/install_coreutils.py`（管理工具路径）。

**测试。** `tests/tools/test_terminal_post_process.py`（新增，约 295 行）覆盖：ANSI 剥离、CR/LF 归一化、单行和块去重（低于阈值、高于阈值、空、单行）、行截断（低于限制、无操作、折叠标记、空、小 max_lines）、完整管线集成（直通、去重开关、rtk_rewritten 跳过、行截断、原始保存、返回类型）、超长导出（低于/超过/恰好等于限制）、原始保存到临时文件、元数据块组装（完整+最小）。现有 terminal/file/search/tirith 测试已更新以覆盖 managed-tools-dir 解析。Ruff 检查通过。

**是否可上游？** 可以——去重/截断/导出管线是纯 Python 无外部依赖；managed-tools-dir 模式（`get_managed_tools_dir()`）是一个通用的维护改进，整合了 Hermes 发现自身下载的二进制文件的方式。rtk 集成（命令重写 + 二进制检测）依赖第三方 CLI（`rtk-ai/rtk`），可作为可选增强提交上游。

## 发布和维护支撑

这些不是运行时行为补丁，但属于 fork 维护能力：

| 范围 | 目标文件 | 做了什么 |
|---|---|---|
| 上游同步 | `scripts/sync-upstream.sh`, `.github/workflows/upstream-watch.yml`, `MAINTAINING.md` | 固化“临时同步分支 + PR 回 main”的同步流程，避免直接在 `main` 合上游 |
| managed runtime | `.github/workflows/release-runtime.yml`, `scripts/sign_runtime_manifest.py`, `docs/RUNTIME_RELEASES.md` | 构建 PyInstaller runtime，签名 manifest，并发布给 desktop 下载 |

## 补丁详情

### P-047：CLI 委派事件——可视化 Claude Code / Codex 交接

**症状 / 需求。** hermes 经 `claude-code` / `codex` 技能把编码任务交给外部 CLI，实际形态是 `terminal` 工具跑 `claude -p …` / `codex exec …`。在事件流上这只是一次匿名工具调用：`tool.start` 只有 80 字符截断预览，`tool.complete` 才有全量命令与最终输出。UI 无法可靠判定"这是一次委派"，前台运行期间完全无输出可看，`--resume` 所需的 session_id、轮数、成本也只能各客户端自行脆弱解析。

**改动。**
- `tui_gateway/cli_delegation.py`（新增）：纯函数分类器（`classify_cli_delegation` → `DelegationSpec{agent, mode, prompt_excerpt, workdir, flags}`）；Claude stream-json 与 Codex 两代 JSON 形态的归一化解析；`DelegationTracker` 单例负责生命周期 + 惰性 watcher daemon 线程。
- `tui_gateway/server.py` 四处接线：`_on_tool_start` 分类命中发 `delegation.cli.started`（与 `tool.start` 同 `_tool_progress_enabled` 门控）；`_on_tool_complete` 前台直接终态（从 terminal 输出解析结果对象）、后台绑定 `process_registry` 会话；`_emit_agent_terminal_output` 加第二个 sink 消费者喂 tracker；`_wire_agent_terminal_output` 装配 tracker（`emit=_emit`、`is_alive=sid in _sessions`）。
- 事件：`started`（`delegation_id == tool_call_id`、agent、mode、execution、脱敏命令 ≤2000、prompt 摘录 ≤200、workdir、flags）→ `output`（仅后台：500ms 合并 `chunk` ≤4KB（ANSI 剥离+脱敏）、每委派累计 256KB 后仅发归一化 `events[]`（≤20/次）并标 `truncated`）→ `completed`（`status ∈ completed|failed|killed|lost`、exit_code、时长、脱敏 `output_tail` ≤4KB、解析后 `result`）。不设单独 failed 事件——终态一个原子 payload。
- 技能：claude-code 2.3.0 新增「Hermes background + streaming」小节（>30s 任务优先 `--output-format stream-json --verbose --include-partial-messages` + `background=true, notify_on_complete=true`，短任务前台 `--output-format json`）及两条 Rules（结构化输出、保留 `session_id`）；codex 1.1.0 后台示例加 `--json`（"if supported" 措辞对冲版本差异）+ `notify_on_complete=true`。
- 清理：死会话条目静默清扫；6 小时过期发 `lost`；无绑定委派时 watcher 线程自动退出。

**兼容性。** 纯增量事件类型——ui-tui 的 `asGatewayEvent` 只要求 `.type` 且按已知类型分发；CN 桌面端 `parseGatewayEvent` 对未知事件回退 `RawGatewayEvent`；上游 `apps/desktop` 忽略未知类型。非目标已登记：tmux 交互式委派（无稳定进程可绑）保持普通工具卡；`process(action="submit")` 输入不回显。

**测试。** `test_cli_delegation_classifier.py`（表驱动 fixture，与桌面仓字面共享）、`test_cli_delegation_events.py`（接线行为：前台两拍、后台绑定、blocked 直接 failed、非委派静默、门控关闭静默）、`test_cli_delegation_stream.py`（跨 chunk 半行拼接、上限、ANSI 剥离、退出状态映射、进程消失、死会话清扫、过期）。

**可否上游？** 或可——事件与客户端无关，价值取决于上游桌面端何时有委派视图。与 `Hermes-CN-Desktop/web/src/lib/cli-delegation.test.ts` 的 fixture 镜像必须同步维护。

---

### P-041：修复 Windows 桌面端 `write_file` → `terminal` 卡死回合

**现象**（Windows 桌面端）：助手先发出一个没有前导正文的 `write_file` 工具调用，该工具很快完成后模型又发出第二个工具调用（`terminal`）。UI 显示"正在运行终端命令"并伴随思考中指示器，始终不产生最终助手消息；用户发送 `again` 也无法恢复。

**根因**：桌面端流状态机由 `message.start` 初始化，期望收到 `message.delta` 文本或最终的 `message.complete`。纯工具调用的助手消息两者都不提供：`message.start` 之后第一个实质性事件是 `tool.start`。缺少显式边界时，合并后的助手消息从第一个 `tool.start` 起一直 `pending` 到最终 `message.complete`。如果后续任何事件延迟或乱序，UI 没有可恢复的清晰节点。另一诱因是核心循环在工具执行前会 flush `stream_delta_callback(None)`，但网关只设置了 `_stream_callback`，该 flush 对网关无效；若两者接在一起，`None` 增量会被误发为 `message.delta {text: null}`。

**改动**：
- `agent/agent_init.py`、`agent/conversation_loop.py`：新增 `tool_calls_committed_callback` 参数；在追加工具调用助手消息后、执行工具前调用。
- `tui_gateway/server.py`：把回调接到 `assistant.tool_calls_committed` 事件（携带 role、finish_reason、tool_call_ids、has_content）；在 `_stream` 中过滤 `None` 增量；新增按会话事件追踪（`gateway.event_trace`）到 `~/.hermes/logs/tui_gateway_events.log`，以及回合不活动看门狗（`gateway.turn_watchdog_seconds`，默认 600 秒），超时后 emit `error` 并释放 `session["running"]`。
- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts`：处理 `assistant.tool_calls_committed`，flush 队列增量并把 `sawAssistantPayload` 标为 true、`awaitingResponse` 标为 false。
- `apps/desktop/src/lib/chat-messages.ts`：强化 `findToolPartIndex`，仅在 stable id 匹配且工具名也匹配时才复用 pending 行。
- `hermes_cli/config.py`：登记新的 `gateway.event_trace` 与 `gateway.turn_watchdog_seconds` 配置项。
- 测试：`tests/run_agent/test_tool_call_streaming_convergence.py`、`tests/tui_gateway/test_tool_call_committed_event.py`，并在 `apps/desktop/src/lib/chat-messages.test.ts` 新增用例。

**是否可上游？** 是。该事件通用，匹配强化修复了所有消费网关事件流的客户端在纯工具调用回合上的状态机缺口。

---

### P-026：桌面托管运行时收敛第三方缓存到 HERMES_HOME

**现象**（Windows 桌面）：用户把 CN 桌面装到 D 盘以躲开快满的 C 盘，但 C 盘仍持续增长。桌面端自身的运行时树已经收敛，但内核引入的 Python 库仍把缓存散落在 C 盘的用户主目录里。

**根因**：huggingface/transformers、torch、tiktoken、matplotlib、nltk、playwright 在未设置各自环境变量时，默认把缓存写到用户主目录（`~/.cache/...`、`%USERPROFILE%\...`），而托管运行时从未设置它们——于是无论装到哪个盘，这些缓存都逃逸出收敛根。当前运行时里真实可达的命中是 tiktoken（`hermes_cli/tools_config.py`）和 transformers tokenizer（`trajectory_compressor.py`）；playwright 已在 `browser_tool.py` 里自收敛，但只作用于其子进程环境，并非进程级。

**修复**：`hermes_constants.configure_managed_runtime_caches()` 用 `setdefault` 把 `HF_HOME`、`HUGGINGFACE_HUB_CACHE`、`TORCH_HOME`、`TIKTOKEN_CACHE_DIR`、`MPLCONFIGDIR`、`NLTK_DATA`、`PLAYWRIGHT_BROWSERS_PATH` 指向 `<HERMES_HOME>/cache/<tool>`；仅当 `TMPDIR/TEMP/TMP` 都未设置时，再把它们指向 `<HERMES_HOME>/cache/tmp`。由于桌面端把 `HERMES_HOME` 设在其收敛的 `runtime_root()` 下（全新 Windows 安装时锚定到安装目录——见 Hermes-CN-Desktop），这些缓存随之落到所选盘。函数以 `HERMES_DESKTOP_MANAGED=1` 门控并使用 `setdefault`，因此独立 CLI 安装保留其共享 `~/.cache`（不触发重新下载），用户显式设置的值始终优先。挂在 `hermes_bootstrap`（每个入口最先 import）里，确保早于 transformers/tiktoken/playwright 加载。

**测试**：`tests/test_managed_runtime_caches.py`——未设 `HERMES_DESKTOP_MANAGED` 时无操作；托管时把缓存变量设到 HERMES_HOME 下；`setdefault` 不覆盖已有值；已配置临时目录时不动它。

### P-001：provider dict/list 不一致修复

这个补丁已被上游等价修复，本 fork 不再携带。当前 `_apply_model_switch` 中 `user_provs = cfg.get("providers")` 已能处理所需配置形态。

---

### P-002：`POST /api/upload`

**现象**：desktop 或 web composer 拖拽上传文件时，请求 `/api/upload` 返回 404。

**原因**：上游曾经加入过 dashboard 附件上传接口，后来又移除；desktop 仍需要这个能力。

**改动**：增加 FastAPI handler，接收 multipart `file` 和 `session_id`，写入 `~/.hermes/sessions/<id>/attachments/`，返回 `{ok, filename, path, size, mime_type}`。文件名冲突复用上游 `_next_unique_path`。

**风险和约束**：
- 走 dashboard session token 鉴权。
- 只写入指定 session 的 attachments 目录。
- 不覆盖已有文件。
- 不做会触发执行语义的 content-type 处理。

**是否上游**：可以考虑，但需要先确认上游当初移除该接口的原因。

---

### P-003：去掉 `/api/ws` 的 embedded TUI 门禁

**现象**：desktop 运行 `hermes dashboard --no-open` 时，`/api/ws` upgrade 会被关闭，聊天不可用。

**原因**：上游的 `_DASHBOARD_EMBEDDED_CHAT_ENABLED` 只在 `hermes dashboard --tui` 模式下打开。desktop 是 headless dashboard + 独立 UI，不会启用这个标志。

**改动**：移除 `/api/ws` 对 `_DASHBOARD_EMBEDDED_CHAT_ENABLED` 的检查。接口仍受 session token 和 loopback host 约束。

**风险和约束**：持有同源 session token 的 Web UI 可以在非 `--tui` 模式访问 gateway。这和 `/api/pty`、`/api/pub`、`/api/events` 的安全边界一致。

**是否上游**：建议上游。当前门禁会阻断合法的外部 Web UI 用法。

**v0.16.0 同步更新**：上游 #38591 现在默认开启 embedded chat(`_DASHBOARD_EMBEDDED_CHAT_ENABLED` 默认 `True`)并移除了 dashboard 的 `--tui` 标志,原始现象默认不再出现。fork 仍保留 `/api/ws` 上的显式去门禁,以便即使将来 embedded chat 被关闭,gateway RPC 通道(v2 web UI / 桌面端使用)仍可达。

---

### P-004：`GET /api/fs/list`

**现象**：web 工作区选择器没有目录浏览能力，只能退化为 `window.prompt()` 让用户输入路径。

**原因**：纯 Web UI 无法调用系统文件夹选择对话框；上游 dashboard 也没有文件夹浏览 API。

**改动**：增加 `GET /api/fs/list?path=<dir>&include_hidden=<bool>`，返回 `{path, parent, home, entries: [{name, path, is_dir}]}`。

路径处理规则：
- 支持 `~` 展开。
- 使用 `Path.resolve(strict=False)` 折叠 `..`。
- 限制在用户 home 子树内。
- 响应最多 5000 项。
- 默认隐藏隐藏文件。

**风险和约束**：这是目录枚举接口，因此必须保留 token 鉴权、home 子树限制和大目录上限。

**是否上游**：取决于上游是否希望 browser-only Web UI 成为一等场景。

---

### P-005：`GET /api/mcp-servers`

**现象**：desktop 健康检查需要知道 MCP server 总数和启用数，但不应读取完整 MCP 配置。

**原因**：MCP 配置中的 `command`、`args`、`env` 可能包含敏感信息。上游没有只读摘要接口。

**改动**：返回 `{summary: {total, enabled}, servers: [{name, enabled}]}`，刻意不返回 `command`、`args`、`env`。

**风险和约束**：只读摘要，风险低。必须继续避免暴露密钥和启动参数。

**是否上游**：建议上游，其他 dashboard frontend 也会用到。

---

### P-006：CN provider 的 `OPTIONAL_ENV_VARS`

**现象**：desktop 模型设置页列出 CN provider，但 env 面板没有对应 `*_API_KEY` 输入项。

**原因**：上游 metadata 主要覆盖 OpenAI、Anthropic、Google、DeepSeek 等全球 provider。

**改动**：为 ARK、QIANFAN、HUNYUAN、SILICONFLOW、MODELSCOPE、AI302、COMPSHARE 等注册 provider 类环境变量，并补充中文说明和官方文档链接。

**风险和约束**：设置页会多出一批高级 provider 配置项，不改变现有解析逻辑。

**是否上游**：部分 provider 也许可以单独上游，但整体是 CN 专属。

---

### P-007：gateway WS dispatch 异常可观测性

**现象**：前端偶发只显示 “WebSocket closed”，后端没有足够上下文定位 dispatch 异常。

**原因**：`tui_gateway/ws.py` 中 dispatch/write 发生异常时会跳出循环并关闭连接，客户端只能看到连接断开。

**改动**：
- 包裹 `server.dispatch` 和 `transport.write_async`。
- 将 traceback 写入 `~/.hermes/logs/dispatch_exceptions.log`。
- 返回 JSON-RPC error（code `-32000`）。
- 保持连接继续可用。

**风险和约束**：异常日志会增长；客户端应把 `-32000` 视为通用服务端错误。

**是否上游**：强烈建议。正常路径行为不变，主要提升诊断能力。

---

### P-008：`GET/PUT /api/profiles/active`

**现象**：desktop profile 切换器需要读取和设置 sticky active profile。

**原因**：上游有 profile 列表、创建、删除、重命名、SOUL 读写，但没有对 `~/.hermes/active_profile` 的 HTTP getter/setter。

**改动**：
- `GET /api/profiles/active` 返回 `{name}`，文件不存在时返回 `default`。
- `PUT /api/profiles/active` 接收 `{name}` 并写入 sticky 设置。

**风险和约束**：该接口只影响下次启动默认 profile，不改变当前 dashboard 进程正在使用的 `HERMES_HOME`。desktop 需要提示用户重启。

**Desktop 托管的命名档案→默认档案重启修复**：Desktop 为保证失败可恢复，会先启动替换 dashboard，确认就绪后才清除 `active_profile`。旧逻辑在替换进程启动时读取这个尚未清除的 sticky 文件，把 Desktop 显式传入的根 `HERMES_HOME` 又改回 `profiles/<旧档案>`。现在当 `HERMES_DESKTOP_MANAGED=1` 且没有显式 `--profile` 时，`_apply_profile_override()` 以 Desktop 传入的 `HERMES_HOME` 为准；显式 `--profile` 仍然优先，独立 CLI/systemd 的 sticky 行为不变。回归覆盖见 `tests/hermes_cli/test_apply_profile_override.py`。

**是否上游**：建议上游，属于明显的 API 对称性缺口。

---

### P-009：SSE+POST gateway transport —— **已弃用**

> **弃用说明（2026-06-09）**：桌面端自 0.4 起已切换到 runtime 原生的
> `/api/ws` JSON-RPC WebSocket（与官方 Electron 桌面端 `apps/desktop` 同一
> transport）——SSE+POST 路径无心跳、每个 RPC 一次 HTTP 往返、异步 ack 拆分
> 导致在途回合脆弱。**这两个端点必须保留**到 ≤ 0.3.x 旧外壳 EOL：Tauri 外壳
> 没有自更新，而 runtime 会在其下热更新，新 runtime 必须继续服务旧外壳。
> `/api/v2/events` 每次连接会打一条弃用日志，便于在移除前从 runtime 日志
> 量化残留用量。

**现象**：desktop 需要稳定、浏览器友好的流式 transport。只依赖 `/api/ws` 时，桌面壳和网络环境下的故障更难诊断。

**原因**：上游 gateway 主要通过 WebSocket 暴露。desktop 希望服务端到客户端走 EventSource，客户端到服务端走普通 HTTP POST。

**改动**：
- 增加 `GET /api/v2/events` 推送 SSE frame。
- 增加 `POST /api/v2/rpc` 发送 gateway JSON-RPC 请求。
- 增加 `tui_gateway/sse.py` transport 实现。

**风险和约束**：新增一个经过鉴权的 gateway transport 面。鉴权应继续复用 dashboard session token。

**是否上游**：可以考虑。它对 browser-hosted dashboard 和桌面壳有价值，但会扩大上游需要维护的 transport 矩阵。

---

### P-010：`LONGCAT_API_KEY`

**现象**：CN 模型设置包含 LongCat，但 env metadata 没有 `LONGCAT_API_KEY`。

**原因**：上游 provider metadata 未覆盖 LongCat。

**改动**：将 `LONGCAT_API_KEY` 加入 `OPTIONAL_ENV_VARS`。

**风险和约束**：设置页多一个 provider credential 输入项。

**是否上游**：只有在上游正式支持 LongCat 时才适合提交。

---

### P-011：模型过滤和 provider probe

**现象**：desktop 需要按 provider slug 过滤模型选择器，并在不启动完整 agent turn 的情况下轻量探测 provider。

**原因**：上游 `model.options` 返回较宽泛的选项；没有专用的 provider 探测 RPC。

**改动**：
- `model.options` 增加 `slug_filter`。
- 增加 `provider.probe` gateway RPC。

**风险和约束**：`provider.probe` 不应返回密钥、原始配置或敏感错误细节。

**是否上游**：可以考虑，但需要先审定 probe 的返回结构和错误语义。

---

### P-012：`_model_flow_anthropic()` 支持可选自定义 `base_url`

**现象**：在交互式添加 Anthropic 模型时，代码无条件执行 `model.pop("base_url", None)`，导致任何预配置或期望的自定义 `base_url` 被静默丢弃。

**原因**：`_model_flow_anthropic()` 原本假设所有 Anthropic 请求都应走官方 `https://api.anthropic.com`，未考虑使用兼容代理、OpenRouter 或私有端点的场景。

**改动**：
- 移除无条件的 `model.pop("base_url", None)`。
- 在模型选择后增加交互式提示，显示当前 `base_url`（默认 `https://api.anthropic.com`）。
- 用户输入自定义地址则保存到 `model["base_url"]`。
- 用户直接回车则保留已有 `base_url`；仅在原本不存在时才将其移除，让运行时回退到硬编码的官方地址。

**风险和约束**：无。`runtime_provider.py` 对 `anthropic` provider 已使用 `model_cfg.get("base_url")` 读取配置，无需额外运行时改动。

**是否上游**：建议上游。该改动向后兼容，且能支持合法的第三方 Anthropic 兼容端点场景。

---

### P-013：`handle_function_call` 自动修复工具参数键名

**现象**：LLM 发起工具调用时经常使用同义词或拼写错误的参数名（如 `file` 代替 `path`、`cmd` 代替 `command`、`backgroud` 代替 `background`），导致工具层返回 "unknown parameter" 或直接失败。

**原因**：Hermes 的 JSON Schema 较为严格，LLM 对字段名的漂移会直接透传给工具 handler，而 handler 通常不认识这些别名。

**改动**：
- 在 `model_tools.py` 中引入 `repair_tool_arg_keys()` 与 `_repair_nested_args()`。
- 定义全局别名表 `TOOL_FIELD_ALIASES`，覆盖通用、文件、Shell、Web、任务、待办、输入、搜索、记忆、定时任务、技能等多类参数名。
- 定义 `TOOL_SPECIFIC_ALIASES` 实现工具级覆盖（如 `delegate_task` 将 `task` 映射到 `goal` 而非全局的 `prompt`；`cronjob` 将 `command` 映射到 `action`）。
- 当别名表未命中时，使用 `difflib.get_close_matches` 对拼写错误进行模糊匹配。
- 根据 schema 中的 `properties` 与 `items` 定义，递归修复嵌套对象和对象数组内部的键名。
- 提供可选回调钩子 `set_arg_repair_callback`，供外部系统（TUI、ACP）在顶层键名被修复时得到通知。
- 在 `handle_function_call()` 中于 `coerce_tool_args()` 之前调用修复逻辑，因此修复后的键仍会正常经历类型强制转换。
- 新增完整测试 `tests/run_agent/test_repair_tool_arg_keys.py`。

**风险和约束**：极低。该函数是纯键名映射变换，无法识别的键保持原样；模糊匹配仅对长度 ≥4 且相似度 ≥0.75–0.80 的键生效，随机字段不会被误改名。

**是否上游**：建议上游。这是与平台、provider 无关的通用健壮性提升，对所有 Hermes 部署都有价值。

---

### P-014：冻结 desktop runtime 缺失原生 MCP 客户端

**现象**（issue #16）：用户在 `~/.hermes/config.yaml` 正确配置了 `mcp_servers`，MCP server 脚本独立运行正常，但 CN Desktop agent 启动后从不连接它——`agent.log` 中没有任何 MCP 发现/连接日志，工具列表里也没有 `mcp_*` 工具。在宿主机执行 `pip install mcp` 也无济于事。

**根因**：原生 MCP 客户端其实已完整实现（`tools/mcp_tool.py`、`discover_mcp_tools()`），但其 SDK 是只存在于 `[mcp]` extra 的可选依赖。runtime 发布流程当时只安装 `.[web,anthropic]`，因此冻结后的 PyInstaller 产物**没有**打进 `mcp` 包。于是冻结 runtime 内 `_MCP_AVAILABLE` 为 `False`，`discover_mcp_tools()` 仅以 `debug` 级别记录后返回 `[]`——在默认 INFO 日志级别下完全不可见。宿主机的 `pip install mcp` 无关紧要，因为冻结 runtime 自带独立解释器和依赖。

**改动内容**：
- `release-runtime.yml`：把 `mcp` SDK 打进产物（安装入口后并入 `cn-desktop` extra，见 P-015），PyInstaller 增加 `--collect-submodules mcp` 与 `--copy-metadata mcp`，并扩展校验步骤——若缺少 `mcp-*.dist-info` 则直接让构建失败（防止再次悄悄回归）。
- `tools/mcp_tool.py`：当已配置 `mcp_servers` 但 SDK 不可用时，`discover_mcp_tools()` 改为输出一次 `WARNING`（“mcp_servers are configured but the MCP SDK is not available …”），而非静默的 debug。未配置 MCP 的用户仍走安静的 debug 分支。
- `hermes_cli/config.py`：把 `mcp_servers` 加入 `_KNOWN_ROOT_KEYS`，让根级 schema 文档保持准确。
- `docs/RUNTIME_RELEASES.md`：将 MCP 列为 runtime 必备依赖，并更新手动 dry-run 命令。
- `tests/tools/test_mcp_tool.py`：覆盖“已配置则告警 / 未配置则安静 / 仅告警一次”三种行为。

**风险和约束**：冻结 runtime 体积增加 `mcp` SDK 及其传递依赖（`anyio`/`httpx-sse`/`sse-starlette`，均已随 `web`/`anthropic` 存在）。对已包含 `[mcp]` extra 的源码安装无行为变化。

**是否上游**：打包改动是 CN runtime 特有（上游不构建这些 PyInstaller 产物）；`mcp_tool.py` 的诊断日志与 `mcp_servers` 根键属于通用改进，值得上游。

---

### P-015：冻结 desktop runtime 缺失 IM 平台后端

**现象**：桌面用户正确填了飞书 App ID/Secret 到 `.env`，在 `config.yaml` 加了飞书平台，网关进程也在跑——但就是连不上飞书；打包应用内 `lark-oapi`"无法安装"。钉钉、企业微信、微信同理。

**根因**：和 P-014 完全同源，只是范围更广。IM 适配器（`gateway/platforms/feishu.py`、`dingtalk.py`、`wecom*.py`、`weixin.py`）都在 `try/except` 里导入 SDK，包缺失时降级为 `*_AVAILABLE = False`。这些 SDK 只存在于可选 extra（`[feishu]`→`lark-oapi`、`[dingtalk]`→`dingtalk-stream`+`alibabacloud-*`、`[wecom]`→`defusedxml`；微信**没有** extra，需要 `aiohttp`/`qrcode`/`cryptography`）。`[all]` 的策略故意排除它们，因为它们能通过 `tools/lazy_deps.py` 懒安装——但**冻结的 PyInstaller 二进制里懒安装根本跑不了**（没有可用 pip），而 desktop runtime 当时只装了 `.[web,anthropic,mcp]`，于是一个都没带。用户在宿主机执行的 `pip install lark-oapi` 写进的是系统 Python，冻结 runtime 从不使用。

**改动内容**：
- `pyproject.toml`：新增 `cn-desktop` 聚合 extra，列出冻结 runtime 必须预打包的所有后端——`web`、`anthropic`、`mcp`、`feishu`、`dingtalk`、`wecom`，外加微信用的 `aiohttp`/`qrcode`/`cryptography`（pin 与现有 extra 对齐）。这是"桌面端打包什么"的单一事实来源，刻意区别于 `[all]` 的懒安装策略。
- `release-runtime.yml`：安装 `.[cn-desktop]`；为 `lark_oapi`、`dingtalk_stream`、`alibabacloud_dingtalk`（+`alibabacloud_tea_openapi`/`alibabacloud_tea_util`）、`aiohttp`、`qrcode` 增加 `--collect-submodules`/`--copy-metadata`；新增**构建环境 import 冒烟**——逐个 import 适配器并断言其 `*_AVAILABLE` 为 True（缺依赖立即失败）；并把校验步骤推广为断言每个打包后端的 `dist-info` 都在冻结产物里。
- `docs/RUNTIME_RELEASES.md`：把 `cn-desktop` extra 记录为"以后新增桌面后端"的入口，并标注 `alibabacloud_*` 收集较脆（首次发版需对真实钉钉机器人做连通冒烟）。
- `uv.lock`：为新 extra 重新生成（`uv lock --check` 通过）。

**风险和约束**：冻结 runtime 体积增加 IM SDK 及其传递依赖（尤其是纯 Python 的 `alibabacloud_*` 链）。它们都是纯 Python、有跨平台 wheel/sdist——不像 `matrix` 的 `python-olm` 需要 C 工具链，那个仍刻意排除。对源码安装无影响。

**是否上游**：否。上游不构建这些 PyInstaller 产物，`cn-desktop` extra 与打包均为 CN runtime 特有。

---

### P-016 / P-019 / P-XXX：PowerShell 原生执行——Git-Bash→PowerShell 迁移 + pwsh 检测

> P-019 完成了 P-016 开启的迁移，移除全部剩余 Git Bash 发现逻辑，只针对 **Windows PowerShell 5.1**（`powershell.exe`）；P-XXX（落地时未编号）其后又在 P-019 之上恢复 pwsh 优先检测。三者合并描述如下。

**现象 / 需求。** Windows 上 agent 硬编码为始终使用 Git Bash。PowerShell 启动更快（`-NoProfile`）、原生处理 Windows 路径（无需 `/c/foo` 翻译）。此外 terminal 工具的静态 `TERMINAL_TOOL_DESCRIPTION` 引用了原生 PowerShell 中不存在的 Linux/bash 命令。上游 `LocalEnvironment` 只支持 bash，因此完全没有 PowerShell 路径。

**P-016 做了什么**（已被 P-019 取代）：

1. **`tools/environments/local.py`** — 新增 `_resolve_shell()`：Windows 上先探测 `pwsh.exe`（PS7），回退到 `powershell.exe`（PS5.1）或 Git Bash。新增 `_run_pwsh()`、`_wrap_command_pwsh()`，覆写 `init_session()`、`_run_bash()`、`_wrap_command()`。支持 `HERMES_SHELL_TYPE` 与 `HERMES_PWSH_PATH`。
2. **`tools/terminal_tool.py`** — 动态描述：`_detect_shell_for_description()` + `_build_dynamic_terminal_description()` 把 Linux/bash 命令引用替换为 PowerShell cmdlet。
3. **`model_tools.py`** — 把 `_shell_fp` 加入 `get_tool_definitions()` 缓存键。
4. **`tools/environments/process_pwsh.py`** — `pwsh_transform()` 把 PS7+ 语法（`?:`、`??`、`&&`、`||`、`?.`、`?[`）降级为 PS5.1 兼容的 `if/else`，带警告传递，让 LLM 获知其 PS7 语法被降级。

**P-019 做了什么**（完成迁移；仅 Windows PowerShell 5.1）：

1. **`tools/environments/local.py`** — 核心 shell 解析：移除 `_find_bash()`（约 130 行，7 策略 + WSL 启动器过滤 + 带死 `_install_git` import 的自动安装），仅对非 Windows 保留最小化 `_find_bash_posix()`。`_find_pwsh_simple` → `_find_powershell()`（就是 `shutil.which("powershell.exe") or "powershell.exe"`——不再探测 `pwsh.exe`）。`_resolve_shell()` 在 Windows 上始终返回 `("powershell", path)`；`HERMES_SHELL_TYPE=bash` 抛 `RuntimeError`；移除 `HERMES_PWSH_PATH` 支持。重命名 `_run_pwsh` → `_run_powershell`、`_wrap_command_pwsh` → `_wrap_command_powershell`。**`pwsh_transform` 改为始终开启**（移除 `if os.path.basename(...).startswith("powershell")` 门）。`_update_cwd`/`_extract_cwd_from_output` 的 MSYS 归一化以 `self._shell_type == "bash"` 门控。
2. **`tools/terminal_tool.py`** — 移除 "Windows Git Bash" 描述分支（死代码）；`_detect_shell_for_description()` 在 Windows 上始终返回 `"powershell"`。
3. **`agent/prompt_builder.py`** — `_WINDOWS_BASH_SHELL_HINT` 换成 `_WINDOWS_POWERSHELL_SHELL_HINT`（PS5.1 语法：`;` 而非 `&&`、`$env:VAR`、无 `?:`/`??`/`?.`）。
4. **`cli.py`** — `_normalize_git_bash_path` → `_normalize_msys_path`。
5. **`apps/desktop/electron/main.cjs`** — `findGitBash()`（约 40 行）换成 `findPowerShell()`（约 15 行）；预检校验 `powershell.exe`。
6. **`scripts/install.ps1`** — 移除 `Install-Git` bash 发现 + `Set-GitBashEnvVar`（约 210 行），简化 `Stage-Git`，增加防御性 `powershell.exe` 检查，去掉全部 `HERMES_GIT_BASH_PATH` 引用。
7. **`hermes_cli/uninstall.py`** — 从环境变量清理中移除 `HERMES_GIT_BASH_PATH`。
8. **`cron/scheduler.py`** — 更新 `.sh`/`.bash` 错误消息（不再提 Git for Windows）。
9. **注释清理** — `tools/environments/base.py`、`tools/file_operations.py`、`tools/browser_tool.py`："Git Bash" → "PowerShell" 或通用 "shell"。
10. **测试** — `test_shell_resolution.py`（重写）、`test_terminal_dynamic_description.py`（移除 bash-on-Windows 测试）、`test_windows_native_support.py`、`test_local_env_windows_msys.py` 更新。
11. **文档** — `windows-native.md`、`environment-variables.md`、`contributing.md` 改为 PowerShell 5.1 描述。
12. **PowerShell UTF-8 编码加固** — `tools/environments/windows_env.py` 新增 `ps_with_utf8()`，前置 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8;`；在 `local.py` 的 `pwsh_transform()` 之后调用；仅对 PowerShell 子进程保留 `encoding="utf-8", errors="replace"`（`hermes_cli/claw.py`、`clipboard.py`、`gateway.py`、`managed_uv.py`）；`hermes_bootstrap.py` 把 Windows 控制台代码页设为 CP_UTF8（65001）并提供 `HERMES_DISABLE_WINDOWS_UTF8=1` 逃逸开关；撤销所有非 PowerShell 子进程上的 `encoding="utf-8"`；新增测试（`test_clipboard.py::TestClipboardPowershellEncoding`、`test_local_pwsh_warnings.py::TestRunPowershellUtf8Encoding` / `TestPwshTransformAndUtf8Compose`、`test_windows_encoding.py`、`scripts/verify_windows_utf8.py`）。

**P-XXX 做了什么**（pwsh 优先后续，未编号）：

1. **`tools/environments/local.py`** — 新增 `_find_pwsh()` 多步检测（PATH、ProgramFiles、注册表、LocalAppData）。`_resolve_shell()` 现在优先 pwsh 而非 powershell.exe。`_wrap_command_powershell()` 在原生 pwsh 时跳过 `pwsh_transform`。所有分发点（`init_session`、`_run_bash`、`_wrap_command`、`_update_cwd`、`_extract_cwd_from_output`）同时接受 `"powershell"` 与 `"pwsh"` 类型。
2. **`tools/terminal_tool.py`** — `_detect_shell_for_description()` 探测 pwsh，找到即返回 `"pwsh"`；`_build_dynamic_terminal_description()` 增加 pwsh 变体。
3. **`agent/prompt_builder.py`** — pwsh 可用时使用 `_WINDOWS_PWSH_SHELL_HINT`（无 PS5.1 限制警告）。

**为什么需要**：
- `powershell.exe`（5.1）随每套 Windows 10/11 自带——零安装、零下载。
- 比 Git Bash 启动更快、原生处理 Windows 路径、避免 POSIX 翻译开销。
- P-019 删除约 400 行死代码（7 策略 bash 发现、WSL 启动器过滤、PortableGit 自动安装、`HERMES_GIT_BASH_PATH`、`HERMES_PWSH_PATH`）；agent 在 Windows 上拥有唯一、可预测、始终可用的 shell。P-016 的 `pwsh.exe`（PS7）探测是不必要的复杂度——5.1 全覆盖。
- P-XXX：装了 pwsh 的用户此前白白承受 PS7→PS5.1 转换开销与 PS5.1 限制警告；pwsh 可用时直接使用并完全跳过降级转换。

**风险和约束**：
- `HERMES_SHELL_TYPE=bash` 在 Windows 上抛清晰的 `RuntimeError`（直到 P-050/P-052/P-054 把它重新开放为显式 opt-in——见合并后的 P-050/P-052/P-054 条目）。
- `HERMES_PWSH_PATH` 与 `HERMES_GIT_BASH_PATH` 环境变量不再被识别。
- 所有命令无条件经过 `pwsh_transform`（除非 P-XXX 下原生 pwsh 运行）。
- PowerShell 命令现在能可靠往返非 ASCII 输出（中文、emoji、带重音字符）；非 PowerShell 子进程仍用系统 locale，这是 fork 刻意保持的保守范围。

**是否上游？** 建议。P-019 完成 P-016 开启的迁移，使 Hermes 成为零依赖的 Windows 程序；P-XXX 是通用后续。

**同步说明（2026-06-27，`chore/sync-upstream-20260627`）**：上游会周期性在 `main` 上重新引入 Git Bash 机制。本次同步的 `upstream/main` 恢复了 `tools/environments/local.py` 里的 7 策略 `_find_bash()`、`tools/terminal_tool.py` 里的静态终端描述、以及 `website/docs/developer-guide/contributing.md` 里的 Git-Bash 措辞。同步**重申 P-016/P-019**——保留 fork 的 PowerShell-only 路径，只在上面嫁接上游*独立*的修复：`_find_shell()` 的 POSIX 后台 spawn `$SHELL` 优先（#42203，改调 fork 的 `_find_bash_posix()`）；`start_new_session`；install-dir PATH 可达性；以及 `apps/desktop/electron/main.cjs` 的 no-console-python 助手（`getNoConsoleVenvPython`/`toNoConsolePython`/`applyWindowsNoConsoleSpawnHints`/`unwrapWindowsVenvHermesCommand`）与 fork 的 async 后端解析结合（探测是 `async` 的，fork 的 `await` 是必需的）。未来同步应预期同样的 Git-Bash 漂移并以同样方式解决。

**`cli.py` 分解清理（2026-06-27）**：此前一次同步在 `HermesCLI` 内联留了一大块"从上游 CLI 分解恢复"的方法。上游现在通过 `CLICommandsMixin` / `CLIAgentSetupMixin`（`HermesCLI` 继承）提供这些方法，因此本次同步按 MAINTAINING.md "上游已加等价功能就移除本地 fork 实现"删掉了冗余的内联副本。重新应用到 `cli.py` 的唯一真正 fork 改动是 P-019 的重命名；未使用的 `_new_session_id` 助手（0 调用者）一并删除。

---

### P-017：跨迭代重复工具调用检测（无限循环断路器）

**现象**：在复杂任务（长时间构建、多步骤重构）中，agent 有时会陷入无限循环，跨连续 API 迭代反复调用相同的工具和相同参数——例如反复读取同一文件，或使用相同命令反复调用 `run`。现有的 `_deduplicate_tool_calls()` 仅移除**同一次**工具批次中的精确重复，完全无法检测跨迭代重复。

**根因**：此前没有跨步骤去重机制。每个 API 迭代的工具结果进入下一次 LLM 调用时，对之前尝试过什么完全没有历史感知。

**改动内容**：

1. **`agent/tool_dedup.py`** — 新增 `ToolDedupTracker` 类模块：
   - 通过 `_canonical_tool_arguments()` 对工具调用键规范化（字典递归排序、回退到 `str()`）。
   - 跟踪 `_seen_call_keys`（所有跨步骤见过的调用）和 `_consecutive_key`/`_consecutive_count`（连续调用计数）。
   - `begin_step(previous_calls, step_no, turn_id)`：从上一步的工具调用结果中植入状态。
   - `end_step()`：返回本步的调用列表供下一次迭代使用，并更新连续计数。
   - `check_and_register(tool_name, arguments)`：在工具执行期间调用；若调用键在前序步骤中已出现过，则在重复计数达到 3、5、8 时返回逐步升级的提示文本。
   - 逐级提示：计数 3 时温和提醒（`<system-reminder>`：“你在重复完全相同工具调用…”）。计数 5 和 8 时更强提示，明确给出工具名、重复次数和参数。

2. **`agent/agent_init.py`** — 在 `AIAgent` 实例上初始化 `_tool_dedup_tracker`。

3. **`agent/conversation_loop.py`** — 步骤生命周期：
   - 每次 API 调用前：`begin_step()` 从上一次迭代的调用结果植入跨步骤状态。
   - 所有工具结果收集完成后：`end_step()` 捕获本次迭代的调用供下一次使用。

4. **`agent/tool_executor.py`** — 去重检查注入：
   - 在 `execute_tool_calls_concurrent()` 中：每次工具执行后调用 `check_and_register()`，将提示文本追加到结果中。
   - 在 `execute_tool_calls_sequential()` 中：相同模式。

**风险和约束**：
- 触发去重时，工具结果可能增加数百字符（`<system-reminder>` 文本）。
- LLM 可见提示文本，可能影响其下一步决策——这正是预期行为。
- 线程安全：`check_and_register()` 使用 `threading.Lock()` 保护并发执行路径中的共享状态。

**是否上游**：机制是通用的，但集成点（`agent_init.py`、`conversation_loop.py`、`tool_executor.py`）与 fork 的 agent 架构高度耦合。可作为通用可观测性钩子提出。

---

### P-018：`agent/agent_init.py` 空 API key 保护

**现象**：当 API key 为空（参数显式传入 `""`，环境变量未设置）时，agent 会以底层 OpenAI 或 Anthropic SDK 认证异常的形式 panic，而不是给出清晰可操作的错误提示。在 TUI/gateway 后台线程中，堆栈信息不会暴露给用户，看起来像静默崩溃。

**根因**：`init_agent()` 在将 `api_key` 交给 `_create_openai_client()` 或 `build_anthropic_client()` 之前，没有显式验证其非空。空字符串流入 SDK 构造函数后产生令人困惑的异常。

**改动内容**：
- 新增 `_api_key_required(provider, api_key, base_url)` 辅助函数，对真正不需要字面量密钥的 provider（Azure Entra ID callable token、`"aws-sdk"` / `"no-key-required"`、Bedrock）返回 `False`。
- 在 `anthropic_messages` 分支的 `build_anthropic_client()` 调用前插入保护。
- 在 `chat_completions` 分支的 `_create_openai_client()` 调用前插入保护。
- 两个保护都在 key 为空且 provider 需要密钥时抛出 `RuntimeError("no API key (param empty, env vars unset)")`。
- 新增两个 pytest 用例分别覆盖 `chat_completions` 和 `anthropic_messages` 的空 key 路径。

**风险和约束**：对真正不需要密钥的 provider（本地端点 `"no-key-required"`、Bedrock、Azure Entra ID）无影响。fallback 循环（`fallback_model` / `fallback_providers`）仍在保护之前执行。

**是否上游**：建议上游。改动纯增量、与 provider 无关，能同时改善 CLI、TUI、gateway 和直接 `AIAgent()` 调用的用户体验。

---

### P-021：Cron 调度器可靠性修复 — 防止静默停摆

**现象**：定时任务在默认日志级别下无任何错误提示就停止执行。Gateway 仍在运行且健康，但 `hermes cron list` 显示任务的 `next_run_at` 已过期却一直不触发。

**根因**：四个相互独立的故障模式：

1. **Daemon 线程静默死亡** — ticker 线程顶部的导入语句在 try/except 之外，`ImportError` 会直接杀死 daemon 线程且零日志。
2. **僵尸锁文件** — 进程被 `SIGKILL` 或内核 panic 后 `.tick.lock` 永不清理，后续进程永远获取不到锁。
3. **损坏的 `jobs.json`** — 首次 tick 中 `load_jobs()` 抛 `RuntimeError`，线程在产生任何输出前死亡。
4. **时区解释漂移** — 旧的无时区时间戳按系统本地时间解释，与配置时区不一致时所有调度时间静默偏移。

同时修复了 `hermes_time.py` 中 `def now():` 缺失的既有 bug。

**改动**：`gateway/run.py`（F-1/F-4）、`cron/scheduler.py`（F-3/F-7）、`cron/jobs.py`（F-5）、`hermes_time.py`（F-7）。详见英文版 Fork Notes。

**是否上游**：建议上游。通用可靠性修复，与平台和 provider 无关。

---

### P-024：`sanitize_api_messages` 空内容消息过滤

> 原先误编号为 **P-022**，与上面的"流式 stale 检测"补丁撞号（后者有 `cn/P-022-provider-stream-hang` 分支与 `[CN-fork] P-022` 提交坐实 P-022；本空内容过滤补丁没有自己的 P-022 提交，故移到下一个空号）。

**现象**：长会话（如飞书 3-13h）在调用模型 API 时偶发 HTTP 400，错误信息如 MiMo 的 `"text is not set"` 或某些严格 OpenAI 兼容网关的空内容拒绝。出错的请求里包含 `content` 被压缩/截断为空字符串的 `assistant` 或 `user` 消息。

**根因**：部分 provider（MiMo v2.5、严格的 OpenAI 兼容网关）拒绝 `content` 为 `""` 且没有工具载荷的消息。Agent 的上下文压缩器会留下这类空消息；此前的预调用清理器只修复了孤儿 tool result 并去掉了 `session_meta` 角色消息，但没有清理空内容的 `assistant`/`user`/`function` 消息。

**改动**：

- 在 `sanitize_api_messages` 的孤儿修复之后新增一轮过滤：角色属于 `{assistant, user, function}`、`content` 严格等于 `""` 且没有有效载荷的消息会被丢弃。
- 可保留 `assistant` 消息的有效载荷包括：
  - `tool_calls`
  - `codex_reasoning_items`
  - `codex_message_items`
  - `reasoning_content`
- 这样 codex / DeepSeek reasoning 回放和工具调用链保持完整，同时剔除触发网关校验错误的空消息。
- `system` 消息故意保留不处理（各 provider 行为不同，避免误删）。
- 完全没有 `content` 键的消息也保留不动，以便需要时让 API 按自身规则报错，避免掩盖其他 bug。

**涉及文件**：
- `agent/agent_runtime_helpers.py` — 在 `sanitize_api_messages` 中加入空内容过滤。
- `tests/run_agent/test_agent_guardrails.py` — 新增 11 个聚焦回归测试，覆盖 assistant/user/function 空内容丢弃、带 tool calls / codex reasoning / reasoning content 时的保留、system 保留、多连续空消息丢弃、幂等性等。
- `tests/run_agent/test_session_meta_filtering.py` — 新增 `TestSanitizeApiMessagesEmptyContentFilter` 类，提供端到端回归测试，包括 MiMo "text is not set" 场景。

**副作用**：
- 重度压缩后到达 API 的消息数会略少，这是预期行为，因为这些消息本身没有可用内容。
- 如果上游调用方出于某种协议目的故意传入空 `assistant` 消息，现在除非携带上述识别载荷，否则会被丢弃。

**是否上游**：建议上游。过滤逻辑与 provider 无关，能防范一类真实的网关拒绝，且已有较全面的测试覆盖。

---

### P-027：`save_config_value()` 绝不创建项目级 `cli-config.yaml`

**现象**：并行测试跑批下，`tests/hermes_cli/test_ignore_user_config_flags.py::test_user_config_skipped_when_flag_set` 失败（一旦 `tests/test_tui_gateway_server.py` 进入同一 CI 切片就稳定复现）：`HERMES_IGNORE_USER_CONFIG=1` 时 `load_cli_config()` 返回泄漏的 `model.default`（`anthropic/claude-sonnet-4.6`）而不是内建默认值。

**根因**：`save_config_value()` 用 `config_path = user_config_path if user_config_path.exists() else project_config_path`。当（测试隔离的）`HERMES_HOME` 没有 `config.yaml` 时，它写入——并**创建**——`<repo>/cli-config.yaml`（已安装包/源码树内的项目配置）且从不清理。`scripts/run_tests.sh` 每个测试文件跑在自己的子进程里，但 8 个并行 worker 共享工作树，泄漏文件污染任何并发跑、其 `load_cli_config()` 回退到 `project_config_path` 的测试——正是 `--ignore-user-config` 读取路径。`tests/test_tui_gateway_server.py` 是写入方。

**修复**：只在项目 `cli-config.yaml` **已存在**时才写入它；否则写入（并创建）用户配置。`save_config_value()` 不再在源码树里创建文件。

**是否上游？** 是——往已安装包目录写配置是通用 footgun，与 CN 无关。

---

### P-044：agent 初始化期的 WMI + SSL Windows 开销——无 WMI 平台检测 + 记忆化 CA guard

**现象。** agent-init 火焰图（`.plans/15-WMI-SSL-Windows-Overhead.md`）标出两个 Windows 特有 C 扩展热点：`_wmi.exec_query`（自耗时 2.91%）与 `_ssl._SSLContext.set_default_verify_paths`（8.19%），外加 import loader 成本（`_io.open_code` 4.39%、`builtins.compile`）。

**根因（在 py3.14/Windows 上实测确认，非照搬计划）。**
1. **WMI。** 计划归咎于 `import wmi` / `pywin32` 依赖，但本树**没有** `wmi` 包的使用。真正来源是 stdlib `platform` 模块：Python 3.12+ 的 `platform.uname()` 通过 `_wmi` 内建解析 Windows release + machine 字段（`win32_ver` 与 `_get_machine_win32` 各发一次 `_wmi.exec_query`，冷 ~40-90ms）。`platform.system()` 与 `platform.release()` 都会构造 `uname()`，而 fork 在十来个文件的**模块作用域**跑 `_IS_WINDOWS = platform.system() == "Windows"`——于是 `from run_agent import AIAgent` 导入级联里 WMI 服务被命中**两次**（定位到 `hermes_cli/config.py` + `agent/credential_pool.py`），`agent/prompt_builder.py` 的 `platform.release()` 每次初始化构建 system prompt 时又命中**一次**。
2. **SSL。** `agent/ssl_guard.verify_ca_bundle()` 每次 `AIAgent` 构造都跑（`agent/agent_init.py`），并构建一次性 `ssl.create_default_context(cafile=certifi.where())` 来证明 CA bundle 可加载——Windows 上 ~225ms，每次初始化都重复支付（实测冷 244ms、暖 225ms），网关/subagent 集群每个 agent 都重载 CA 存储。

**修复。**
1. **`platform_utils.py`（新增，无 WMI、纯 stdlib）。** `is_windows()` / `is_macos()` / `is_linux()` 从 `sys.platform` 常量解析（不构造 `uname()` → 不触发 WMI）；`windows_release()` 用 CPython 自带的版本→名称表从 `sys.getwindowsversion()` 的 build 号推导出与 `platform.release()` 相同的 release 标签（如 `"11"`），跳过 `win32_ver` 的 WMI 探测；`host_os_label()` 供 prompt/诊断主机行使用。7 处模块级 `_IS_WINDOWS = platform.system() == "Windows"` 标志（`hermes_cli/config.py`、`hermes_cli/dep_ensure.py`、`tools/code_execution_tool.py`、`tools/environments/{powershell_session,local}.py`、`tools/process_registry.py`、内置 `plugins/platforms/whatsapp/adapter.py` 的 inline `sys.platform`）以及 `prompt_builder` 的 Windows 主机行改用它。**结果：导入级联与 `build_environment_hints()` 期间 `_wmi.exec_query` 调用 0 次**（原来 2 + ≥1）；主机行逐字节一致（`Host: Windows (11)`）。
2. **记忆化 CA guard（`agent/ssl_guard.py`）。** `verify_ca_bundle()` 以四个 CA 环境变量 + certifi bundle（path/size/mtime）的廉价指纹为键缓存*成功*结论。CA 配置不变时每个进程只校验一次（重复调用 ~0.05ms vs ~225ms）；任何变更（改了环境变量、重装 certifi）都会击碎指纹并重新校验+重新抛出，早破 bundle 的错误契约完整保留。`_reset_ca_bundle_cache()` 是测试钩子；`test_ssl_ca_guard.py` 增加 autouse reset 保证既有 broken-bundle 用例保持隔离。
3. **`scripts/precompile.py`（新增）。** 基于 `compileall` 的提前 `.pyc` 预热，面向源码运行布局（CN fork 的 Windows 默认），首次 import 不必在热路径上付 `builtins.compile` + `_io.open_code` + Defender 扫描新写字节码。坏源文件绝不致命（报告后返回非零）。`platform_utils` 登记进 `pyproject.toml` `py-modules`。
4. **Defender 提示。** `tools/environments/windows_env.suggest_defender_exclusion()` 返回可执行的"把 HERMES_HOME 加入 Defender 排除"建议，经 `hermes doctor` 展示（`_check_windows_defender_hint()`），与 P-042 既有的 `FILE_ATTRIBUTE_TEMPORARY` 写路径提示互为补充。仅提示——不动任何 Defender 设置。

**结果。** WMI 从导入级联与 prompt 构建路径中彻底移除（0 次查询）。每次 init 重复的 SSL 证书加载消失（每个进程至多付一次，而非每个 `AIAgent` 付一次）。两个火焰热点在主导测量套件的重复 init / 网关路径上均朝计划的 `< 0.1%` 目标收敛。

**测试。** `tests/tools/test_wmi_ssl_windows_overhead.py`（新增）：`platform_utils` 与 `sys.platform` 一致且从不调用 `platform.uname()`/`system()`；`windows_release()` 在 Windows 上等于 `platform.release()` 且无 `_wmi`（Windows-only 追踪）；Windows-only 子进程守卫断言 import `run_agent` + `build_environment_hints()` 发出**零**次 `_wmi.exec_query`；`precompile_all` 产出 `.pyc`、语法错误时返回 `False`（不抛）、缺失路径 no-op；Defender 提示平台门控。`tests/agent/test_ssl_ca_guard.py`（扩展）：记忆化在配置不变时跳过重校验、环境变量变更时重校验并抛出、指纹追踪 certifi 身份——所有既有 SSL-guard 用例仍通过。`ruff check` 干净。

**是否上游？** 是——`platform_utils`（`sys.platform` 惯用法在每个 OS 上都正确）与 CA-guard 记忆化与 provider/OS 无关；只有 WMI 的*量级*是 py3.12+-on-Windows 特有。相关：P-042（Windows 子进程 spawn 开销）、P-043（首次分发延迟）。

---

### P-045：Flame-graph import 系统优化

**现象 / 目标。** agent-init 火焰图（`.plans/16-Flame-Graph-Import-Optimizations.md`）把冷启动 agent 的 **~71%** 归因于 Python import 系统——主要是 `builtins.compile`（~10.82%）、`_io.open_code`（~4.39%）、模块搜索统计 `nt.stat`（~1.6%）/ `nt._path_exists`（~2.1%）。

**计划里被证伪的两条（实现前先验证过）。** 计划五条建议中有两条在本树不成立，故刻意未按原文实现：
- *"在热路径上用 `type(x) is dict` 换掉 `isinstance`。"* 基准：`type(d) is dict`（0.069s/5M）并不比 `isinstance(d, dict)`（0.067s/5M）快——CPython 已有快路径——而保正确性的组合形式（`type() is ... or isinstance(...)`）反而更慢（0.085s）。照搬会拖慢热路径。
- *"初始化期间的 34.4 万次 isinstance / 15.3 万次 getattr 是冗余计算。"* 这些绝大多数是第三方**import 期**类构造（pydantic、OpenAI SDK），不是 Hermes 代码。真正的对策是不急切 import 它们——已由懒代理 + 懒工具索引（P-043）完成，并经下面的加速器强化——而不是改写我们自己的类型检查。

**实现内容。**
1. **`import_accelerator.py`（新增）——一方 meta-path finder。** `sys.meta_path[0]` finder 用 dict 查找解析 Hermes 自身的顶层模块/包，跳过标准机制按 `sys.path` 逐条 `FileFinder` 扫描的开销。关键正确性选择：**精选 allow-list**（镜像 pyproject `py-modules` + `packages.find`——盲目 `os.listdir` 会把仓库本地 `packaging/` 目录注册进去并遮蔽真正的 PyPI `packaging` 依赖）；**包优先于模块**（仓库根 `agent.py` harness 绝不赢过 `agent/` 包）；`spec_from_file_location` 保留 stdlib `SourceFileLoader` 及其 `.pyc` 缓存；每个非一方名字 O(1) 透传（单次 dict miss）；`HERMES_DISABLE_IMPORT_ACCELERATOR` 可关。从 `hermes_bootstrap`（每个入口最早的 import）安装，并从 `run_agent` 幂等安装。映射在构建期校验一次，不做逐 resolve 的 re-stat——早期版本做了，反而比 PathFinder 的缓存目录命中更贵、让暖 import 慢 ~1.7%；去掉后加速器在暖树上中性到更快，冷启动/`sys.path` 把 site-packages 排前时仍绕过扫描。
2. **`scripts/precompile.py` —— 幂等 + 后台。** 新增 `precompile_if_needed()`（源码+解释器指纹与磁盘 stamp 一致时跳过）、`precompile_in_background()`（daemon 线程）、`[tool.hermes.precompile]` 目标读取、`--if-needed`/`--force` CLI。`hermes_bootstrap.maybe_precompile_on_start()` 仅当 `HERMES_PRECOMPILE_ON_START` 已设且不在 pytest/冻结下运行后台预热（隔离的逐文件测试运行器给每个文件一个全新临时 `HERMES_HOME`，无门控的预热会在每个子进程里起一个 `compileall` 线程）。`pip install` 已对安装包做字节编译；这是给源码运行布局（CN fork 的 Windows 默认）准备的——那里首次 import 否则要付 `builtins.compile`。
3. **每请求微优化。** `agent/message_utils.get_tool_call_function_and_id()` 把 `sanitize_api_messages` 此前对每个 tool_call 做的两次 `isinstance(tc, dict)` 分发（`get_tool_call_function` + `get_tool_call_id`）合并为一次；sanitizer 在每次 LLM 请求前对整段历史运行，因此对工具调用密集的会话有可测收益。`tools/registry.get_definitions()` 现在只在短暂锁内快照*被请求的*条目，不再每次调用物化整个 ~250 工具注册表的 `{name: entry}` map。

**测试。** `tests/test_import_accelerator.py`（新增）：精选名构建（agent 是包、`agent.py` 永不是模块、`packaging`/`tests` 永不注册）、保留 `.pyc` 的 `SourceFileLoader` spec、O(1) 透传、子模块透传、幂等安装/卸载、env 关闭、以及 `test_import_hook_bypass`——子进程证明把仓库根从 `sys.path` 剥掉后模块仍经加速器导入（且不咨询 `PathFinder`），而移除加速器的对照组失败。`tests/test_precompile.py`（新增）：指纹稳定性/敏感性、`precompile_if_needed` 编译→跳过→变更后重编译→force、后台完成、`precompile_all` 回归。`tests/agent/test_message_utils.py`（扩展）：`test_isinstance_caching` 把融合访问器与两个分离访问器在 dict/SDK 对象/畸形输入上逐字节钉死。所有既有 sanitizer/registry/bootstrap/懒 import 不变量测试仍通过（受影响范围 581 个）；`ruff check` 干净。

**可上游？** 加速器、precompile 幂等与 sanitizer/registry 微优化与 OS 无关；冷启动量级是 Windows/源码运行特有。相关：P-043、P-044。

---

### P-051：给每一条文本模式子进程管道钉死解码——消灭 GBK `_readerthread` UnicodeDecodeError 这一类 bug

**现象。** zh-CN Windows（ANSI 代码页 936、py3.14 无 UTF-8 模式）上，CLI 在第一个对话回合结束后打印 `Exception in thread Thread-N (_readerthread): UnicodeDecodeError: 'gbk' codec can't decode byte 0xae ... illegal multibyte sequence`，随后继续运行，但受影响输出被静默丢弃。

**根因。** `subprocess.run(..., text=True)` 未传 `encoding=`/`errors=` 时，用 `io.TextIOWrapper` 以 `locale.getpreferredencoding(False)` 包住子进程管道——zh-CN Windows 上即 cp936/GBK。现代跨平台子进程（git、python、node、uv、docker、gh、…）无论 ANSI 代码页如何都写 UTF-8；一个 GBK 非法字节（如实 = `E5 AE 9E` 的中间字节 `0xAE`，中文提交信息常见）在 CPython 的 daemon `_readerthread`（`subprocess.py`: `buffer.append(fh.read())`）内抛错，只能经 `threading.excepthook` 浮出，且 `result.stdout is None`——调用方永远看不到异常。报告的实例是会话开始的 workspace 快照（`agent/system_prompt.py` → `coding_system_blocks()` → `agent/coding_context.py::_git()` 对含中文提交信息的仓库跑 `git status --porcelain=2 --branch` / `git log -3 --pretty=%h %s`）：reader 线程死亡后 `_git()` 撞上 `None.strip()`，`system_prompt.py` 的宽 `except` 吞掉——整个 workspace 块从 system prompt 里静默消失。用 `python -X utf8=0`（→ cp936）对输出 UTF-8 CJK 的子进程逐字节复现：同样的 `subprocess.py:1613 _readerthread` 帧、同样的 `0xae` 消息、之后 `stdout is None`。

**修复。** 类级修复，不止报告的那个点——全仓库 AST 审计发现 shipped 代码中 **269 处文本模式子进程调用点**缺显式解码（另有 3 处藏在 `**kwargs` dict 展开后面）：
1. **按约定输出 UTF-8 的子进程**（git、gh、rg、python/pip/uv、node/npm、docker、ssh/scp、systemctl/loginctl/launchctl/ps/lsof、tmux、s6、cosign、codex/claude CLI、secret CLI、hermes 自身、用户 shell 片段、…）：钉 `encoding="utf-8", errors="replace"`——按子进程实际写出的内容解码，坏字节永远杀不死 reader 线程。
2. **输出 OEM/ANSI 代码页的 Windows 原生工具**（`tasklist`、`taskkill`、`netstat`、`where`）：只钉 `errors="replace"`——locale 解码在那里才是忠实选择（本地化消息与中文路径保持正确）；唯一的 bug 是严格性。这与 `gateway/status.py::terminate_pid` 既有的文档化加固一致。
3. 三处 `**kwargs` dict 展开点（`hermes_cli/web_server.py::_fs_git_branch`、`tools/tts_tool.py::_run_command_tts`、`tools/transcription_tools.py::_run_command_stt`）在各自 dict 内补同样钉法。
4. 顺手修复：`hermes_cli/_subprocess_compat.py` 的（当前未用的）drop-in wrapper 用了 `subprocess.run`/`Popen` 却 `import subprocess`——潜在 `NameError`；补上 import，wrapper docstring 现在记录文本模式解码陷阱。

**回归防护。** `tests/test_subprocess_text_pipe_decoding.py`（新增）：(a) 对 shipped 代码的静态 AST 不变式——每个文本模式 `subprocess.run/Popen/call/check_call/check_output` 调用（含具名 dict 与 inline `**dict` 展开形式）必须传显式 `errors=`——让这一类不可能经未来代码或上游同步卷土重来；(b) 行为测试把对任何 codec 都非法的字节经 `coding_context._git` 管道，断言线程存活且 UTF-8 CJK 完整落地（解码已钉死，任何 locale 下确定性通过）。沿用 `tests/tui_gateway/test_slash_worker_utf8_decode.py` 的先例（同一 bug 类更早为长生命周期子进程管道修复，commit c210b9621）。

**测试。** 新守卫：2 通过。受影响范围扫描（330 个测试文件，覆盖每个被编辑模块——clipboard、docker/ssh/singularity 环境、kanban、gateway、tui_gateway、cli worktree 助手、main 更新流程、tools_config、lazy_deps、computer_use、cron、webhook、transcription/tts/voice、secret 源、插件）在分支与未改动基线上都跑：基线 206 失败 vs 分支 209 失败，其中 205 个是相同的既有批量组合污染（每个抽查失败单独跑都通过；如 `test_update_autostash.py` 的 4 个文件级失败在未改动的基线上也复现）。4 个分支独有失败是 exact-kwargs change-detector 断言（`test_webhook_integration`、`test_whatsapp_connect`、`test_update_autostash`、`test_docker_environment`）——更新为新契约；whatsapp `_terminate_bridge_process` 的 taskkill 点移到 locale 风格（仅 `errors="replace"`）组以匹配其 Windows 原生输出。1 个基线独有失败是 flaky 污染。`scripts/check-windows-footguns.py --all` 干净（793 文件）；`ruff check .` 构造上干净（只开 PLW1514/`open()`-encoding，本次未触及；CI lint.yml 是执行门）。

**是否上游？** 是——逐点钉法与 AST 不变式测试 OS 无关（崩溃只在非 UTF-8 Windows locale 上*显现*，但把 UTF-8 管道按 locale 解码在哪里都是错的）。相关：c210b9621（slash worker + Copilot ACP 管道钉法）、P-042（Windows 子进程开销）、P-019（Windows shell 处理）。

---

## Windows 兼容性补丁

以下补丁由 Maxwell Geng 贡献，用于提升 Windows 平台的一等支持体验，均可向上游提交。

### `282cfeeca` — 为 `shlex.split` 增加 `posix` 选项以兼容 Windows

**做了什么**：在代码库所有涉及到 `subprocess` 的 `shlex.split()` 调用中增加 `posix=os.name == "posix"` 参数，防止 Windows 路径中的反斜杠被误解析为转义字符。

**涉及文件**：
- `agent/copilot_acp_client.py`
- `agent/shell_hooks.py`
- `agent/subdirectory_hints.py`
- `cli.py`
- `gateway/run.py`
- `hermes_cli/auth.py`
- `hermes_cli/gateway_windows.py`
- `hermes_cli/memory_setup.py`
- `tools/transcription_tools.py`

**上游状态**：建议上游。纯 Windows bug 修复，POSIX 下行为无变化。

### `ada59ec36` — 修复 10 个在 Windows 上失败的测试，使其跨平台

**做了什么**：让 10 个测试用例在 Windows 上正确通过或优雅跳过：

| 测试 | 修复方式 |
|---|---|
| `test_make_run_env_appends_homebrew_on_minimal_path` | Windows 下跳过（POSIX PATH 注入在该平台被有意跳过）。 |
| `test_returns_root_when_only_root_exists` | Windows 下对 cwd 做 `os.path.normpath()`，使带正斜杠的路径能正确走到文件系统根目录。 |
| `test_close_stdin_allows_eof_driven_process_to_finish` | 用 `cat` 代替 `python3`；PTY 库缺失时跳过；winpty 传 `str`、ptyprocess 传 `bytes`。 |
| `test_popen_killed_when_thread_creation_fails` | 仅在 `os.getpgid` 存在时（POSIX）patch。 |
| `test_popen_killed_when_write_checkpoint_fails` | 仅在 `os.getpgid` 存在时（POSIX）patch。 |
| `test_kill_detached_session_uses_host_pid` | 直接 mock `_terminate_host_pid`，不再依赖内部 `psutil` 调用。 |
| `test_windows_does_not_call_psutil` | 增加 `pytest.importorskip("psutil")`。 |
| `test_posix_walks_tree_and_terminates_children_then_parent` | 增加 `pytest.importorskip("psutil")`。 |
| `test_posix_no_such_process_swallowed` | 增加 `pytest.importorskip("psutil")`。 |
| `test_posix_oserror_falls_back_to_os_kill` | 增加 `pytest.importorskip("psutil")`。 |

**涉及文件**：
- `tests/tools/test_local_env_blocklist.py`
- `tests/tools/test_process_registry.py`
- `tools/environments/local.py`
- `tools/process_registry.py`

**上游状态**：建议上游。扩展 CI 到 Windows 覆盖，不改变生产行为。

### `1a75a7672` — ~~Windows 下自动安装 Git-Bash，并将 Windows 风格命令转换为 POSIX 风格~~ **已删除**

**状态**：已移除。Git for Windows 自动安装与 Git Bash 回退支持已被删除，改为原生 PowerShell 执行（见 P-016）。以下文件已移除：
- `tools/environments/_install_git.py`
- `tools/environments/_process_bash_command.py`

Windows 平台现在要求使用 PowerShell 7（`pwsh`）或 Windows PowerShell（系统 PowerShell）。Shell 通过 `_find_pwsh` 解析，不再自动安装——PowerShell 属于 Windows 标准组件，默认已可用。

### P-058：Windows 默认 shell 偏好——git-bash → pwsh → powershell 5.1

**现象 / 需求。** P-050 恢复了 Git Bash 作为 Windows 上*可选显式* shell（`HERMES_SHELL_TYPE=bash`），P-052 又移植了 kimi 的完整 bash 发现链，但默认/`auto` 解析仍然直奔 PowerShell 7 → Windows PowerShell 5.1，从不探测 bash。装了 Git for Windows 的 Windows 用户（很常见——装 git 就带 bash，且常为 VCS 操作而装）即使机器上有完全可用的 POSIX shell，默认仍拿到 PowerShell 语法提示与 PowerShell 后台包装脚本。`_detect_shell_for_description` 对显式 `HERMES_SHELL_TYPE=bash` 也仍返回 `"powershell"`（P-019 遗留守卫），导致模型被要求用 PowerShell 语法、实际却跑在 bash 下。

**改动内容。**

1. **`tools/environments/local.py` —— 非抛错 `_find_bash` 探测。** `_find_bash()` 增加 `raise_if_missing: bool = True`；两处现有 raise 点（ASLR/msys 失败类与最后的 `RuntimeError("Git Bash is not found ...")`）在 `raise_if_missing is False` 时改为返回 `None`。所有既有调用者保持原来的提示性 RuntimeError（默认参数保留行为）。默认/auto 解析路径用非抛错探测实现“有 git-bash 就用”。
2. **`tools/environments/local.py` —— `_resolve_shell()` 默认 git-bash 优先。** Windows `auto`/未设置现在解析为：`_find_bash(raise_if_missing=False)` → `("bash", path)`；否则 `_find_pwsh()` → `("pwsh", path)`；否则 `_find_powershell()` → `("powershell", path)`。显式 `bash`（缺失时抛错，不变）、显式 `pwsh`/`powershell`（pwsh → 5.1，**绝不** git-bash，不变）、未知值（抛错，不变）三个分支不动。相关 docstring 更新（`_resolve_shell`、`_find_shell`、`LocalEnvironment`）。
3. **`tools/environments/local.py` —— 新增 `_build_bash_background_script()`。** 镜像 `_build_powershell_background_script()` 用于 Windows 上的 bash 后台路径：构造 `bash -lc` 脚本——`cd` 到 cwd（带守卫，`|| exit 126`）、通过 `eval` 执行命令、在提供 cwd_file 时把 `pwd`（MSYS 形式；消费端经 `_msys_to_windows_path` 转换）写入、以命令的 `$?` 退出。复用 `_quote_bash_path`/`_bash_safe_path` 嵌入路径。（本基底不存在 P-052 的 MSYSTEM 辅助函数——wrapper 与 `BaseEnvironment._wrap_command` 保持一致。）
4. **`tools/terminal_tool.py` —— shell 感知的描述检测。** `_detect_shell_for_description()` 镜像解析顺序：显式 `bash` → `"bash"`（移除遗留 `"powershell"` 守卫）；`auto`/未设置 → 有 bash 用 bash，否则 pwsh，否则 powershell；显式 `pwsh`/`powershell` 不变；未知值按 auto 处理（描述路径不得抛错）。`_build_dynamic_terminal_description` 不变（bash 已映射到 Linux/bash 句子，对 git-bash 正确）。
5. **`tools/process_registry.py` —— 后台与 PTY 拉起跟随 shell。** `_spawn_windows_powershell_local()` / `_spawn_windows_pty_local()` 按 `shell_type` 分支：`"bash"` → `_build_bash_background_script()` + `[shell_path, "-lc", script]`（非 PTY 的 Popen 保持相同的 creationflags/env/管道处理；PTY 经 winpty 同参数）；PowerShell 保留既有 `-NoProfile` 路径。PTY docstring 改为 shell 通用。
6. **`agent/prompt_builder.py` —— auto shell 提示优先 git-bash。** `build_environment_hints()` 的 auto/未知分支按新默认选提示：存在 git-bash → `_WINDOWS_BASH_SHELL_HINT`；否则 PATH 有 pwsh → `_WINDOWS_PWSH_SHELL_HINT`；否则 `_WINDOWS_POWERSHELL_SHELL_HINT`。显式 `bash`/`powershell` 分支不变。
7. **文档与注释清扫。** `website/docs/user-guide/windows-native.md` + zh-Hans + `website/docs/reference/environment-variables.md` + zh-Hans + README 系列更新为新默认顺序；`hermes_cli/gateway.py`、`scripts/keystroke_diagnostic.py`、`tools/environments/base.py`、`hermes_cli/config.py` 的过期注释改为“存在时默认 git-bash 优先”。

**有意不动的部分。** Desktop 预检（`apps/desktop/electron/main.ts`）——需求范围限定在 `tools/terminal_tool.py` + `tools/environments/local.py`；记为后续项，保持桌面预检一致。不自动安装 git-bash（kimix 也不自动装；需求是“若存在可用的”）。非 Windows 解析不变（恒为 bash）。`_resolve_pwsh_session_reuse` / `_resolve_cmd_fast_path` 仍是 PowerShell 专属（bash 会话跳过）。

**测试。** `tests/tools/test_shell_resolution.py`（auto→bash 胜出 / auto→pwsh 回退 / 显式分支不变 / `TestBuildBashBackgroundScript`）、`tests/tools/test_terminal_dynamic_description.py`（显式 bash → `"bash"`、auto git-bash → `"bash"`、未知 → auto、Windows git-bash 描述句）、`tests/tools/test_process_registry.py`（bash 后台拉起用 `bash -lc` 包装、无 PowerShell 标志；PTY 变体）、`tests/agent/test_prompt_builder.py`（auto + git-bash → bash 提示）、`tests/tools/test_find_bash_optional_probe.py`（新增——`_find_bash(raise_if_missing=False)` → `None` 而非抛错，覆盖 ASLR/未找到/坏 bash 路径；仅存在于 dev-020 的 `test_local_git_bash_port.py` 在本基底不存在）。完整 `tests/tools` + `tests/agent` 套件通过；`ruff check .` 干净。

**是否可上游？** git-bash 优先的*默认*是刻意分叉（上游 Windows shell 默认是 PowerShell 优先，P-016/P-019/P-050）。非抛错 `_find_bash` 探测与 shell 感知的后台/PTY 拉起是通用改进，适合上游。

# 2026-09-09 社区 P0/P1 修复验收

配对 Desktop 基线 `f7f36bf5`，Core 基线 `d0575cb0`，独立分支 `fix/issue-triage-20260909`。完整 153 条分类与逐条处置记录见配对 Desktop 仓库的 `docs/issue-triage-20260909.md`。

本仓新增修复：冻结 cron Python 脚本改为独立 `__run-script` 子进程，超时结束进程树并保留主进程输出和参数；安装器使用 Python 3.14 / CN 仓库（移植社区 PR #168）；Linux 发行基线固定 Ubuntu 22.04；冻结包补齐磁盘发现所需 tools 源码及 Windows pywinpty 的 OpenConsole.exe / winpty-agent.exe。

Windows 对照：官方 cn.9 网关启动 NameError 可复现，main 冻结候选正常启动。冻结候选完成 cron 执行/超时、实际 MCP stdio 调用、89 工具发现、插件 doctor、Node ConPTY 和 Desktop 原生交互。源码进程树中止测试运行在真实 Windows。新增发行 smoke 直接运行冻结 executable，避免把源码测试当成成品验证。

最小测试记录：cron/CUA/安装器 4 文件 48 通过 1 跳过；配置路由 9 通过；会话/供应商/工作区/运行兼容 4 文件 31 通过；微信 HTTP 超时 4 通过。Windows 4 文件 50 通过 4 跳过，另有真实终端 1 通过。冻结产物的后续新增源码仅涉及打包脚本/测试/文档，Python 产品代码与本次冻结编译源码一致。

未发布正式版本。Intel/macOS 12 与 Ubuntu 22.04 成品仍需对应平台验收；#141 缺原现场线程栈；#136 按用户要求跳过。原始证据位于协作工作区 `CNDesktop/artifacts/issue-triage-20260909/`。

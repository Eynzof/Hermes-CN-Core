# 2026-11-10 dev-fix 主分支整合

整合目标：将 dev-fix 的 `8458689311` 并入从 `ebe22de2b6` 创建的整合分支，保留双方提交历史。

本轮处理 35 处 Git 合并冲突，保留主分支的 Desktop 版本探针、冻结运行时打包、PM 依赖和工具恢复修复。采用 dev-fix 的新网关及 slash worker 结构，保留其进程所有权机制、provider/base_url 传递和冻结运行时入口，不恢复已被新模块替代的旧实现。

CI 延续当前 fork 的标准 GitHub 托管运行器与 `All required checks pass` 检查名，保留十二个 Linux 测试切片。将贡献者归属与精确供应链审核记录迁入上游合并后的 Repository guards；不调整代码健康基线、不关闭现有检查。

依赖通过 PM 从官方 PyPI 重新锁定，保留主分支的 Mem0 可选后端、Windows ARM64 grpcio 构建约束及 hermes_embedded 包发现。

本地验证限定在冲突相关测试：9 个文件，修正扫描报告格式与 pnpm 锁文件迁移后，相关测试共 279 项通过；29 项按宿主平台跳过。Web 构建相关测试使用仓库锁定的 pnpm；未运行视觉测试。完整跨平台结果以本次 PR 的 GitHub Actions 为准。

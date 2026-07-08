# Agent

| 标题 | 描述 | 状态 |
|------|------|------|
| qli-master | Quantized Lightning Indexer (QLI) 算子完整知识库。聚合核心概念、流水线同步、Buffer 管理、计算算法、Mask/变长序列、CV 比例适配、MXFP8 修复、V2 Metadata、Tiling/性能、同步陷阱、迁移经验 11 个模块。 | 可用 |

# Skills

| 标题 | 描述 | 状态 |
|------|------|------|
| code-commit-analyzer | PR 知识提炼技能。从 Gitee（含企业版，通过 MCP server）/GitCode 等平台收集用户全部 PR（含已合入和未合入），代码级深度分析后提炼技术知识点，输出 knowledge_detail.md 和 resume_skills.md。支持断点续传。 | 好用 |
| code-commit-reviewer | PR 知识提炼产出检视技能。对 code-commit-analyzer 的输出目录进行结构化审查，验证目录合规性、两份最终文件的内容质量、中间产物完整性和代码级分析深度。 | 可用 |
| daily-distill-runner | 每日定时任务调度（常驻循环）。每晚 22:00 准时启动一组任务，任务完成后不打断对话，自动 sleep 到下一个 22:00 继续触发，无限循环。等待用纯 bash sleep，不消耗 AI token。 | 待验证 |
| cannbot-token-guard | Cannbot 每日 Token 用量守卫（两阶段）。阶段一启动检测验证 Cannbot provider 并记录开始日期；阶段二任务开始时激活监控，每 10 分钟检查当日 token 消耗，达 95%（9500 万/1 亿）或当前时间不在 22:00-24:00 任务窗口即停。 | 待验证 |

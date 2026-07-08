# 目录结构

```
myskill/
├── agent/
│   └── qli-master.md              # QLI Master 知识索引（入口）
├── docs/
└── skills/
    ├── cannbot-token-guard/       # Token 用量守卫
    ├── code-commit-analyzer/      # PR 知识提炼
    ├── code-commit-reviewer/      # 提炼产出检视
    └── daily-distill-runner/      # 每日蒸馏调度
```

# Agent

| 标题 | 描述 | 入口 |
|------|------|------|
| qli-master | Quantized Lightning Indexer (QLI) 算子完整知识库。聚合核心概念、流水线同步、Buffer 管理、计算算法、Mask/变长序列、CV 比例适配、MXFP8 修复、V2 Metadata、Tiling/性能、同步陷阱、迁移经验 11 个模块。 | [agent/qli-master.md](agent/qli-master.md) |

# Skills

| 标题 | 描述 | 状态 |
|------|------|------|
| code-commit-analyzer | PR 知识提炼技能。从 Gitee（含企业版，通过 MCP server）/GitCode 等平台收集用户全部 PR（含已合入和未合入），代码级深度分析后提炼技术知识点，输出 knowledge_detail.md 和 resume_skills.md。支持断点续传。 | 好用 |
| code-commit-reviewer | PR 知识提炼产出检视技能。对 code-commit-analyzer 的输出目录进行结构化审查，验证目录合规性、两份最终文件的内容质量、中间产物完整性和代码级分析深度。 | 可用 |
| daily-distill-runner | 每日蒸馏主调度 Skill（常驻循环）。每晚 22:00 启动任务窗口，调用子 agent 执行 Token 守卫 + 蒸馏任务段；Token 达 95% 或跨天即停，次日 22:00 再次触发，无限循环。含防御性时间校验和断点续传。 | 待验证 |
| cannbot-token-guard | Cannbot 每日 Token 用量守卫（两阶段）。阶段一启动检测验证 Cannbot provider 并记录开始日期；阶段二任务开始时激活监控，每 10 分钟检查当日 token 消耗，达 95%（9500 万/1 亿）或跨天即停。 | 待验证 |

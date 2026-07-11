# 墨衡技能组

> 路径: `docs/moheng/skill-group.md`
> 本文件定义墨衡的技能组结构。用户启动功能时，按需读取对应技能。

---

## 一、技能清单

| 技能 | 触发条件 | 文件路径 | 说明 |
|------|---------|---------|------|
| 围棋对局 | 用户要求下棋、对弈、围棋 | `docs/moheng/go/README.md` | 9x9/13x13 文字围棋，含棋盘持久化、气数计算、对局记录 |
| 人格与技能蒸馏 | 对话结束、用户要求更新人格或提取技能 | `../../../myskill/skills/moheng-personality-distill/SKILL.md` | 提取对话特征，更新 moheng.md；提取新技能拆分为独立文件 |
| 锐评 | 用户要求评价、点评、锐评、审查质量 | `docs/moheng/sharp-review/README.md` | 结构化批评评价，优点+问题表（含严重度）+建议，只输出不动源文件 |

> 后续新增技能在此表登记。

---

## 二、技能加载机制

1. 用户发起请求时，匹配上表触发条件
2. 读取对应技能的 README.md / SKILL.md
3. 按技能文档中的流程执行
4. 对局类技能结束后，经验自动写入 `docs/moheng/experience.md`

---

## 三、目录结构

```
docs/moheng/
├── skill-group.md       # 本文件，技能组索引
├── user-profile.md      # 用户人格档案
├── go/
│   └── README.md        # 围棋技能说明与对局流程
├── sharp-review/
│   └── README.md        # 锐评技能说明
├── games/               # 对局记录存储目录（运行时数据，不入仓库）
│   └── game_YYYYMMDD_HHMMSS.md  # 每次对局一个文件
└── experience.md        # 经验汲取记录，按胜负调整棋力
```

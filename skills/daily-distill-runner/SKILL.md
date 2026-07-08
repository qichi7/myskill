---
name: daily-distill-runner
description: 每日蒸馏主调度 Skill（常驻循环）。每晚 22:00 启动任务窗口，调用子 agent 执行 Token 守卫 + 蒸馏任务段；Token 达 95% 或跨天即停，次日 22:00 再次触发，无限循环。含防御性时间校验（0:00–22:00 直接中断，防止提前消耗新一天额度）和断点续传（按段打标记，中断后从最近已完成段继续）。触发关键词：每日蒸馏、常驻任务、22点启动、夜间任务循环、断点续传蒸馏。
---

# 每日蒸馏主调度 Skill（常驻循环）

本 Skill 是常驻运行的编排器，不主动终止。核心职责：
1. **定时等待**：阻塞至每晚 22:00（不消耗 AI token，纯 bash sleep）
2. **防御性时间校验**：若被唤醒时处于 0:00–22:00，立即中断，防止提前消耗新一天额度
3. **子 agent 编排**：22:00 到达后，用 Task 工具启动子 agent 执行实际任务
4. **断点续传**：每段任务打标记，中断后从最近已完成段继续
5. **无限循环**：任务结束后回到等待，次日 22:00 再次触发

## 核心参数

| 参数 | 值 |
|------|-----|
| 任务窗口 | 每日 22:00–23:59（次日 0:00 前结束） |
| 防御性禁止时段 | 0:00–21:59（此区间被唤醒则立即中断） |
| 等待轮询间隔 | 10 分钟（600 秒） |
| 等待安全超时 | 25 小时（90000 秒） |
| 子 agent 类型 | general |
| Token 停止阈值 | 95%（由 cannbot-token-guard 守护） |
| 跨天停止 | 日期变更即停（由 cannbot-token-guard 守护） |

## 脚本路径

> 脚本路径相对于本 Skill 目录（即 SKILL.md 所在目录）解析。跨 Skill 引用使用 `../{skill名}/scripts/`。

```bash
RUNNER_SCRIPTS=scripts
GUARD_SCRIPT=../cannbot-token-guard/scripts/check_guard.sh
```

| 脚本 | 用途 |
|------|------|
| `wait_until_22.sh [last_run_date]` | 阻塞至 22:00（新日期），输出 `TRIGGER` 或 `TIMEOUT` |
| `runner_state.sh {init\|scan\|mark\|clear\|set_last_run\|get_last_run\|log}` | 断点状态管理 |

## 状态目录

```
~/.cache/daily-distill-runner/
├── runner_state.json        # { "last_run_date": "2026-07-06", "current_seg": 2 }
├── segments/
│   ├── .SEG_TOKEN_GUARD     # Token 守卫已启动
│   ├── .SEG_1               # 段1已完成
│   └── .SEG_2               # 段2已完成
└── logs/
    └── 2026-07-06.log       # 当日执行日志
```

## 主循环工作流程

```
Skill 被激活
  │
  └─ 进入无限循环 LOOP ─────────────────────────────────┐
      │                                                 │
      ├─ 步骤1：读取上次运行日期                          │
      │   bash runner_state.sh get_last_run             │
      │                                                 │
      ├─ 步骤2：阻塞等待 22:00                           │
      │   bash wait_until_22.sh {last_run_date}         │
      │   ⚠️ 必须设 timeout=86400000（24小时）           │
      │   ├─ TRIGGER → 继续                              │
      │   └─ TIMEOUT → 记录日志，回到步骤2                │
      │                                                 │
      ├─ 步骤3：记录触发日期                              │
      │   today=$(date +%Y-%m-%d)                       │
      │   bash runner_state.sh set_last_run {today}     │
      │   bash runner_state.sh log "任务窗口触发"        │
      │                                                 │
      ├─ 步骤4：调用子 agent 执行任务                     │
      │   Task 工具，subagent_type=general              │
      │   prompt = 下方「子 agent 执行指令」全文          │
      │   等待子 agent 返回执行摘要                       │
      │                                                 │
      ├─ 步骤5：记录执行摘要                              │
      │   bash runner_state.sh log "任务完成: {摘要}"   │
      │                                                 │
      └─ 回到步骤1（无限循环）────────────────────────────┘
```

## 执行规则（给 AI 的指令）

当本 Skill 被激活时，**必须严格遵守**以下规则：

### 主循环规则

1. **进入无限循环**：本 Skill 不主动终止。完成一轮后回到等待状态，等待次日 22:00。

2. **等待时设长超时**：调用 `wait_until_22.sh` 时，bash 工具的 `timeout` 参数必须设为 `86400000`（24小时）。脚本内部每 10 分钟轮询一次，不会真正占用 24h，但需要足够大的超时窗口。

3. **不跳过等待**：即使上次任务刚结束（如 23:30），也必须回到步骤1重新等待。`wait_until_22.sh` 接收 `last_run_date` 参数，会自动跳过同日重触发，等到次日 22:00。

4. **子 agent 隔离**：用 Task 工具启动子 agent，prompt 传入下方「子 agent 执行指令」全文。子 agent 执行完毕返回后，主 agent 只记录摘要，不携带子 agent 的上下文细节，保持自身轻量。

5. **循环不退出**：除非用户显式指示停止，否则永远循环。遇到 `TIMEOUT` 也只记录日志后继续循环。

### 子 agent 执行指令（Task 工具的 prompt 全文）

将以下内容作为 Task 工具的 prompt 传给子 agent：

```
你是每日蒸馏任务执行器。严格按以下顺序执行，不得跳步。

## A. 防御性时间校验

第一步：获取当前时间。
- 若当前时间在 0:00–22:00（即小时数 < 22）：立即返回「非任务时段，中断执行」，不执行任何后续步骤。这是为了防止提前消耗新一天的 Token 额度。
- 若当前时间在 22:00–23:59：继续执行 B。

## B. 初始化断点状态

运行：
  bash scripts/runner_state.sh init

扫描已有进度：
  bash scripts/runner_state.sh scan

判断是否需要清除旧标记：
- 如果 segments 中存在 .SEG_* 标记，但本次是新一轮夜间任务（新日期），运行 `runner_state.sh clear` 清除旧标记，从头开始。
- 如果是同日恢复（今日已有部分段完成，因中断而续传），保留标记，从下一个未完成段继续。

## C. 启动 Token 守卫

运行：
  bash ../cannbot-token-guard/scripts/check_guard.sh init

- 若返回 STOP：立即返回「Token 守卫 init 失败，非 Cannbot 环境」，不执行后续。
- 若返回 ARMED：继续。

运行：
  bash ../cannbot-token-guard/scripts/check_guard.sh activate

- 若返回 STOP：立即返回「Token 守卫 activate 失败」，不执行后续。
- 若返回 OK：继续。

打标记：
  bash scripts/runner_state.sh mark TOKEN_GUARD

## D. 任务段循环

对每个任务段，执行前先 check Token 守卫：

  bash ../cannbot-token-guard/scripts/check_guard.sh check

- 若返回 STOP：立即跳到步骤 F（收尾），记录停止原因。
- 若返回 OK：执行该段任务，完成后打标记：
  bash scripts/runner_state.sh mark {SEG_NAME}

### 任务段定义

⚠️ 蒸馏 Skill 尚未就绪，以下为占位结构。蒸馏 Skill 就绪后替换 TODO 部分。

段1 (SEG_1): 蒸馏分析 - 准备阶段
  # TODO: 蒸馏 Skill 就绪后，此处调用蒸馏 Skill 的初始化步骤
  # 占位：打印"段1占位 - 蒸馏 Skill 未就绪"并标记完成

段2 (SEG_2): 蒸馏分析 - 收集阶段
  # TODO: 蒸馏 Skill 就绪后，此处调用蒸馏 Skill 的数据收集步骤
  # 占位：打印"段2占位 - 蒸馏 Skill 未就绪"并标记完成

段3 (SEG_3): 蒸馏分析 - 分类与提炼阶段
  # TODO: 蒸馏 Skill 就绪后，此处调用蒸馏 Skill 的分析提炼步骤
  # 占位：打印"段3占位 - 蒸馏 Skill 未就绪"并标记完成

段4 (SEG_4): 蒸馏分析 - 输出阶段
  # TODO: 蒸馏 Skill 就绪后，此处调用蒸馏 Skill 的输出文件生成步骤
  # 占位：打印"段4占位 - 蒸馏 Skill 未就绪"并标记完成

每段执行前必须 check，STOP 即停。

## E. 全部段完成

如果所有段都完成且未触发 STOP：
  bash scripts/runner_state.sh log "全部段完成"
  返回摘要：完成了哪些段、Token 用量、是否触达 95%。

## F. 收尾（STOP 或完成）

无论 STOP 还是正常完成，都执行：
1. 获取 Token 守卫状态：
   bash ../cannbot-token-guard/scripts/check_guard.sh status
2. 记录日志：
   bash scripts/runner_state.sh log "任务结束: {停止原因}"
3. 返回执行摘要，包含：
   - 完成的段列表
   - 未完成的段列表
   - 停止原因（95%阈值 / 跨天 / 全部完成）
   - 当前 Token 用量
```

### 通用规则

6. **不消耗等待期 token**：`wait_until_22.sh` 是纯 bash 阻塞，等待期间 AI 不做任何调用。这是设计的核心——用 bash sleep 代替 AI 轮询，避免浪费额度。

7. **防御性校验不可省略**：子 agent 的步骤 A（时间校验）必须在任何任务执行前完成。即使主循环已通过 `wait_until_22.sh` 确保时间正确，子 agent 仍需独立校验，防止误触发。

8. **断点续传**：子 agent 启动时通过 `runner_state.sh scan` 读取已完成段，跳过已标记的段，从下一个继续。同日恢复保留标记；新日期清除标记重新开始。

9. **STOP 即终止**：Token 守卫返回 STOP 时，子 agent 立即停止所有操作，保存进度（已完成的段标记已在执行中写入），返回摘要。不得以任何理由继续。

10. **日志记录**：所有关键节点（触发、段完成、停止）都通过 `runner_state.sh log` 写入当日日志文件，便于排查。

## 断点续传机制

### 标记文件

| 标记 | 位置 | 含义 |
|------|------|------|
| `.SEG_TOKEN_GUARD` | `segments/` | Token 守卫已启动 |
| `.SEG_{N}` | `segments/` | 段N已完成 |

### 恢复逻辑

```
子 agent 启动
  ├─ runner_state.sh scan
  ├─ 若 .SEG_TOKEN_GUARD 不存在 → 从步骤 C（启动守卫）开始
  ├─ 若 .SEG_TOKEN_GUARD 存在但 .SEG_1 不存在 → 从步骤 D 段1 开始
  ├─ 若 .SEG_1 存在但 .SEG_2 不存在 → 从步骤 D 段2 开始
  └─ ...
  
  新日期（与 last_run_date 不同）→ clear → 从头开始
  同日恢复（与 last_run_date 相同）→ 保留标记 → 从断点继续
```

## 与其他 Skill 的关系

```
daily-distill-runner（本 Skill，主调度）
  ├─ 调用 → cannbot-token-guard（Token 守卫，已就绪）
  │          提供 init/activate/check/status 命令
  │
  ├─ 调用 → [蒸馏 Skill]（尚未就绪，占位）
  │          将代码合并记录按知识点分类写入输出文件
  │          就绪后替换 SKILL.md 中段1-段4 的 TODO 占位
  │
  └─ 调用 → [cannbot 关联 Skill]（尚未就绪，占位）
               蒸馏 Skill 就绪后，在蒸馏段内调用相关 Skill 参与分析
```

## 使用示例

### 启动常驻循环

```
用户: 启动每日蒸馏常驻循环
Bot: [读取 last_run_date]
     [运行 wait_until_22.sh，阻塞至 22:00]
     [22:00 到达，启动子 agent]
     [子 agent: 时间校验 → Token 守卫 → 任务段循环 → STOP/完成]
     [记录日志]
     [回到等待，次日 22:00 再次触发]
```

### 手动触发单次（跳过等待）

```
用户: 立即执行一次蒸馏任务（跳过等待）
Bot: [跳过 wait_until_22.sh]
     [直接启动子 agent，子 agent 内部仍做时间校验]
     [若 0:00–22:00 → 子 agent 返回"非任务时段"]
```

### 查看进度

```
用户: 查看蒸馏任务进度
Bot: bash runner_state.sh scan
     [输出已完成段、未完成段、上次运行日期]
```

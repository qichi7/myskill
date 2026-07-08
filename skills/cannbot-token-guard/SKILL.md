---
name: cannbot-token-guard
description: Cannbot 每日 Token 用量守卫（两阶段）。阶段一「启动检测」：验证 Cannbot provider 并记录开始日期，但不开始监控。阶段二「任务开始」：激活监控，在被检测任务执行期间每 10 分钟检查 Cannbot 当日 token 消耗，达到每日预算 95%（1 亿 token 中的 9500 万）或当前时间不在 22:00-24:00 任务窗口时立即停止任务。触发关键词：启动检测、token 守卫、cannbot 额度监控、长时间任务、整日运行、token 预算、停止任务。
---

# Cannbot Token Guard（两阶段）

本 skill 通常与其他 skill 配合使用：先通过本 skill「启动检测」完成前置检查，再启动被检测任务（由另一个 skill 执行），此时本 skill 进入「监控激活」阶段持续守护。

## 核心参数

| 参数 | 值 |
|------|-----|
| 每日预算 | 100,000,000 tokens (1 亿) |
| 停止阈值 | 95,000,000 tokens (95%) |
| 检查间隔 | 10 分钟（600 秒） |
| 数据来源 | `opencode-usage sync` + `~/.local/share/opencode-usage/usage-data.json` |
| Provider 过滤 | `cannbot` |
| 任务窗口 | 22:00–24:00（不在窗口内即停） |

## 两阶段工作流程

```
用户说"启动检测"或类似指令
    │
    ├─ 阶段一：ARM（前置检查）
    │   └─ check_guard.sh init
    │       ├─ 验证 opencode-usage 今日数据中存在 cannbot provider
    │       ├─ 记录开始日期
    │       ├─ 失败 → 拒绝，告知用户当前非 Cannbot
    │       └─ 成功 → 返回 ARMED，等待被检测任务启动
    │
    │   ⚠️ 此时监控尚未激活，check 为 no-op
    │
用户启动被检测任务（另一 skill）
    │
    ├─ 阶段二：ACTIVATE + MONITOR（激活并监控）
    │   └─ check_guard.sh activate
    │       ├─ 再次验证 Cannbot provider
    │       ├─ 检查当前时间是否在 22:00-24:00 任务窗口内
    │       └─ 设置 monitoring_active=1
    │
    │   ┌─ 任务循环 ─────────────────────────┐
    │   │  每个主要步骤前:                    │
    │   │  check_guard.sh check              │
    │   │    ├─ OK   → 继续任务              │
    │   │    └─ STOP → 立即停止，保存进度    │
    │   └────────────────────────────────────┘
    │
    └─ 停止条件（任一满足即停）
          ├─ Cannbot 当日 token ≥ 95,000,000
          └─ 当前时间不在 22:00-24:00 任务窗口内
```

## 脚本命令

> 脚本路径相对于本 Skill 目录（即 SKILL.md 所在目录）解析。

```bash
SCRIPT=scripts/check_guard.sh
```

| 命令 | 阶段 | 说明 |
|------|------|------|
| `init` | 阶段一 | 验证 Cannbot provider，记录开始日期。返回 `ARMED` 或 `STOP` |
| `activate` | 阶段二 | 激活监控，再次验证 provider 和时间窗口。返回 `OK` 或 `STOP` |
| `check` | 阶段二 | 检查是否应继续任务。未激活时为 no-op 返回 OK；激活后执行真正的阈值/时间窗口检查 |
| `status` | 任意 | 打印当前守卫状态（预算/用量/监控是否激活） |

### 间隔控制

`check` 内置 10 分钟间隔控制：
- 距上次检查不足 10 分钟 → 返回缓存结果，不请求 opencode-usage
- 距上次检查满 10 分钟 → 重新拉取 opencode-usage 数据

## 执行规则（给 AI 的指令）

当本 skill 被激活时，必须严格遵守以下规则：

### 阶段一：启动检测

1. 当用户说「启动检测」「启动守卫」「开始监控准备」或类似指令时，运行：
   ```bash
   bash scripts/check_guard.sh init
   ```
2. 如果返回 `STOP`：当前 provider 非 Cannbot，**告知用户并拒绝继续**，不得进入阶段二。
3. 如果返回 `ARMED`：告知用户"守卫已就绪，请在被检测任务启动时告知我"，然后**等待**。此时不得运行 `check` 或 `activate`。
4. **不要在阶段一运行 `activate` 或 `check`**。等待用户启动被检测任务。

### 阶段二：任务监控

5. 当用户开始执行被检测任务时（通常是激活了另一个 skill 并给出任务指令），**第一步**运行：
   ```bash
   bash scripts/check_guard.sh activate
   ```
6. 如果 `activate` 返回 `STOP`：不得执行被检测任务的任何内容。
7. `activate` 返回 `OK` 后，在被检测任务的**每个主要步骤/迭代之前**运行：
   ```bash
   bash scripts/check_guard.sh check
   ```
8. 如果 `check` 返回 `STOP`：
   - **立即停止**被检测任务的所有操作
   - 保存已完成的工作进度
   - 向用户报告停止原因和当前 token 用量
   - **不得以任何理由继续**，只能由用户显式指示后才能恢复

### 通用规则

9. **不跳过检查**：即使上一次返回 OK，下一步前仍需检查。脚本内部已处理间隔控制。
10. **停止即终止**：一旦收到 STOP，不得继续任何后续步骤。
11. 如果用户在阶段一之前直接给任务指令，应先提醒用户运行「启动检测」。

## 状态文件

路径：`~/.cache/cannbot-token-guard/guard_state.json`

```json
{
  "start_date": "2026-07-05",
  "last_check_epoch": 1751750400,
  "last_total_tokens": 10284979,
  "last_cost_usd": 9.36,
  "monitoring_active": 0
}
```

`monitoring_active` 字段：
- `0` = 已 ARM（init 完成），监控未激活
- `1` = 监控已激活（activate 完成），check 正在执行限制

## Token 计算口径

数据来源：`opencode-usage sync` 同步后读取 `~/.local/share/opencode-usage/usage-data.json`。

筛选条件：
- `provider == "cannbot"`（不区分大小写）
- `createdAt` 日期 = 当地时间今日

每个符合条件的 session，累加主 session + 所有 subagent 的 token：
```
total = input + output + cacheRead + cacheWrite + reasoning
```

所有 model（glm-5.2、deepseek-v4-pro 等）只要 provider 为 cannbot 就计入。

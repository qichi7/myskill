# Skill 类型与协作工作流

本文件定义所有 skill 的身份（主/辅助）、类型约束、询问规则，以及多个 skill 组成的协作工作流。各 skill 的 SKILL.md 通过相对路径引用本文件，不在各自文件中重复定义。

> 路径：`docs/skill-types-and-workflows.md`
> 引用方式：各 skill 目录位于 `skills/{name}/`，相对路径为 `../../docs/skill-types-and-workflows.md`

---

## 一、Skill 类型定义

| 类型 | 定义 | 可否独立执行 | 询问主 skill |
|------|------|-------------|-------------|
| **主 skill** | 执行实际任务，是工作流的核心 | 可以独立执行，也可被辅助 skill 配合 | 不需要 |
| **辅助 skill** | 为主 skill 提供调度、守护等支撑能力，自身不执行业务任务 | 不可独立执行，必须有主 skill 配合 | 激活后必须询问主 skill |

### 辅助 skill 的询问规则

辅助 skill 被激活后，第一件事是询问用户"主 skill 是什么？"。用户回答后检查：

1. **若用户回答的是辅助 skill**：记录该辅助 skill（它也会一起运行），然后**立即继续追问**："已记录辅助 skill。主 skill 是什么？" 必须当场问到主 skill 为止，不得等到运行时再问。
2. **若用户回答的是主 skill**：记录主 skill 名称，作为配合的目标。
3. **若用户在激活时已一并提供主 skill**：跳过询问。用户可额外指定要一起运行的辅助 skill。

---

## 二、Skill 清单与身份

| skill 名称 | display_name | 类型 | 身份说明 | 职责 |
|------------|-------------|------|---------|------|
| `ops-transformer-distill` | 蒸馏skill | 主 | 可独立执行，也可被辅助 skill（每日skill、守卫skill）配合运行 | 从 GitCode 指定仓库的合入记录蒸馏技术知识，分类输出 |
| `daily-distill-runner` | 每日skill | 辅助 | 不独立执行任务，激活后必须先确定主 skill 才开始等待 | 每晚 22:00 定时触发主 skill，无限循环，不打断对话 |
| `cannbot-token-guard` | 守卫skill | 辅助 | 不独立执行任务，需配合主 skill 使用，激活后先询问主 skill | 守护主 skill，监控 Cannbot 每日 token 用量，达 95% 或超出时间窗口即停 |

> 后续新增 skill 时，在此表登记类型、身份与职责。

---

## 三、各 skill 的身份与角色规则

### 蒸馏skill（`ops-transformer-distill`）— 主 skill

- **身份**：主 skill，工作流核心。
- **独立性**：可独立执行，也可被辅助 skill 配合运行。
- **询问主 skill**：不需要。自身即主 skill。
- **被配合方式**：可被每日skill 在 22:00 定时触发执行，可被守卫skill 在执行期间监控 token 用量。

### 每日skill（`daily-distill-runner`）— 辅助 skill

- **身份**：辅助 skill，不独立执行任务。
- **询问主 skill**：激活后第一件事按上方「辅助 skill 的询问规则」执行，确定主 skill 后才开始等待。
- **核心职责**：准点触发 + 循环。主 skill 的具体内容由用户在激活时指定，本 skill 不关心主 skill 的内部细节。
- **立即触发检测**：确定主 skill、一切就绪准备开始等待前，立即检测当前时间。若当前在 22:00-24:00 之间，跳过等待直接执行主 skill；否则进入正常等待循环。

### 守卫skill（`cannbot-token-guard`）— 辅助 skill

- **身份**：辅助 skill，不独立执行任务，需配合主 skill 使用。
- **询问主 skill**：激活后第一件事按上方「辅助 skill 的询问规则」执行，确定主 skill 后再进行启动检测。
- **核心职责**：守护主 skill，在其执行期间监控 Cannbot 每日 token 用量和时间窗口。
- **两阶段**：阶段一启动检测（ARMED，等待主 skill 启动）；阶段二激活监控（主 skill 启动时 activate，执行期间每步前 check）。

---

## 四、协作工作流

### 邪恶蒸馏工作流

**目标**：每晚 22:00 自动从 GitCode 仓库合入记录中蒸馏技术知识，在 Cannbot 每日 token 预算内安全运行，中断可续传，次日自动再次触发。

**参与 skill**：

| 角色 | skill | 触发时机 |
|------|-------|---------|
| 调度 | 每日skill | 用户激活，询问主 skill 后进入等待 |
| 守护 | 守卫skill | 用户激活，询问主 skill 后进入启动检测 |
| 主任务 | 蒸馏skill | 22:00 由每日skill 触发执行，守卫skill 全程监控 |

**工作流步骤**：

```
1. 用户激活每日skill
   → 每日skill 询问主 skill
   → 用户回答"蒸馏skill"（若先回答守卫skill，则记录并继续追问主 skill）
   → 确认：主 skill = 蒸馏skill，辅助 skill = 守卫skill

2. 用户激活守卫skill
   → 守卫skill 询问主 skill
   → 用户回答"蒸馏skill"（若先回答每日skill，则记录并继续追问主 skill）
   → 确认：主 skill = 蒸馏skill

3. 守卫skill 执行启动检测（check_guard.sh init）
   → 验证 Cannbot provider，记录开始日期
   → ARMED 状态，等待主 skill 启动

4. 每日skill 立即触发检测
   → 若当前 22:00-24:00：跳过等待，直接执行
   → 否则：阻塞等待至 22:00

5. 22:00 到达，每日skill 用 skill 工具加载蒸馏skill
   → 守卫skill 执行 activate，激活监控

6. 蒸馏skill 执行 5 阶段蒸馏流程：
   阶段1 初始化 → 阶段2 收集合入记录 → 阶段3 获取Diff
   → 阶段4 批量蒸馏循环（每20条→5分片→并行Task→标记完成→下一批）
   → 阶段5 汇总
   每个批次前守卫skill check，STOP 则立即停止

7. 蒸馏skill 完成（或被守卫skill STOP）
   → 每日skill 记录摘要
   → 不打断对话，回到等待次日 22:00

8. 次日 22:00 再次触发
   → 蒸馏skill 从断点续传（check_progress.sh 扫描 .PHASE_* 标记）
   → 守卫skill 重新 init + activate（新一天的预算）
```

**停止条件**（任一满足即停当前轮）：
- 守卫skill 检测到 Cannbot 当日 token ≥ 95%（9500 万 / 1 亿）
- 守卫skill 检测到当前时间不在 22:00-24:00 窗口
- 蒸馏skill 全部批次完成

**断点续传**：
- 每日skill：`runner_state.sh` 记录 `last_run_date`，跨天自动续
- 蒸馏skill：`.PHASE_{N}` + `batch_{NN}/.DONE` + `batch_progress.json` 三层标记
- 守卫skill：每日重新 `init`，不跨天续

---

### [预留] 其他工作流

> 后续新增工作流在此登记。

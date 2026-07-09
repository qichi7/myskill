---
name: ops-transformer-distill
display_name: 蒸馏skill
description: 从 GitCode 指定仓库的合入记录蒸馏技术知识。每次处理20条合入记录，按代码量均分给5个子agent并行分析，支持依赖合并和断点续传。触发关键词：蒸馏、知识提炼、合入记录分析、技能学习。
---

# 蒸馏skill — 仓库合入记录知识提炼

从 GitCode 指定仓库的所有合入记录中蒸馏技术知识。每次取 20 条合入记录，按代码量均分给 5 个子 agent 并行分析，处理完一批继续下一批，直到全部完成。

## 概述

本 skill 的身份、类型定义与协作工作流详见 [skill 类型与协作工作流](../../docs/skill-types-and-workflows.md)。

从第一条合入记录开始，每批 20 条，按代码量均分给 5 个子 agent 并行蒸馏。若两条记录修改了相同文件（有依赖），必须分给同一个 agent。每批完成后标记已处理记录，支持断点续传。全部批次完成后生成总览。

## 数据源

| 项目 | 值 |
|------|-----|
| 仓库 | 用户激活时指定（如 `cann/ops-transformer`） |
| API Base | `https://gitcode.com/api/v5` |
| 认证 | 环境变量 `GITCODE_TOKEN`，header `private-token` |
| 数据范围 | 指定仓库的所有合入记录（pulls state=merged），按合入时间升序 |

## 批量蒸馏策略

| 参数 | 值 |
|------|-----|
| 每批记录数 | 20 |
| 子 agent 数 | 5 |
| 分配方式 | 按代码量（additions+deletions）均衡分配 |
| 依赖合并 | 修改了相同文件的两条记录必须分给同一个 agent |
| 记录不可拆分 | 一条合入记录完整归属一个 agent |
| 处理顺序 | 按合入时间升序，第 1-20 条 → 第 21-40 条 → ... |

## 输出目录结构

用户激活时指定 `output_dir`：

```
{output_dir}/
├── Summary.md                    # 蒸馏总览（最终产物）
├── 01_download/
│   └── merges.json               # 所有合入记录原始数据
├── 02_intermediate/
│   ├── diffs.json                # 合入记录diff数据（patch 500行截断）
│   ├── batch_progress.json       # 批次进度（已完成记录编号列表）
│   ├── errors.log                # 错误日志
│   └── .PHASE_{1-5}             # 阶段进度标记
└── 03_knowledge/
    ├── batch_01/
    │   ├── shard_1.json          # 分片输入（patch 100行截断）
    │   ├── shard_2.json
    │   ├── shard_3.json
    │   ├── shard_4.json
    │   ├── shard_5.json
    │   ├── batch_plan.json       # 批次分片计划
    │   ├── analysis_1.md         # 各 agent 的分析输出
    │   ├── analysis_2.md
    │   ├── analysis_3.md
    │   ├── analysis_4.md
    │   ├── analysis_5.md
    │   └── .DONE                 # 该批次已完成
    ├── batch_02/
    └── ...
```

## 脚本路径

> 脚本路径相对于本 Skill 目录（即 SKILL.md 所在目录）解析。

| 脚本 | 用途 |
|------|------|
| `collect_merges.py {output_dir} {repo}` | 收集指定仓库所有合入记录 |
| `fetch_diffs.py {output_dir} {repo}` | 逐条获取diff（daemon线程防卡死+增量保存） |
| `prepare_batch.py {output_dir} {batch_num}` | 准备一个批次的5个分片（按代码量均分+依赖合并） |
| `mark_batch.py {output_dir} {batch_num}` | 标记批次完成，更新断点进度 |
| `check_progress.sh {output_dir}` | 检查进度 |

## 5阶段工作流

| 阶段 | 动作 | 脚本 | 断点标记 |
|------|------|------|----------|
| 1 初始化 | 创建目录结构 | agent mkdir | `.PHASE_1` |
| 2 收集合入记录 | 获取所有合入记录 | `collect_merges.py` | `.PHASE_2` |
| 3 获取Diff | 逐条获取patch | `fetch_diffs.py` | `.PHASE_3` |
| 4 批量蒸馏循环 | 每20条→5分片→并行Task→标记完成→下一批 | `prepare_batch.py` + Task并行 + `mark_batch.py` | `.PHASE_4` |
| 5 汇总 | 生成 Summary.md | agent 直接写 | `.PHASE_5` |

## 断点续传机制

### 三层标记

| 层级 | 标记 | 位置 | 作用 |
|------|------|------|------|
| 阶段级 | `.PHASE_{N}` | `02_intermediate/` | 决定从哪个阶段恢复 |
| 批次级 | `.DONE` | `03_knowledge/batch_{N}/` | 跳过已完成批次 |
| 记录级 | `batch_progress.json` | `02_intermediate/` | 记录已完成的记录编号列表 |

### 恢复逻辑

```
激活时运行 check_progress.sh 扫描进度

.PHASE_1 不存在 → 从阶段1开始
.PHASE_2 不存在 → 从阶段2开始
.PHASE_3 不存在 → 从阶段3开始（fetch_diffs.py 自动读已有 diffs.json 续传）
.PHASE_4 不存在 → 从阶段4开始
  读取 batch_progress.json 的 next_batch
  从该批次继续（跳过已有 .DONE 的批次）
.PHASE_4 存在但 .PHASE_5 不存在 → 直接进入阶段5
```

## 执行规则（给 AI 的指令）

当本 Skill 被激活时，**必须严格遵守**以下规则：

### 前置检查

1. **询问仓库和输出路径**：本 Skill 被激活后，第一件事是询问用户两个必填参数：
   - `repo`：要蒸馏的 GitCode 仓库全名（如 `cann/ops-transformer`）
   - `output_dir`：蒸馏产物输出目录（如 `~/distill/ops-transformer`）

   若用户在激活时已一并提供，则跳过询问。

2. **检查 GITCODE_TOKEN**：运行 `echo $GITCODE_TOKEN | head -c 4`，若为空则告知用户需先 `export GITCODE_TOKEN=xxx`，停止执行。
3. **扫描进度**：运行 `bash scripts/check_progress.sh {output_dir}`，根据 `.PHASE_*` 标记确定从哪个阶段恢复。

### 阶段1：初始化

4. 若 `.PHASE_1` 不存在：
   - 创建目录结构
   - 操作：
```bash
mkdir -p {output_dir}/01_download
mkdir -p {output_dir}/02_intermediate
mkdir -p {output_dir}/03_knowledge
touch {output_dir}/02_intermediate/errors.log
touch {output_dir}/02_intermediate/.PHASE_1
```

### 阶段2：收集合入记录

5. 若 `.PHASE_2` 不存在：
   - 运行 `python3 -u scripts/collect_merges.py {output_dir} {repo}`
   - 脚本分页获取 `{repo}` 所有合入记录，输出 `01_download/merges.json`
   - 成功后 `touch 02_intermediate/.PHASE_2`

### 阶段3：获取Diff

6. 若 `.PHASE_3` 不存在：
   - 运行 `python3 -u scripts/fetch_diffs.py {output_dir} {repo}`（可后台运行）
   - 脚本自动读已有 `diffs.json` 续传，逐条获取patch（三步法：合入记录详情→compare→files补全）
   - daemon线程防卡死，每10条增量保存
   - 成功后 `touch 02_intermediate/.PHASE_3`

### 阶段4：批量蒸馏循环（核心）

7. 若 `.PHASE_4` 不存在，进入批量循环：

   **确定起始批次**：
   - 读取 `02_intermediate/batch_progress.json` 的 `next_batch` 字段
   - 若文件不存在或无 `next_batch`，从批次 1 开始

   **对每个批次循环执行**：

   **步骤4a：准备分片**
   - 运行 `python3 -u scripts/prepare_batch.py {output_dir} {batch_num}`
   - 脚本取该批次 20 条记录，按代码量均分 5 个分片，有文件交集的记录合并到同一分片
   - 输出 `03_knowledge/batch_{NN}/shard_{1-5}.json` + `batch_plan.json`

   **步骤4b：并行下发 Task**
   - 读取 `batch_plan.json`，获取 5 个分片信息
   - **在单条消息中并行下发 5 个 Task**（必须同一条消息，5 个 Task tool call）
   - Task **不指定 subagent_type**
   - 每个 Task 的 prompt 见下方「分片分析 Task prompt 模板」
   - 等待所有 Task 返回

   **步骤4c：标记完成**
   - 运行 `python3 -u scripts/mark_batch.py {output_dir} {batch_num}`
   - 标记该批次所有记录为已完成，更新 `batch_progress.json`，创建 `.DONE`

   **步骤4d：继续下一批**
   - 读取 `batch_progress.json` 的 `next_batch`
   - 若有下一批 → 回到步骤4a
   - 若无下一批（全部记录已完成）→ `touch 02_intermediate/.PHASE_4`，进入阶段5

### 阶段5：汇总

8. 若 `.PHASE_5` 不存在：
   - 汇总各批次 `analysis_*.md` 的要点，生成顶层 `Summary.md`
   - Summary.md 包含：蒸馏概况（仓库/合入记录总数/批次数）、各批次技术要点摘要、关键知识点索引
   - 成功后 `touch 02_intermediate/.PHASE_5`

### 通用规则

9. **不跳阶段**：必须按 1→2→3→4→5 顺序，每阶段完成后打标记才进入下一阶段。
10. **断点续传**：激活时先运行 `check_progress.sh`，从第一个未完成的阶段继续。阶段4内按批次续传。
11. **错误处理**：脚本失败时记录到 `errors.log`，可重试；重试3次仍失败则跳过该条记录，记录 `[ERROR]`。
12. **增量保存**：`fetch_diffs.py` 每10条保存一次，中断不丢数据。
13. **路径隔离**：所有输出、中间文件、下载文件必须放在 `{output_dir}` 内，不得写入 `output_dir` 之外的任何路径。Task 分析的输出文件也必须写入 `{output_dir}/03_knowledge/batch_{NN}/` 下。

## 分片分析 Task prompt 模板

每个分片的 Task 使用以下 prompt（替换 `{...}` 占位符）：

```
你是代码蒸馏分析器。请分析以下合入记录分片的代码级 diff，提炼技术知识点。

## 输入文件

分片摘要数据：{output_dir}/03_knowledge/batch_{batch_num}/shard_{shard_num}.json

该 JSON 文件包含一个合入记录数组，每条记录含：
- title: 合入记录标题
- number: 记录编号
- html_url: 记录链接
- files: 文件修改列表，每个文件含 filename/status/additions/deletions/patch（已截断到100行）

## 分析要求

对每条合入记录，输出以下内容（用 markdown 格式）：

### #{number}: {title}
- **链接**: {html_url}
- **修改文件**: {文件清单，含增删行数}
- **技术要点**: {3-5条核心技术点，从 patch 代码中提炼}
- **代码模式**: {代码修改的模式，如：新增kernel实现/修改tiling策略/修复边界条件等}

## 输出

将完整分析写入文件：{output_dir}/03_knowledge/batch_{batch_num}/analysis_{shard_num}.md

分析完成后返回：已完成分片 batch_{batch_num} shard_{shard_num}，包含 {记录数} 条合入记录的分析。
```

## 使用示例

### 首次蒸馏

```
用户: 蒸馏 cann/ops-transformer，输出到 ~/distill/ops-transformer
Bot: [询问仓库和输出路径 → cann/ops-transformer, ~/distill/ops-transformer]
     [检查 GITCODE_TOKEN]
     [运行 check_progress.sh，无进度]
     [阶段1: 创建目录结构]
     [阶段2: collect_merges.py 收集合入记录]
     [阶段3: fetch_diffs.py 获取diff]
     [阶段4: 批量蒸馏循环]
       批次1: prepare_batch → 5个Task并行 → mark_batch
       批次2: prepare_batch → 5个Task并行 → mark_batch
       ...直到全部完成
     [阶段5: 生成 Summary.md]
```

### 断点续传

```
用户: 继续蒸馏 cann/ops-transformer，输出到 ~/distill/ops-transformer
Bot: [运行 check_progress.sh]
     [.PHASE_1~3 已存在，.PHASE_4 不存在]
     [读取 batch_progress.json: next_batch=3]
     [从批次3继续：prepare_batch → 5个Task → mark_batch → 批次4 → ...]
     [全部批次完成 → 阶段5: 生成 Summary.md]
```

### 查看进度

```
用户: 查看蒸馏进度
Bot: bash scripts/check_progress.sh {output_dir}
     [输出各阶段完成度、批次进度、合入记录统计]
```

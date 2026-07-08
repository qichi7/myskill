---
name: ops-transformer-distill
display_name: 蒸馏skill
description: 从 GitCode 指定仓库的合入记录蒸馏技术知识。收集所有合入记录，获取代码级diff，按8类分类，主agent并行下发Task加速分析，支持断点续传。触发关键词：蒸馏、知识提炼、合入记录分析、技能学习。
---

# 蒸馏skill — 仓库合入记录知识提炼

从 GitCode 指定仓库的所有合入记录中蒸馏技术知识，按功能、性能等 8 类分类输出。

## 概述

本 Skill 从第一条合入记录开始，逐条获取合入记录的代码级 diff，按关键词分类后，主 agent 并行下发 Task 对各分类分片做深度分析，最终输出分类知识文档。支持断点续传，中断后从最近已完成处继续。

## 数据源

| 项目 | 值 |
|------|-----|
| 仓库 | 用户激活时指定（如 `cann/ops-transformer`） |
| API Base | `https://gitcode.com/api/v5` |
| 认证 | 环境变量 `GITCODE_TOKEN`，header `private-token` |
| 数据范围 | 指定仓库的所有合入记录（pulls state=merged），按合入时间升序 |

## 分类方案

按优先级降序，匹配第一个命中的分类：

| 优先级 | 分类 | 关键词示例 |
|--------|------|-----------|
| 1 | 功能实现 | flash attention, mxfp8, attention, quant, matmul, softmax, 新增, 支持, 实现 |
| 2 | 性能优化 | tiling, ub, l1, vec, 性能, 流水线, pipeline, 优化, cache, 分块 |
| 3 | 精度修复 | 精度, precision, atol, rtol, 数值, scale, 误差, 对齐 |
| 4 | Bug修复 | 编译, 卡死, 修复, fix, 异常, bug, 越界, 溢出, 段错误, 死锁, race |
| 5 | 重构 | 重构, refactor, 抽取, 合并, 清理, 移除, 统一 |
| 6 | 测试 | 测试, test, ut, st, 用例, 覆盖率, pytest |
| 7 | 文档 | 文档, doc, readme, 注释, comment |
| 8 | 基础设施 | ci, 构建脚本, cmake, makefile, 安装, 依赖, infra |

无任何匹配 → 默认 `功能实现`（算子仓绝大多数合入记录属于功能）。

## 输出目录结构

用户激活时指定 `output_dir`：

```
{output_dir}/
├── Summary.md                    # 蒸馏总览（最终产物）
├── 01_download/
│   └── merges.json               # 所有合入记录原始数据
├── 02_intermediate/
│   ├── diffs.json                # 合入记录diff数据（patch 500行截断）
│   ├── merge_classification.json # 分类结果
│   ├── shard_map.json            # 分片映射表
│   ├── errors.log                # 错误日志
│   └── .PHASE_{1-6}             # 阶段进度标记
└── 03_knowledge/
    ├── 功能实现/
    │   ├── merge_summary_1.json  # 分片紧凑摘要（patch 100行截断）
    │   ├── analysis_part1.md     # 分片分析（临时）
    │   ├── analysis.md           # 合并后完整分析
    │   └── .DONE                 # 该分类已完成
    ├── 性能优化/
    ├── 精度修复/
    ├── Bug修复/
    ├── 重构/
    ├── 测试/
    ├── 文档/
    └── 基础设施/
```

## 脚本路径

> 脚本路径相对于本 Skill 目录（即 SKILL.md 所在目录）解析。

| 脚本 | 用途 |
|------|------|
| `collect_merges.py {output_dir} {repo}` | 收集指定仓库所有合入记录 |
| `fetch_diffs.py {output_dir} {repo}` | 逐条获取diff（daemon线程防卡死+增量保存） |
| `classify_merges.py {output_dir}` | 按关键词分类合入记录 |
| `prepare_shards.py {output_dir}` | 按分类拆分片，生成紧凑摘要 |
| `merge_analysis.py {output_dir} {category}` | 合并分片分析为完整analysis.md |
| `check_progress.sh {output_dir}` | 检查进度 |

## 6阶段工作流

| 阶段 | 动作 | 脚本 | 断点标记 |
|------|------|------|----------|
| 1 初始化 | 创建目录结构 | agent mkdir | `.PHASE_1` |
| 2 收集合入记录 | 获取所有合入记录 | `collect_merges.py` | `.PHASE_2` |
| 3 获取Diff | 逐条获取patch | `fetch_diffs.py` | `.PHASE_3` |
| 4 分类 | 关键词优先级匹配 | `classify_merges.py` | `.PHASE_4` |
| 5 并行蒸馏 | 分片→并行Task→合并 | `prepare_shards.py` + Task并行 + `merge_analysis.py` | `.PHASE_5` |
| 6 汇总 | 生成 Summary.md | agent 直接写 | `.PHASE_6` |

## 断点续传机制

### 三层标记

| 层级 | 标记 | 位置 | 作用 |
|------|------|------|------|
| 阶段级 | `.PHASE_{N}` | `02_intermediate/` | 决定从哪个阶段恢复 |
| 分类级 | `.DONE` | `03_knowledge/{cat}/` | 跳过已完成分类 |
| 记录级 | `diffs.json` 已有记录 | `02_intermediate/` | 跳过已获取diff的合入记录 |

### 恢复逻辑

```
激活时运行 check_progress.sh 扫描进度

.PHASE_1 不存在 → 从阶段1开始
.PHASE_2 不存在 → 从阶段2开始
.PHASE_3 不存在 → 从阶段3开始（fetch_diffs.py 自动读已有 diffs.json 续传）
.PHASE_4 不存在 → 从阶段4开始
.PHASE_5 不存在 → 从阶段5开始（扫描 .DONE，仅处理未完成分类的分片）
.PHASE_5 存在但 .PHASE_6 不存在 → 直接进入阶段6
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
   - 创建目录结构：`01_download/`、`02_intermediate/`、`03_knowledge/{8个分类目录}`
   - 创建空 `02_intermediate/errors.log`
   - 操作：
```bash
mkdir -p {output_dir}/01_download
mkdir -p {output_dir}/02_intermediate
mkdir -p {output_dir}/03_knowledge/{功能实现,性能优化,精度修复,Bug修复,重构,测试,文档,基础设施}
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

### 阶段4：分类

7. 若 `.PHASE_4` 不存在：
   - 运行 `python3 -u scripts/classify_merges.py {output_dir}`
   - 输出 `02_intermediate/merge_classification.json`
   - 成功后 `touch 02_intermediate/.PHASE_4`

### 阶段5：并行蒸馏（核心）

8. 若 `.PHASE_5` 不存在：

   **步骤5a：准备分片**
   - 运行 `python3 -u scripts/prepare_shards.py {output_dir}`
   - 输出 `02_intermediate/shard_map.json` + 各分类 `merge_summary_{N}.json`
   - 每分片上限 25 条合入记录 / 80 KB

   **步骤5b：并行下发Task**
   - 读取 `shard_map.json`，获取所有未完成分片（对应分类无 `.DONE` 的分片）
   - **在单条消息中并行下发所有分片的 Task**（必须同一条消息，多个 Task tool call，否则变串行）
   - Task **不指定 subagent_type**
   - 每个 Task 的 prompt 见下方「分片分析 Task prompt 模板」
   - 等待所有 Task 返回

   **步骤5c：合并**
   - 逐分类运行 `python3 -u scripts/merge_analysis.py {output_dir} {category}`
   - 合并 `analysis_part*.md` → `analysis.md`，创建 `.DONE`，删除 part 文件
   - 所有分类完成后 `touch 02_intermediate/.PHASE_5`

### 阶段6：汇总

9. 若 `.PHASE_6` 不存在：
   - 汇总各分类 `analysis.md` 的要点，生成顶层 `Summary.md`
   - Summary.md 包含：蒸馏概况（合入记录总数/分类分布）、各分类技术要点摘要、关键知识点索引
   - 成功后 `touch 02_intermediate/.PHASE_6`

### 通用规则

10. **不跳阶段**：必须按 1→2→3→4→5→6 顺序，每阶段完成后打标记才进入下一阶段。
11. **断点续传**：激活时先运行 `check_progress.sh`，从第一个未完成的阶段继续。
12. **错误处理**：脚本失败时记录到 `errors.log`，可重试；重试3次仍失败则跳过该条记录，记录 `[ERROR]`。
13. **增量保存**：`fetch_diffs.py` 每10条保存一次，中断不丢数据。
14. **路径隔离**：所有输出、中间文件、下载文件必须放在 `{output_dir}` 内，不得写入 `output_dir` 之外的任何路径（如 `/tmp`、`~`、`$HOME` 等）。Task 分析的输出文件也必须写入 `{output_dir}/03_knowledge/{category}/` 下。

## 分片分析 Task prompt 模板

每个分片的 Task 使用以下 prompt（替换 `{...}` 占位符）：

```
你是代码蒸馏分析器。请分析以下合入记录分片的代码级 diff，提炼技术知识点。

## 输入文件

分片摘要数据：{output_dir}/03_knowledge/{category}/merge_summary_{shard_num}.json

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

将完整分析写入文件：{output_dir}/03_knowledge/{category}/analysis_part{shard_num}.md

分析完成后返回：已完成分片 {category} #{shard_num}，包含 {记录数} 条合入记录的分析。
```

## 使用示例

### 首次蒸馏

```
用户: 蒸馏 cann/ops-transformer，输出到 ~/distill/ops-transformer
Bot: [检查 GITCODE_TOKEN]
     [运行 check_progress.sh，无进度]
     [阶段1: 创建目录结构]
     [阶段2: collect_merges.py {output_dir} {repo} 收集合入记录]
     [阶段3: fetch_diffs.py {output_dir} {repo} 获取diff]
     [阶段4: classify_merges.py 分类]
     [阶段5: prepare_shards.py → 并行Task分析 → merge_analysis.py 合并]
     [阶段6: 生成 Summary.md]
```

### 断点续传

```
用户: 继续蒸馏 cann/ops-transformer，输出到 ~/distill/ops-transformer
Bot: [运行 check_progress.sh]
     [.PHASE_1~3 已存在，.PHASE_4 不存在]
     [从阶段4继续：classify_merges.py → 阶段5 → 阶段6]
```

### 查看进度

```
用户: 查看蒸馏进度
Bot: bash scripts/check_progress.sh {output_dir}
     [输出各阶段完成度、各分类完成度、合入记录统计]
```

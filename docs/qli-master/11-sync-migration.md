# 11 - 同步迁移经验

> 从上游 ops-transformer 仓库同步修改到 MXFP8 变体仓库的完整经验总结。

---

## 11.1 同步策略

### 1. 提交分析

**必须先分析所有待同步提交**，不能逐笔盲目同步：

```bash
# 列出所有待同步提交
cd upstream && git log --oneline --reverse <start_commit>..HEAD -- <path>
```

**分析每笔提交的：**
- 影响范围（哪些文件）
- 依赖关系（是否依赖前置提交）
- 与目标仓库的冲突风险
- 用户是否需要（有些功能目标仓库不需要）

### 2. 制定计划

创建 `/home/hpc/skill-hpc/<project>_sync_plan.md`，包含：

- 待同步提交列表（commit hash、描述、影响范围、优先级）
- 跳过提交列表（及原因）
- 执行顺序（按依赖关系排序）
- 验证检查清单

### 3. 跳过策略

**可以跳过的提交类型：**
- 目标仓库已有等价实现（如 NpuArch 支持）
- 用户明确不需要的功能（如 hifp8、0轴非连续）
- 互相抵消的提交（如提交+回退）
- 仅影响其他算子的修改

**跳过后记录原因**，避免后续审查时重复讨论。

---

## 11.2 同步执行

### 1. 逐笔同步原则

- **每笔提交单独同步**，不要合并多笔
- 每笔同步后立即 `git add && git commit`
- 提交信息格式：`sync: <upstream_hash> <upstream_message>`

### 2. 文件级同步策略

**直接复制**（适用于无 MXFP8 特有代码的文件）：
```bash
cd upstream && git show <hash>:<path> > /target/path
```

**手动应用 diff**（适用于有 MXFP8 特有代码的文件）：
```bash
cd upstream && git show <hash> -- <path> > /tmp/diff.txt
# 逐 hunk 检查并应用，保留 MXFP8 代码
```

**子 agent 辅助**（适用于大量文件）：
- 使用 `task` 工具分发文件级同步任务
- 明确指示保留 MXFP8 特有代码
- 要求返回修改摘要

### 3. MXFP8 代码保护

**必须保留的 MXFP8 特有元素：**
- `IS_MXFP8` 条件编译分支
- `tensorQScaleOffset`、`qScaleCoreOffset_` 等 MXFP8 字段
- `_MxFP8` 后缀的函数（如 `BatchMulWeightAndReduceSum_MxFP8`）
- `SCALE_T` typedef（MXFP8 使用 `fp8_e8m0_t`）
- `CVRATIO` 宏（MXFP8 仓库可能使用不同值）
- `resMm1UB_` buffer 复用模式

**检查方法：**
```bash
grep -n "IS_MXFP8\|_MxFP8\|SCALE_T\|CVRATIO" <file>
```

---

## 11.3 编译验证

### 1. 必须编译

**每笔同步提交后必须编译验证**，不能等到全部同步完成。

**编译命令：**
```bash
cd /home/hpc/code/ops-transformer_qliv2mxfp8
bash build.sh --pkg --ops=quant_lightning_indexer_v2 --op_debug_config ccec_g --soc=ascend950
```

**编译失败时：**
- 立即修复，不要继续同步后续提交
- 记录错误和修复方案
- 提交修复：`fix: <问题描述>`

### 2. 常见编译错误

**命名空间错误：**
```
error: no template named 'XXX'; did you mean 'namespace::XXX'?
error: use of undeclared identifier 'XXX'
```

**原因：** 从其他提交提前同步的函数未放入正确的命名空间。

**修复：** 检查 namespace 闭合括号位置，确保函数在正确的命名空间内。

**常量重命名遗漏：**
```
error: use of undeclared identifier 'OLD_CONSTANT_NAME'
```

**原因：** metadata 常量重命名时遗漏某些文件（如 arch22）。

**修复：** 全局搜索旧常量名，确保所有引用都已更新。

---

## 11.4 审查报告处理

### 1. 审查报告结构

审查报告通常包含：
- 同步进度总览
- 已同步提交详细审查（文件对比、问题详情）
- 未同步提交分析
- 综合评估（评分、待修复优先级）
- 增量审查记录

### 2. 问题验证

**对每个问题：**
1. 验证问题是否真实存在（不要盲目接受）
2. 评估影响（编译失败？测试失败？功能错误？）
3. 决定修复或反驳

**验证方法：**
```bash
# 检查代码
grep -n <pattern> <file>

# 检查二进制
xxd <file> | head

# 对比上游
cd upstream && git show <hash> -- <file> | grep <pattern>
```

### 3. 修复与反驳

**修复：**
- 立即修复确认的问题
- 提交修复：`fix: 修复审查报告中的N个问题`
- 更新审查报告，记录修复结果

**反驳：**
- 如果审查结论错误，提供证据反驳
- 在审查报告中添加"反驳"章节
- 说明为什么审查结论不适用于当前场景

---

## 11.5 测试框架同步

### 1. 测试文件特殊性

测试文件（`golden.py`、`result_compare_method.py` 等）通常：
- 不阻塞编译
- 但影响测试运行
- 可能有设备特定逻辑（如 Ascend950 门控）

### 2. 设备门控

**上游可能添加设备检查：**
```python
if "Ascend950" not in properties.name:
    seqused_q = None
    output_idx_offset = None
```

**MXFP8 仓库适配：**
- 使用已有的 `soc_version` 变量
- 保持代码风格一致
- 确保非目标设备不会传入不支持的参数

### 3. 参数同步

**测试参数变更时：**
- 检查 `paramset.py` 中的参数列表
- 检查 `single.py` 中的参数解包
- 检查 `golden.py` 中的参数处理
- 确保所有文件一致

---

## 11.6 经验教训

### 1. 编译验证是必须的

**错误做法：** 仅靠代码审查确认同步正确性。

**正确做法：** 每笔同步后编译验证。

**案例：** per_tensor 函数命名空间错误，代码审查标记为"合理"，但编译失败。

### 2. 命名空间归属必须检查

**错误做法：** 从其他提交提前同步函数时，仅复制函数体。

**正确做法：** 确认函数在源仓库中的命名空间归属，确保在目标仓库中处于相同命名空间。

**案例：** `MulWeightAndReduceSumPerTensor` 等函数在源仓库位于 `namespace vector1` 内，但同步时被放在 namespace 闭合括号之后，导致无法访问工具函数。

### 3. 审查报告可能有遗漏

**错误做法：** 完全信任审查报告。

**正确做法：** 验证每个问题，但也主动检查审查未覆盖的方面。

**案例：** 审查报告未检查 per_tensor 函数的命名空间归属，导致编译错误在审查后才被发现。

### 4. 提前同步要谨慎

**错误做法：** 从未来提交提前同步函数，认为"反正以后要用"。

**正确做法：** 仅在确实需要时提前同步，并确保命名空间、依赖关系正确。

**案例：** per_tensor 函数来自 `c8a34e261`（用户标记为无需同步），但在 `93eb0b04e` 中被附带同步，引入编译错误。

---

## 11.7 检查清单

### 同步前

- [ ] 分析所有待同步提交
- [ ] 制定同步计划（含跳过列表）
- [ ] 确认用户同意的跳过决策

### 同步中

- [ ] 每笔提交单独同步并提交
- [ ] 保留 MXFP8 特有代码
- [ ] 检查命名空间归属
- [ ] 编译验证

### 同步后

- [ ] 处理审查报告（验证、修复、反驳）
- [ ] 更新审查报告
- [ ] 记录经验教训

---

## 11.8 工具与命令

### Git 命令

```bash
# 列出提交
git log --oneline --reverse <start>..<end> -- <path>

# 查看提交内容
git show <hash> --stat -- <path>

# 提取文件
git show <hash>:<path> > /target/path

# 生成 diff
git show <hash> -- <path> > /tmp/diff.txt
```

### 编译命令

```bash
# ascend950 编译
bash build.sh --pkg --ops=quant_lightning_indexer_v2 --op_debug_config ccec_g --soc=ascend950

# 查看编译日志
tail -50 build.log
```

### 检查命令

```bash
# 检查 MXFP8 特有代码
grep -n "IS_MXFP8\|_MxFP8\|SCALE_T\|CVRATIO" <file>

# 检查命名空间
grep -n "namespace\|^}" <file>

# 检查常量引用
grep -n "OLD_CONSTANT\|NEW_CONSTANT" <file>
```

---

## 11.9 参考案例

**案例：QLI V2 同步（2026-06-29）**

- 源仓库：`/home/hpc/code/ops-transformer`
- 目标仓库：`/home/hpc/code/ops-transformer_qliv2mxfp8`
- 同步提交：4 笔（`6ff944876`、`7dd5303b2`、`89fe43fde`、`93eb0b04e`）
- 跳过提交：4 笔（hifp8、NpuArch、0轴非连续、互相抵消）
- 修复问题：6 个（arch22 常量、metadata 空白、golden 门控、命名空间等）
- 编译验证：ascend950 通过

**关键文档：**
- 同步计划：`/home/hpc/skill-hpc/qliv2_sync_plan.md`
- 审查报告：`/home/hpc/skill-hpc/qliv2_sync_review.md`

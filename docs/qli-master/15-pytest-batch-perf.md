# 15 - Pytest 批跑修复与性能采集

> 本模块记录 QLI V2 算子 pytest 批跑测试（batch 模式）的系统性 bug 修复链、metadata 生成双场景分支、
> 以及性能采集（msprof）在 ProcessPoolExecutor 下采不到的根因与隔离执行方案。
> 实施时间：2026-07-21，仓库 `ops-transformer_qliv2mxfp8`。

---

## 15.1 测试架构总览

### 三种运行模式

| 模式 | 入口 | golden | metadata | 适用场景 |
|------|------|--------|----------|----------|
| single | `test_run.sh single` | 实时算 CPU golden + 实时跑 NPU | 实时生成 | 单算子调测 |
| batch（原有） | `test_run.sh batch` | 预存 .pt（含 golden） | 从 .pt 加载 | 批量精度验证 |
| batch（隔离） | `batch_isolated_run.sh` | 预存 .pt（含 golden） | 运行时生成 | 批量精度 + 性能采集 |

### 数据流

```
single 模式:
  paramset.py 硬编码参数 → qliv2_output_single(is_batch=False)
  → CPU golden + metadata 生成 + NPU 算子 → 对比

batch 模式（原有）:
  Excel → pt_save.py → .pt(含 golden + NPU 输入)
  → replace_path.py 替换 __PATH__ → pytest → pt_loadprocess.py
  → 从 .pt 加载 metadata → NPU 算子 → 对比

batch 模式（隔离）:
  Excel → pt_save.py → .pt(含 golden + NPU 输入)
  → batch_isolated_run.sh 逐用例:
       QLIV2_TESTCASE_PATH=<单用例> msprof python -m pytest
       → _is_isolated_mode=True → ThreadPoolExecutor
       → 运行时生成 metadata → NPU 算子 → 对比
       → collect_perf_data.py --incremental → result_perf.xlsx
```

---

## 15.2 batch 模式系统性 bug 修复链（7 个连锁 bug）

### Bug 1：`expected 34, got 33` — pt_save.py 漏读 weight_dtype

**根因**：`pt_save.py` 的 `load_excel_test_cases` 读取 Excel 时，`required_columns` 和 `test_cases.append(...)` 漏了 `weight_dtype` 列（Excel 有 35 列，只读了 34 列，去掉 Testcase_Name 后 33 个，但 golden.py 解包期望 34 个）。

**修复**：
- `pt_save.py` required_columns 补 `'weight_dtype'`
- `pt_save.py` append tuple 补 `row['weight_dtype']`

### Bug 2：dtype 字符串转换不完整 — golden.py is_batch 分支

**根因**：`golden.py` 的 `is_batch` 分支把 Excel 字符串转 torch dtype，但漏了 `weight_dtype` 和 `dequant_dtype='FLOAT8_E8M0FNU'`。single 模式不报错（paramset.py 用 torch 对象），batch 模式从 Excel 读到字符串。

**修复**：`golden.py` is_batch 分支补：
```python
if weight_dtype == 'FP32' or weight_dtype == 'FLOAT':
    weight_dtype = torch.float32
elif weight_dtype == 'FP16':
    weight_dtype = torch.float16
# dequant_dtype 补:
elif dequant_dtype == 'FLOAT8_E8M0FNU':
    dequant_dtype = torch.float8_e8m0fnu
```

### Bug 3：`malformed node or string: 1` — ast.literal_eval 对 int 报错

**根因**：`golden.py` is_batch 分支对 12 个字段调 `ast.literal_eval()`，但 pandas 从 Excel 读标量数字时返回 `int`（如 `output_idx_offset_enable=1`），`ast.literal_eval(1)` 抛 `ValueError`。

**修复**：新增 `_safe_eval` 安全包装：
```python
def _safe_eval(val):
    if isinstance(val, str):
        return ast.literal_eval(val)
    return val
```
替换全部 12 处 `ast.literal_eval` 调用。

### Bug 4：`max_seqlen_q Expected int found str` — params 索引错位 + 标量未转 int

**根因（双重）**：
1. 加了 `weight_dtype`（params[11]）后，params 从 33 元素变 34，`pt_loadprocess.py` 仍用旧索引：`params[18]`（原 max_seqlen_q）现在指向 cmp_residual_k（字符串 `"[1]"`）。
2. Excel 标量字段读出是 `str "8"`，从未转 int，传给 NPU 算子报 `Expected int found str`。

**修复**：
- `pt_loadprocess.py`：`params[18]→params[19]`、`params[30]→params[31]` + `int()`
- `golden.py` is_batch 分支：对 16 个标量字段统一 `int()` 转换

### Bug 5：`cpu_topk_value` 未存进 .pt — return_value 模式崩溃

**根因**：`golden.py` 的 `forward()` 返回三个值 `(y, y_value, sparse_value)`，分别赋给 `cpu_result, topk_value, cpu_topk_value`。但 `.pt` dict 只存了 `topk_value`，没存 `cpu_topk_value`（return_value 模式的真值）。`pt_loadprocess.py` 用 `.get()` 得到 None，传给 `check_result_return_value(None, ...)` 崩溃。

**修复**：
- `golden.py`：.pt dict 补 `"cpu_topk_value": cpu_topk_value`
- `pt_loadprocess.py`：加载 `cpu_topk_value = test_data.get('cpu_topk_value')`
- `pt_loadprocess.py`：返回值从 5 元素 → 7 元素（加 `cpu_topk_value, npu_topk_value`）
- `test_batch.py`：解包同步改 + 补 return_value 对比逻辑

### Bug 6：`check_result` 重复调用 — 第二次覆盖第一次且漏传参数

**根因**：`test_batch.py` 连续两个 `if npu_result != None:`，第二次 `check_result` 覆盖第一次结果，且漏传 `output_idx_offset`。

**修复**：删掉第二次调用，只保留带 `output_idx_offset` 的那次（对齐 single 模式）。

### Bug 7：`invalid literal for int() with base 10: '[0'` — result_compare_method 字符串解析

**根因**：`result_compare_method.py` 用手工 `.split(',')` + `int()` 解析列表字符串，但没处理方括号 `[]`。`'[0, 8]'.split(',')` → `['[0', ' 8]']` → `int('[0')` 崩溃。single 模式不报错（paramset.py 用 Python list）。

**修复**：全部替换为 `ast.literal_eval`：
```python
# 改前：
cu_seqlens_q = [int(x.strip()) for x in cu_seqlens_q.split(',')]
# 改后：
cu_seqlens_q = list(ast.literal_eval(cu_seqlens_q))
```
共 10 处 `.split(',')` + 3 处 `list(seqused_X)` 替换。

### 修复链总结

| # | 报错 | 根因 | 修复文件 |
|---|------|------|---------|
| 1 | `expected 34, got 33` | pt_save.py 漏读 weight_dtype | pt_save.py |
| 2 | （隐患）dtype 不匹配 | golden.py 缺 weight_dtype/FLOAT8_E8M0FNU 转换 | golden.py |
| 3 | `malformed node or string: 1` | ast.literal_eval 对 int 报错 | golden.py |
| 4 | `max_seqlen_q Expected int found str` | 索引错位 + 标量未转 int | pt_loadprocess.py + golden.py |
| 5 | `NoneType has no attribute cpu` | cpu_topk_value 未存 .pt | golden.py + pt_loadprocess.py + test_batch.py |
| 6 | （逻辑错误）重复调用 | check_result 第二次覆盖第一次 | test_batch.py |
| 7 | `invalid literal for int() '[0'` | .split(',') 不处理方括号 | result_compare_method.py |

### 核心教训

**single 和 batch 模式的参数来源不同，导致类型差异**：
- single：`paramset.py` 硬编码，dtype 是 torch 对象，列表是 Python list
- batch：Excel → pandas，dtype 是字符串，标量是 str/int 混合，列表是字符串

**batch 模式的 is_batch 分支必须做完整的类型转换**，否则连锁报错。

---

## 15.3 golden 一致性分析

### 结论：算法一致，数值不一致

两种模式走同一个 `qliv2_output_single()` 函数，CPU golden 计算逻辑完全相同，但数值不同：

1. **参数来源不同**：single 用 paramset.py（2 组），batch 用 Excel（几十组）
2. **无随机种子**：`golden.py` 数据生成用 `np.random.uniform` / `random`，全程没有 `torch.manual_seed` / `np.random.seed`

### 对比

| | single | batch |
|---|---|---|
| 参数来源 | paramset.py 硬编码 | Excel 表 |
| 随机数据 | 每次跑都不同 | .pt 生成时冻结 |
| golden 计算路径 | `qliv2_output_single(is_batch=False)` | `qliv2_output_single(is_batch=True)` |
| golden 落盘 | 不落盘 | 存 .pt（含 cpu_result + cpu_topk_value） |
| NPU 算子调用 | is_batch=False 的 else 分支 | pt_loadprocess.py |

**不能跨模式复用 golden**。

---

## 15.4 metadata 生成双场景分支

### 两种场景

| 场景 | IS_NPU_NOW | metadata 来源 | 数据流 |
|------|:---:|------|------|
| 场景一（CPU 仿真） | False | `_load_metadata_from_cpu()`（cpu_test C++ 二进制） | CPU tensor → CPU metadata → .npu() → NPU 算子 |
| 场景二（NPU 算子） | True | `quant_lightning_indexer_metadata()`（NPU op） | CPU tensor → .npu() → NPU metadata op → NPU 算子 |

### 实现

**`golden.py` + `pt_loadprocess.py` 顶部各加常量**：
```python
IS_NPU_NOW = False  # True 启用场景二
```

**`golden.py` single 模式 else 分支三段式**：
```python
if not is_npu_now:
    # 场景一：CPU 生成 metadata
    metadata = _load_metadata_from_cpu(...)
# 公共：所有 tensor .npu()
if is_npu_now:
    # 场景二：NPU metadata 算子
    metadata = torch.ops.cann_ops_transformer.quant_lightning_indexer_metadata(...)
else:
    # 场景一：metadata .npu()
    metadata = log(metadata, "metadata")
```

**`pt_loadprocess.py` 同样三段式**（batch 模式运行时生成 metadata）。

### 切换方式

```python
# golden.py 行 30
IS_NPU_NOW = False    # 改 True 启用场景二

# pt_loadprocess.py 行 25
IS_NPU_NOW = False    # 改 True 启用场景二
```

### metadata 从 .pt 移除

batch 模式不再将 metadata 存入 .pt（之前存了），改为运行时生成。`golden.py` is_batch 分支的 dict 移除了 `"metadata"` key，metadata 生成移入 else（非 batch）分支。

---

## 15.5 性能采集：ProcessPoolExecutor + msprof 不兼容

### 根因

```
msprof python -m pytest test_batch.py
  └─ 父进程 (pytest)
       └─ ProcessPoolExecutor.submit(qliv2, ...)
            └─ 子进程 (fork/spawn)
                 └─ torch.ops.cann_ops_transformer.quant_lightning_indexer(...)
                     ↑ msprof 只挂在父进程，子进程的 NPU 调用采集不到
```

### 仓库统计规律

| 批跑测试 | Executor | 有 profiling |
|----------|----------|:---:|
| quant_lightning_indexer_v2 | ProcessPoolExecutor | ❌ |
| lightning_indexer_v2 | ProcessPoolExecutor | ❌ |
| mixed_quant_sparse_flash_mla | **ThreadPoolExecutor** | ✅ |
| kv_quant_sparse_attn_sharedkv | **ThreadPoolExecutor** | ✅ |
| quant_block_sparse_attn | 无 Executor | ✅ |

**所有能采集 profiling 的批跑测试都用 ThreadPoolExecutor；所有用 ProcessPoolExecutor 的都没有。**

### 解决方案：隔离执行（参考 commit 2b4db20cc）

**核心思路**：每条用例单独拉起一个 pytest 进程，msprof 挂在该进程上，内部用 ThreadPoolExecutor（不再开子进程）。

**新增 3 个文件**：

| 文件 | 作用 |
|------|------|
| `batch_isolated_run.sh` | 遍历 .pt，逐用例 `QLIV2_TESTCASE_PATH=<单用例> msprof python -m pytest` |
| `collect_perf_data.py` | 解析 `PROF_*/op_summary*.csv`，提取 `QuantLightningIndexerV2` 的 `Task Duration(us)` |
| `test_batch.py`（修改） | `_is_isolated_mode` 分支：隔离→ThreadPoolExecutor，非隔离→ProcessPoolExecutor |

### 隔离模式触发机制

```python
# test_batch.py 顶部
_single_case_path = os.environ.get("QLIV2_TESTCASE_PATH", "").strip()
_is_isolated_mode = bool(_single_case_path)

# test_qliv2() 内分支
if _is_isolated_mode:
    with ThreadPoolExecutor(max_workers=1) as executor:  # 线程池
        ...
else:
    with ProcessPoolExecutor(max_workers=1) as executor:  # 子进程（保留向后兼容）
        ...
```

### msprof 逐用例挂载

```bash
# batch_isolated_run.sh 核心循环
for case_file in "${CASE_FILES[@]}"; do
    QLIV2_TESTCASE_PATH="${case_file}" msprof python3 -m pytest ...
    # 跑完立即增量收集
    python3 collect_perf_data.py --incremental --test_result_path result.xlsx
    # 重命名 PROF 目录防覆盖
    mv PROF_*/ PROF_*_${case_name%.pt}
done
```

### PROF 数据与用例一一对应

每条用例独立 msprof session → 独立 PROF_* 目录 → 独立 op_summary.csv → CSV 里只有一行 `QuantLightningIndexerV2`。跑完立即 `mv PROF_* PROF_*_用例名`，再增量收集。

---

## 15.6 使用方式

### 完整流程

```bash
cd tests/pytest

# 1. 生成 .pt（只需一次）
python3 batch/quant_lightning_indexer_v2_pt_save.py ./testcases_red.xlsx ./pt_path

# 2. 跑测试
# 2a. 采集性能（msprof 逐用例挂载）
bash batch_isolated_run.sh ./pt_path 1

# 2b. 不采集性能，仅隔离执行
bash batch_isolated_run.sh ./pt_path 0

# 2c. 原有方式（非隔离，需 replace_path）
python3 batch/replace_path.py test_quant_lightning_indexer_v2_batch.py ./pt_path
python3 -m pytest -rA -s test_quant_lightning_indexer_v2_batch.py -v -m ci
cp test_quant_lightning_indexer_v2_batch.py.bak test_quant_lightning_indexer_v2_batch.py
```

### 产物文件

| 文件 | 何时产生 | 内容 |
|------|----------|------|
| `result.xlsx` | 每次 | 精度结果（用例名、参数、result、fulfill_percent） |
| `result_perf.xlsx` | 仅 `./pt_path 1` | 精度结果 + 性能数据（Task Duration(us)） |
| `batch_summary.log` | 每次 | 每条用例完整 stdout 日志 |
| `batch_fail_list.log` | 有失败时 | 失败用例名清单 |
| `PROF_*_<用例名>/` | 仅 `./pt_path 1` | msprof 原始数据目录 |

### 文件覆盖/追加行为

**脚本级（每次跑）：覆盖**——开头先删旧文件。
**脚本内循环（逐用例）：追加**——每条用例往 result.xlsx 追加一行。

---

## 15.7 params 索引映射（34 元素，weight_dtype 在 [11]）

```
[0]  batch_size              [18] cmp_residual_k
[1]  q_seq                   [19] max_seqlen_q
[2]  k_seq                   [20] quant_mode
[3]  q_t_size                [21] layout_query
[4]  k_t_size                [22] layout_key
[5]  q_head_num              [23] sparse_count
[6]  k_head_num              [24] sparse_mode
[7]  head_dim                [25] query_datarange
[8]  block_size              [26] key_datarange
[9]  block_num               [27] weights_datarange
[10] qk_dtype                [28] q_scale_datarange
[11] weight_dtype  ← 新增    [29] k_scale_datarange
[12] dequant_dtype           [30] cmp_ratio
[13] actual_seq_dtype        [31] return_value
[14] cu_seqlens_q            [32] output_idx_offset_enable
[15] cu_seqlens_k            [33] output_idx_offset_datarange
[16] seqused_q
[17] seqused_k
```

> ⚠️ 主仓 ops-transformer 是 33 元素（无 weight_dtype），索引 [11] 之后整体差 1。

---

## 15.8 核心经验

1. **single 和 batch 参数来源不同导致类型差异**——batch 从 Excel 读到的是字符串，is_batch 分支必须做完整类型转换（dtype/标量/list 三类）。

2. **params tuple 不可变**——golden.py 的局部变量转换不影响 tuple 本身，pt_loadprocess.py 从 params 读值时必须单独转换。

3. **ast.literal_eval 只接受 str**——pandas 从 Excel 读标量数字返回 int，直接传会报 `malformed node or string`，需 `_safe_eval` 包装。

4. **列表字符串不能用 split 解析**——`'[0, 8]'.split(',')` 产生 `['[0', ' 8]']`，`int('[0')` 崩溃，必须用 `ast.literal_eval`。

5. **ProcessPoolExecutor + msprof 不兼容**——msprof 只挂父进程，子进程的 NPU 调用采集不到。解决方案：隔离执行（每条用例独立 pytest 进程 + ThreadPoolExecutor）。

6. **msprof 逐用例挂载 + PROF 目录重命名**——避免所有用例混在一个 CSV 里无法区分，每条用例跑完立即 `mv PROF_* PROF_*_用例名`。

7. **隔离模式用环境变量触发**——`QLIV2_TESTCASE_PATH` 设置时走 ThreadPoolExecutor，未设置走 ProcessPoolExecutor（向后兼容）。

8. **隔离执行不需要 replace_path**——通过环境变量传单条用例路径，绕过 `__PATH__` 占位符，无需改文件+还原。

# CV 比例 1:1 适配（新芯片）

## 背景

新芯片将 AIC:AIV 核比例从 1:2 改为 1:1，需要相应调整 QLI 算子配置。

## 核心修改

### 1. 核比例宏定义

**文件**: `op_kernel/quant_lightning_indexer_common.h`

```cpp
// 修改前（1:2 比例）
#define CVRATIO 2

// 修改后（1:1 比例）
#define CVRATIO 1
```

### 2. 核类型声明

**文件**: `op_kernel/quant_lightning_indexer.cpp`

```cpp
// 修改前
KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_MIX_AIC_1_2);

// 修改后
KERNEL_TASK_TYPE_DEFAULT(KERNEL_TYPE_MIX_AIC_1_1);
```

### 3. 移除基本块减半逻辑

**文件**: `op_kernel/arch35/quant_lightning_indexer_kernel.h`

```cpp
// 删除以下代码（InitTilingData 函数中）
constInfo.mBaseSize /= 2;
constInfo.s1BaseSize /= 2;
```

**原因**: CVRATIO=2 时需要减半以适配 UB 大小限制，CVRATIO=1 后不再需要。

### 4. 统一 Scale 加载逻辑

**文件**: `op_kernel/arch35/quant_lightning_indexer_service_cube.h`

```cpp
// LoadQScaleToL1 和 LoadKScaleToL1 函数中
// 删除以下守卫条件
if constexpr (!IS_MXFP8) return;
```

**影响**: Scale 加载现在对所有量化模式生效，不再仅限 MXFP8。这简化了代码逻辑，使 scale 处理统一化。

### 5. 清理调试代码

删除了多处 `DumpTensor` 和 `printf` 调试输出。

### 6. 调整 LoadKeyToL0b 参数设置

```cpp
// 将 ifTranspose 设置移到 MXFP8 分支外
loadData2DParams.ifTranspose = false;  // 对所有模式统一设置

if constexpr (IS_MXFP8) {
    // MXFP8 特定逻辑
} else {
    // 非 MXFP8 逻辑（不再需要单独设置 ifTranspose）
}
```

## 影响分析

| 方面 | CVRATIO=2 (旧) | CVRATIO=1 (新) | 说明 |
|------|----------------|----------------|------|
| 核配比 | 1 AIC : 2 AIV | 1 AIC : 1 AIV | 硬件配置变化 |
| mBaseSize | 减半 (如 128→64) | 不减半 (128) | UB 空间充足 |
| s1BaseSize | 减半 (如 4→2) | 不减半 (4) | 同上 |
| Scale 加载 | 仅 MXFP8 | 所有模式 | 逻辑统一化 |
| 核间同步 | 需等待两个 AIV | 只需等待一个 AIV | 简化同步 |
| UB 使用 | 紧张，需复用策略 | 宽松 | 每核处理数据量减半 |

## 与 dualDstCtl 的关系

CVRATIO=1 后，一个 AIC 只对应一个 AIV，**不再需要 `dualDstCtl=1` 拆分 M 维度**。这为启用 Fixpipe 随路 ReLU 创造了条件（详见 `docs/relu_move_to_fixpipe_plan.md`）。

```cpp
// CVRATIO=2 时需要拆 M 给两个 AIV
fixpipeParams.dualDstCtl = 1;  // 按 M 维度拆分

// CVRATIO=1 后不需要拆分
fixpipeParams.dualDstCtl = 0;  // 单目标模式
fixpipeParams.reluEn = true;   // 可启用随路 ReLU
```

## InitBuffers 缓冲区大小修改清单（⚠️ 最关键）

CVRATIO 改变后，所有依赖 `s1BaseSize_`/`mBaseSize` 的 UB buffer 大小必须重新计算。

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_service_vector.h` → `InitBuffers`（Line 160）

| Buffer | 公式 | 当前行号 |
|--------|------|---------|
| `resMm1Buf_` | `2 * CeilDiv(mBaseSize, CVRATIO) * s2BaseSize * sizeof(QK_T)` | Line 163 |
| `weightBuf_` | `2 * CeilDiv(s1BaseSize, CVRATIO) * UB_BANK_DEPTH_STRIDE` | Line 166 |
| `weightTempBuf_` | `2 * CeilDiv(s1BaseSize, CVRATIO) * UB_BANK_DEPTH_STRIDE` | Line 168 |
| `qScaleBuf_` | `2 * CeilDiv(s1BaseSize, CVRATIO) * UB_BANK_DEPTH_STRIDE`（仅非MXFP8） | Line 174 |
| `outBuf_` | `2 * CeilDiv(s1BaseSize, CVRATIO) * s2BaseSize * sizeof(uint16_t)` | Line 177 |

> **关键教训**：InitBuffers 直接决定 UB 缓冲区分配大小，CVRATIO 改变后遗漏任何一个都会导致运行时内存分配失败或崩溃。这是最关键但最容易遗漏的修改点。

### S1 任务分配逻辑修改点

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_service_vector.h`

| 修改点 | 当前行号 | 代码 |
|--------|---------|------|
| s1BaseSizePerAIV 计算 | Line 433 | `CeilDiv(s1BaseSize_, CVRATIO)` |
| ProcessVec1 S1 分配 | Line 437-438 | `curAivS1Idx` / `curAivS1ProcNum` |
| ProcessVec1 输出偏移 | Line 521 | `vec1OutGmOffset` |
| ProcessTopK S1 分配 | Line 546-547 | 同 ProcessVec1 |
| ProcessTopK 索引计算 | Line 582-583 | `rowIdx` / `vecOffset` |

### 核 ID 映射修改点

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_kernel.h`

| 修改点 | 当前行号 | 代码 |
|--------|---------|------|
| aiCoreIdx 计算 | Line 475-476 | `aiCoreIdx = tmpBlockIdx / CVRATIO` |
| aivCoreNum 计算 | Line 726 | `aivCoreNum = GetBlockNum() * CVRATIO` |

## 条件编译保护 Event ID

CVRATIO=1 时只有一个 AIV 核，不需要等待/设置第二个 AIV 核的同步信号。使用 `#if CVRATIO > 1` 条件编译消除无用代码。

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_service_cube.h`

```cpp
// Line 194-199: 等待 Vector 核完成
CrossCoreWaitFlag<...>(CROSS_VC_EVENT + runInfo.loop % 2);
#if CVRATIO > 1
CrossCoreWaitFlag<...>(CROSS_VC_EVENT + runInfo.loop % 2 + AIV0_AIV1_OFFSET);  // 第二个AIV
#endif

// Line 361-366: 通知 Vector 核
CrossCoreSetFlag<...>(CROSS_CV_EVENT + runInfo.loop % 2);
#if CVRATIO > 1
CrossCoreSetFlag<...>(CROSS_CV_EVENT + runInfo.loop % 2 + AIV0_AIV1_OFFSET);  // 第二个AIV
#endif
```

**原理**：
- CVRATIO=1：编译器直接消除 `#if CVRATIO > 1` 内的代码，零运行时开销
- CVRATIO>1：正常编译第二个核的同步逻辑

## V1 vs V2 基本块差异

> ⚠️ qli-change-to-1-1 skill 描述的是 V1 的减半逻辑，V2 代码**不存在**减半。

| 维度 | V1 (skill 描述) | V2 (当前代码) |
|------|----------------|--------------|
| 基本块常量 | `S1_BASE_SIZE`/`S2_BASE_SIZE`/`M_BASE_SIZE` | `M_BASE_SIZE=256`/`S2_BASE_SIZE=128`（无 `S1_BASE_SIZE`） |
| s1BaseSize 计算 | `S1_BASE_SIZE` 直接赋值 | `mBaseSize / gSize` 动态计算（Line 217） |
| 减半逻辑 | `mBaseSize /= 2; s1BaseSize /= 2` | **不存在**（V2 直接 `mBaseSize = M_BASE_SIZE`） |

## 验证要点

1. **分核逻辑**: 确认 SplitCore 正确处理 CVRATIO=1
2. **UB 空间**: 验证各 buffer 大小计算正确（按 InitBuffers 清单逐一检查）
3. **核间同步**: 确认 `#if CVRATIO > 1` 条件编译正确消除第二个 AIV 的同步
4. **Scale 加载**: 验证非 MXFP8 模式下 scale 加载正确性
5. **性能**: 对比 1:2 和 1:1 配置的性能差异

## 相关文件

- `op_kernel/arch35/quant_lightning_indexer_v2_common.h` - CVRATIO 宏定义（Line 28）
- `op_kernel/quant_lightning_indexer_v2.cpp` - 核类型声明（Line 51）
- `op_kernel/arch35/quant_lightning_indexer_v2_kernel.h` - 核 ID 映射、基本块大小
- `op_kernel/arch35/quant_lightning_indexer_v2_service_cube.h` - Scale 加载、条件编译 Event ID
- `op_kernel/arch35/quant_lightning_indexer_v2_service_vector.h` - InitBuffers、S1 任务分配

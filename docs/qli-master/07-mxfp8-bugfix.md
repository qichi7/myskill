# MXFP8 优化与 Bug 修复

## 1. Scale Buffer 大小修复

**问题**：MXFP8_SCALE_EXTRA 和 kL1ExtraScale 计算错误，导致 scale buffer 空间不足。

**修复**：
```cpp
// 修改前
MXFP8_SCALE_EXTRA = M_BASIC_BLOCK * 2;  // = 512 bytes
kL1ExtraScale = s2BasicBlock_ * 2;      // = 256 bytes

// 修改后
MXFP8_SCALE_EXTRA = M_BASIC_BLOCK * D_BASIC_BLOCK / 32;  // = 1024 bytes
kL1ExtraScale = s2BasicBlock_ * D_BASIC_BLOCK / 32;      // = 512 bytes
```

**原因**：每个 token 需要 D/32 = 4 个 fp8_e8m0 scale，总需求 = tokens × 4 bytes。

## 2. ReLU 移至 Fixpipe 随路执行

**背景**：新芯片 CVRATIO=1:1，不再需要 dualDstCtl=1 拆分 M 维度，可以启用 Fixpipe 随路 ReLU。

**修改**：
```cpp
// service_cube.h Fixp()
fixpipeParams.dualDstCtl = 0;      // 单目标模式（CVRATIO=1）
fixpipeParams.reluEn = true;       // 启用随路 ReLU

// vector1.h 所有 MulWeightAndReduceSum* 函数
// 删除所有 MicroAPI::Relu 调用
// WeightedAccum 改为 ApplyRelu=false
```

**收益**：每个 G-loop 迭代省去 2~4 条 Relu VF 指令，Fixpipe ReLU 零额外 cycle。

## 3. BatchMulWeightAndReduceSum 支持任意 batch

**问题**：原实现只支持 batch=1 或 batch=2，s1Base=4 时需要支持 batch=4。

**修改**：
```cpp
// 修改前
if (batch != 2 && batch != 1) return;
if (batch == 2) { ... } else { ... }

// 修改后
if (batch < 1) return;
int pairCount = batch / 2;
for (int p = 0; p < pairCount; p++) {
    // 处理第 2p 和 2p+1 行
}
if (batch % 2 != 0) {
    // 处理最后一行（奇数情况）
}
```

**影响**：同时修改了 `BatchMulWeightAndReduceSum` 和 `BatchMulWeightAndReduceSum_MxFP8` 两个版本。

## 4. 函数重命名：NoScale → MxFP8

将所有 `*_NoScale` 函数重命名为 `*_MxFP8`，更准确反映其用途：
- `MulWeightAndReduceSum_NoScale` → `MulWeightAndReduceSum_MxFP8`
- `MulWeightAndReduceSum2_NoScale` → `MulWeightAndReduceSum2_MxFP8`
- `BatchMulWeightAndReduceSum_NoScale` → `BatchMulWeightAndReduceSum_MxFP8`

## 5. Q/QScale 和 K/KScale L1 Buffer 分离

**背景**：原实现将 scale 数据存储在 query/key L1 buffer 的尾部，计算复杂且容易出错。

**修改**：
```cpp
// 新增独立 buffer
TBuf<TPosition::A1> bufQScaleL1_;
LocalTensor<SCALE_T> qScaleL1_;
TBuf<TPosition::B1> bufKScaleL1_;
LocalTensor<SCALE_T> kScaleL1_;

// InitBuffers
pipe->InitBuffer(bufQScaleL1_, QUERY_BUF_NUM * QSCALE_L1_PINGPONG_SIZE * sizeof(SCALE_T));
pipe->InitBuffer(bufKScaleL1_, KEY_BUF_NUM * KSCALE_L1_SIZE * sizeof(SCALE_T));
```

**收益**：
- 代码更清晰，偏移计算简化
- 不再需要 QUERY_L1_PINGPONG_STRIDE 和 MXFP8_SCALE_EXTRA
- qScale 使用 ping-pong buffer，kScale 使用 triple buffer（与 key 一致）

## 6. Fixpipe dstNdStride 修复

**问题**：当 nSize > 64 时，Fixpipe 拆分为两个 ND 块，dstNdStride 计算错误导致第二个 ND 块覆盖第一个 ND 块的后半部分。

**修复**：
```cpp
// 修改前
fixpipeParams.params.dstNdStride = constInfo_.s2BaseSize * constInfo_.mBaseSize / 2;

// 修改后
fixpipeParams.params.dstNdStride = fixpipeParams.mSize * fixpipeParams.dstStride;
```

**原因**：dstNdStride 应该等于第一个 ND 块的总占用空间（含每行 padding）。

**示例**（mSize=256, dstStride=128）：
- 修改前：128 × 256 / 2 = 16384
- 修改后：256 × 128 = 32768 ✅

## 7. Vector 核 qkVLstride 修复

**问题**：qkVLstride 与 Fixpipe 的 dstNdStride 不匹配，导致 Vector 核读取位置错误。

**修复**：
```cpp
// 修改前
auto qkVLstride = (UB_BANK_DEPTH_STRIDE / sizeof(float)) / 2 * constInfo_.mBaseSize;

// 修改后
auto qkVLstride = constInfo_.mBaseSize * (UB_BANK_DEPTH_STRIDE / sizeof(float));
```

**原因**：qkVLstride 必须等于 Fixpipe 的 dstNdStride，确保 Vector 核读取第二个 ND 块的位置正确。

**示例**（mBaseSize=256）：
- 修改前：64 × 256 = 16384
- 修改后：256 × 128 = 32768 ✅

## 8. LoadKScaleToL1 偏移修复

**问题**：非 PA 路径传入绝对偏移（s2GmBaseOffset + s2GmOffset），但 tensorKeyScaleOffset 已包含 s2GmBaseOffset，导致重复计算。

**修复**：
```cpp
// 修改前
LoadKScaleToL1(s2L1RealSize, s2GmBaseOffset + s2GmOffset, runInfo);

// 修改后
if (PAGE_ATTENTION) {
    LoadKScaleToL1(s2L1RealSize, s2GmBaseOffset + s2GmOffset, runInfo);  // PA 需要绝对偏移
} else {
    LoadKScaleToL1(s2L1RealSize, s2GmOffset, runInfo);  // 非 PA 只需相对偏移
}
```

**原因**：
- `tensorKeyScaleOffset = (prefixSum + s2Idx * s2BaseSize) * 4` 已包含 s2GmBaseOffset
- `s2GmOffset` 是 s2BaseSize 块内部的相对偏移（处理 s2BaseSize > s2BasicBlock_ 的情况）
- 两者互补，不重复

**数值示例**（s2Idx=1, s2BaseSize=128, s2BasicBlock_=128）：
- tensorKeyScaleOffset = 512
- s2GmOffset = 0（循环内）
- 修改前 gmOffset = 512 + 128×4 = 1024 ❌
- 修改后 gmOffset = 512 + 0×4 = 512 ✅

## 9. LoadData2DMxParams xStartPosition 偏移修复

### 问题现象

- 当前因 `M_BASIC_BLOCK = M_BASIC_BLOCK_L0 = 256`，`s1gL1Offset`/`s2L1Offset` 恒为 0，不报错
- 但写法不正确：`xStartPosition = 0` 硬编码，一旦基本块关系改变（如 `M_BASIC_BLOCK_L0 < M_BASIC_BLOCK`）会导致 scale 与 data 错位

### 根因

MXFP8 的 `LoadData(dst, src, scaleL1, loadData2DParams, loadDataMxParams)` 中，data 和 scale 是**两条独立的硬件指令**分别加载的：

```cpp
// data 指令: load_cbuf_to_ca(dst, src0, mStartPosition, ...)
// scale 指令: load_cbuf_to_ca_mx(mxDstAddr, src1, xStartPosition, ...)
```

`mStartPosition` 和 `xStartPosition` 各自独立传给各自指令，**不存在硬件自动同步**。scale buffer 中第 0 行对应 data 的第 `s1gGmOffset` 行（GM 绝对位置），第 `s1gL1Offset` 行才对应当前 data 片段。若 `xStartPosition=0` 而 `mStartPosition≠0`，scale 和 data 错位。

### xStartPosition 语义

`LoadData2DMxParams` 有两组方向参数：

| 参数 | 维度 | 含义 | D:32 压缩比影响 |
|------|------|------|----------------|
| `xStartPosition` / `xStep` | **M(N) 维** — token 维度 | scale 在 token 维的起始 block / 步长 | **不影响** |
| `yStartPosition` / `yStep` | **K(D) 维** — headDim 维度 | scale 在 D 维的起始 block / 步长 | 影响（`/FP8_TWO` 打包） |

D 维 32:1 的压缩比只影响 `y` 系列参数（`yStartPosition`/`yStep`/`srcStride`），`x` 系列与 data 的 `mStartPosition` 完全对齐。

scale 的 NZ 布局中，每个 token 在 N 维占 1 个位置（`dstNzNStride=1`），与 D 维压缩比无关。

### 修复

```cpp
// LoadQueryToL0a (Q 侧)
// 修复前: loadDataMxParams.xStartPosition = 0;
loadDataMxParams.xStartPosition = CeilDiv(s1gL1Offset, BLOCK_CUBE);  // 与 mStartPosition 一致

// LoadKeyToL0b (K 侧)
// 修复前: loadDataMxParams.xStartPosition = 0;
loadDataMxParams.xStartPosition = CeilDiv(s2L1Offset, BLOCK_CUBE);  // 与 mStartPosition 一致
```

### 官方参考

CANN matmul 库的参考实现（`load_to_l0a_load2dV2.h:199`、`load_to_l0b_load2dV2.h:164/202`）：

```cpp
// A 侧非转置
loadDataMxParams.xStartPosition = CeilDiv(aAuxL1MOffset, BLOCK_CUBE);

// B 侧
loadDataMxParams.xStartPosition = CeilDiv(bAuxL1NOffset, BLOCK_CUBE);
```

与对应的 `mStartPosition` 完全一致。

### 检查要点

- **`xStartPosition` 必须与 `mStartPosition` 用同一个 L1 offset、同一个 `BLOCK_CUBE` 除数**
- data 和 scale 是独立硬件指令加载，不存在自动同步
- D:32 压缩比只影响 `y` 系列参数，不影响 `x` 系列
- K 侧（`LoadKeyToL0b`）在 `s2BasicBlock_=256` 分支中 `s2L1Offset` 可能为非 0（如 128），同样存在隐患

## 10. LoadData2DMxParams 参数体系完整说明

### 参数语义

`LoadData2DMxParams` 有两组方向参数，分别对应 scale 的两个维度：

| 参数 | 维度 | 含义 | D:32 压缩比影响 |
|------|------|------|----------------|
| `xStartPosition` | **M(N) 维** — token 维度 | scale 在 token 维的起始 block | **不影响** |
| `xStep` | M(N) 维 | scale 在 token 维的步长 | 不影响 |
| `yStartPosition` | **K(D) 维** — headDim 维度 | scale 在 D 维的起始 block | 影响（`/FP8_TWO` 打包） |
| `yStep` | K(D) 维 | scale 在 D 维的步长 | 影响（`/FP8_TWO` 打包） |
| `srcStride` | K(D) 维 | L1 中 scale 行间 stride | 影响（`/FP8_TWO` 打包） |
| `dstStride` | K(D) 维 | L0 中 scale 行间 stride | 影响（`/FP8_TWO` 打包） |

### 与 LoadData2DParamsV2 的对应关系

| LoadData2DParamsV2 (data) | LoadData2DMxParams (scale) | 关系 |
|---------------------------|---------------------------|------|
| `mStartPosition` | `xStartPosition` | **必须一致**（同 offset，同 BLOCK_CUBE 除数） |
| `kStartPosition` | `yStartPosition` | 独立（scale 的 D 维有压缩） |
| `mStep` | `xStep` | 必须一致 |
| `kStep` | `yStep` | 独立（scale 的 D 维有压缩） |

### 底层硬件指令

data 和 scale 是**两条独立的硬件指令**分别加载（`op_kernel/arch35/quant_lightning_indexer_v2_service_cube.h` → `LoadQueryToL0a`/`LoadKeyToL0b`）：

```cpp
// data 指令: load_cbuf_to_ca(dst, src0, mStartPosition, kStartPosition, ...)
// scale 指令: load_cbuf_to_ca_mx(mxDstAddr, src1, xStartPosition, yStartPosition, ...)
```

不存在硬件自动同步——data 指令只管 data buffer 的偏移，scale 指令只管 scale buffer 的偏移。

### Scale NZ 布局中 token 维的位置

scale 的 NZ 布局中，每个 token 在 N 维占 **1 个位置**（`dstNzNStride=1`），与 D 维压缩比无关。D:32 压缩比只影响 C0 维（`nValue = scalePerToken / FP8_TWO`）。

## 11. MLA Prolog vs QLI V2 MXFP8 实现对比

mxfp8-master skill 描述的是 MLA Prolog 算子的 MXFP8 实现，与 QLI V2 的实现方式有显著差异：

| 维度 | MLA Prolog | QLI V2 |
|------|-----------|--------|
| 类型模板 | `MLAPType` | `QLIV2Type` |
| 判断标志 | `isFp8E8m0` | `isMxFp8` / `IS_MXFP8` |
| Scale L1 存储 | L1B 上半区（与 B 数据共址） | **独立 buffer**（`bufQScaleL1_`/`bufKScaleL1_`） |
| Scale 缓冲策略 | 不做双缓冲 | qScale ping-pong, kScale triple buffer |
| Scale 同步 | `SCALE_EVENT` 信号量保护读写 | **无此机制**（buffer 隔离，不需要） |
| GM→L1 加载 | `LoadL1AAndScale`（数据+scale合并函数） | `QueryNd2Nz` + `LoadQScaleToL1`（拆分两个函数） |
| L1→L0 加载 | `LoadDataL1ToL0Mxfp8` | `LoadQueryToL0a`（内联 MXFP8 分支） |
| 矩阵乘函数 | `MatmulL0`/`MatmulL1`/`MatmulSplitK` | `ComputeMm1`/`ComputeL0c`/`Fixp` |
| 常量名 | `FP8_E4M3_BLOCK_SIZE`/`K_STEP_SIZE_32`/`BLOCK_CUBE_SIZE` | `MXFP8_BLOCK_SIZE`/`FP8_BLOCK_CUBE`/`BLOCK_CUBE` |
| 动态量化 | `RmsNormDynamicQuant` + `DynamicQuantPerBlockMxfp8Vf` | **不存在**（QLI V2 不做动态量化） |
| KV Cache 量化 | `RmsNormAndQuantizeCkvMxfp8` | **不存在** |
| `QUANT_MODE` enum | 7/8/9 三种模式 | **不存在** |
| baseN 调整 | MXFP8 下 baseN 从 128 减到 64 | 无 baseN 概念，使用 `M_BASIC_BLOCK=256` |

### QLI V2 的设计选择

- **独立 Scale buffer**：QLI V2 将 scale 存储在独立的 `bufQScaleL1_`/`bufKScaleL1_` 中，而非 MLA Prolog 的 L1B 共址方式，因此不需要 `SCALE_EVENT` 信号量保护
- **数据/scale 搬运分离**：`QueryNd2Nz`/`KeyNd2Nz` 只搬运数据，`LoadQScaleToL1`/`LoadKScaleToL1` 独立搬运 scale，代码更清晰
- **无动态量化**：QLI V2 的输入已经是量化后的 fp8 数据 + scale，不需要在算子内部做 RmsNorm + 动态量化

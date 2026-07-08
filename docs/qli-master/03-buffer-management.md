# Buffer 管理

## Double/Triple Buffer

- **L1**: 双缓冲 (ping-pong)
- **L0**: 四缓冲池

```cpp
pipe->InitBuffer(bufQL1_, 2 * M_BASIC_BLOCK * D_BASIC_BLOCK);
pipe->InitBuffer(bufKeyL1_, 3 * s2BasicBlock * D_BASIC_BLOCK);
```

## Bank冲突避免

```cpp
#define UB_BANK_STRIDE 256  // B

DataCopyPad(weightUB_[pingpong * UB_BANK_STRIDE / sizeof(W_T)], ...);
```

## arch35 UB 使用详解

### UB 占用规则

- **占 UB**: `TBuf<TPosition::VECCALC>` — 所有 VECCALC 类型 buffer
- **不占 UB**: `TPosition::A1/B1` (L1), `TPosition::A2/B2/CO1` (L0)

### AIC 核 UB（仅 bufUB_）

```cpp
bufUB_ = 2 × CeilDiv(mBaseSize, 2) × s2BaseSize × sizeof(float)
```

| gSize | s1Base=2 (有/=2) | s1Base=4 (无/=2) |
|-------|------------------|------------------|
| 8 | 8 KB | 16 KB |
| 16 | 16 KB | 32 KB |
| 32 | 32 KB | 64 KB |
| 64 | 64 KB | 128 KB |

### AIV 核 UB（全部 buffer 占 UB）

| Buffer | 公式 | s1Base=2, g=64 | s1Base=4, g=64 |
|--------|------|----------------|----------------|
| resMm1Buf_ | `2×CeilDiv(mBase,2)×128×4` | 64 KB | 128 KB |
| weightBuf_ | `2×CeilDiv(s1Base,2)×512` | 1 KB | 2 KB |
| weightFloatBuf_ | 同上 | 1 KB | 2 KB |
| kScaleBuf_ (half) | `2×128×16×2` | 8 KB | 8 KB |
| kScaleBuf_ (float) | `2×128×16×4` | 16 KB | 16 KB |
| qScaleBuf_ | `2×CeilDiv(s1Base,2)×512` | 1 KB | 2 KB |
| outBuf_ | `2×CeilDiv(s1Base,2)×128×2` | 0.5 KB | 1 KB |
| mrgValueBuf_ | `(2048+16384)×2` | 36.5 KB | 36.5 KB |
| indicesOutBuf_ | `(2048+64)×4` | 8.25 KB | 8.25 KB |
| scoreOutBuf_ | `2048×2` | 4 KB | **复用** |
| topkSharedTmpBuf_ | 见下方 | 45.25 KB | **复用** |
| **合计 (MXFP8)** | | **160 KB** | **177 KB** |
| **合计 (非MXFP8 float)** | | **177 KB** | **195 KB** |

### topkSharedTmpBuf_ 计算

```cpp
// topK=2048, trunkLen=16384
bufferSize1 = (2×2048 + 3×256 + 64) × 4 = 17,664 B
bufferSize2 = (2048 + 16384) × 2         = 36,864 B
reuse       = 2048 × 4                    =  8,192 B  // tmpIndex 与 hisIndex[1] 空间复用
合计         = 17664 + 36864 - 8192       = 46,336 B ≈ 45.25 KB
```

## s1BaseSize/mBaseSize 减半影响分析

### 值变化

```cpp
// 去掉 /= 2 后：
s1BaseSize: 2 → 4（翻倍）
mBaseSize:  2×gSize → 4×gSize（翻倍）
```

### 自动适配（无需修改）

| 模块 | 原因 |
|------|------|
| 分核逻辑 (SplitCore) | 基于 constInfo 动态计算，块数减半 |
| Offset 计算 | `gS1Idx * mBaseSize * headDim` 自动正确 |
| Causal Mask | `s1Offset = s1BaseSize * s1gIdx` 自动正确 |
| VF 函数 batch | `CeilDiv(4,2)=2` → 调用 MulWeightAndReduceSum2（已实现） |
| Fixpipe dstNdStride | `s2BaseSize * mBaseSize / 2` 同步翻倍仍匹配 |
| Tiling workspace | 已按 s1BaseSize=4 分配（tiling.cpp qli3510S1Base=4） |
| qkVLstride | `64 * mBaseSize` 与 Fixpipe 同步翻倍 |

### 唯一风险：UB 溢出

gSize=64 时 bufUB_ 从 64KB→128KB，resMm1Buf_ 从 64KB→128KB。
arch35 UB 上限 256KB，需通过 UB 复用策略节省空间。

## UB 复用策略（arch35 AIV 核）

### 原理

ProcessVec1 使用 resMm1UB_ 存 QK 结果 → 处理完写出到 scoreGm → resMm1UB_ 空闲。
ProcessTopK 仅在最后一个 S2 循环调用，此时 ProcessVec1 已结束，可安全复用。

### 复用布局

```
resMm1UB_ 总大小: 128 KB (gSize=64, s1Base=4)

ProcessVec1 阶段: QK 结果数据（Cube→Vector）
ProcessTopK 阶段复用:
  ┌─ topkSharedTmpLocal_ (offset 0) ──────────────────┐
  │ [0..2047]    hisIndexLocal[1]  (2048 u32 = 8KB)    │
  │ [2048..2303] histogramsLocal  (256 u32 = 1KB)      │
  │ [2304..2559] idxHighLocal     (256 u32 = 1KB)      │
  │ [2560..2815] idxLowLocal      (256 u32 = 1KB)      │
  │ [2816..2879] nkValueLocal     (64 u32 = 256B)      │
  │ [2880..]     tmpIndexLocal    (复用 hisIndex[1])    │
  └────────────────────────────────────────────────────┘
  ┌─ scoreOutLocal_ (offset 2880 u32) ─────────────────┐
  │ [2880..4927]  scoreOutLocal   (2048 u16 = 4KB)     │
  └────────────────────────────────────────────────────┘
  ┌─ outInvalidLocal_ (offset 0, 与 topk 互斥) ────────┐
  │ [0..127]      outInvalidLocal (128 i32 = 512B)     │
  └────────────────────────────────────────────────────┘
  已用: ~13.8 KB | 剩余: ~114.2 KB
```

### 不可复用的 buffer

| Buffer | 原因 |
|--------|------|
| mrgValueLocal_ | 跨 topk 迭代使用（mrgValueLocal_[topkCountAlign256_] 存下一轮数据） |
| indicesOutLocal_ | hisIndexLocal[0] 复用它，跨迭代使用 |

### 时序安全保证

```
同一 batch 内:
  actS1/actS2==0 → CleanInvalidOutput(outInvalidLocal_) → continue
  正常流程 → ProcessVec1(resMm1UB_) → ... → ProcessTopK(resMm1UB_)
  两条路径互斥，不会同时访问 resMm1UB_

不同 batch 之间: 串行执行
```

### TBuf 管理安全

- `resMm1Buf_` 通过 `pipe->InitBuffer` 注册，生命周期覆盖整个 kernel
- ReinterpretCast 切出的 LocalTensor 只是指针操作，不需要 pipe 额外管理
- 删除的 TBuf（scoreOutBuf_, topkSharedTmpBuf_）声明和 InitBuffer 都已删除，pipe 不再管理
- pipe 管理的 buffer 从 10 个减少到 8 个

## UB 复用重叠 Bug：scoreOutLocal_ 与 tmpIndexLocal

### 问题现象

- `return_value=0`（不输出 TopK 分数值）时索引精度 **pass**
- `return_value=1`（输出 TopK 分数值）时索引精度 **fail**，索引值远超 k_seq（如 14852，k_seq=20）

### 根因

`scoreOutLocal_` 和 topk 的 `tmpIndexLocal` 都复用 `resMm1UB_`，但 `scoreOutOffsetInU32` 计算遗漏了 `tmpIndexLocal` 的大小，导致**两者起始地址完全重叠**。

**topkSharedTmpLocal_ 内部布局**（以 topk=512 为例，字节偏移）：

```
[0, 1024)     hisIndexLocal[1]    ← Align(512,256)=512 个 uint32
[1024, 2048)  histogramsLocal     ← 256 个 uint32
[2048, 3072)  idxHighLocal        ← 256 个 uint32
[3072, 4096)  idxLowLocal         ← 256 个 uint32
[4096, 4352)  nkValueLocal        ← 64 个 uint32
[4352, 39168) tmpIndexLocal       ← (512+16384) 个 uint16 ← 索引数据!
```

**旧偏移**：`scoreOutOffsetInU32 = topkCountAlign256_ + 3*256 + 64 = 1344`，字节偏移 = `1344 * 4 = 5376`，**落在 tmpIndexLocal [4352, 39168) 范围内**。

### 触发机制

```cpp
// topk.h TopK(), s2LoopNum==1 分支:
if (isNeedLD || returnValueFlag) {
    LiTopKVF<true>(tmpIndexLocal, hisValueLocal, ...);
    //  步骤1: 写索引到 tmpIndexLocal
    //  步骤2: 因<true>，写分数到 hisValueLocal(=scoreOutLocal_)
    //         → scoreOutLocal_ 与 tmpIndexLocal 重叠！
    //         → 步骤2 覆盖了步骤1 的索引数据！
} else {
    LiTopKVF<false>(...);  // 不写 hisValueLocal，tmpIndexLocal 保持正确 → return_value=0 时 pass
}
Cast(indicesOutLocal, tmpIndexLocal, ...);
//  读到的是分数值(uint16 sortable key)，Cast 成 int32 后变成 0~65535 的乱数 → 越界
```

### 修复

在 `scoreOutOffsetInU32` 计算中加上 `tmpIndexLocal` 的大小，使 `scoreOutLocal_` 跳过整个 topkSharedTmpLocal_ 区域：

```cpp
// 修复前:
uint32_t scoreOutOffsetInU32 = topkCountAlign256_ + 3 * 256 + 64;

// 修复后:
uint32_t tmpIndexLocalSizeInU32 = (topkCountAlign256_ + trunkLen_) / 2;
uint32_t scoreOutOffsetInU32 = topkCountAlign256_ + 3 * 256 + 64 + tmpIndexLocalSizeInU32;
```

### 空间验证

所有用例修复后 `resMm1Buf_`(256KB) 都足够（最大需求仅 52KB）。

### 检查要点

- **UB 复用时必须验证所有子 buffer 的地址范围不重叠**
- `resMm1UB_` 被多个 LocalTensor 复用（ReinterpretCast），每个的偏移和大小都要逐一核算
- 特别关注 `topkSharedTmpLocal_` 内部的 `tmpIndexLocal`——它以 `uint16_t` 重解释，大小为 `(Align(topK,256) + trunkLen)` 个 uint16，换算成 uint32 偏移需 `/2`
- bug 隐藏在 `return_value=0` 时不触发，因为 `LiTopKVF<false>` 不写 `hisValueLocal`

### 节省效果（gSize=64）

| | 修改前 | 修改后 | 节省 |
|---|---|---|---|
| AIV UB (MXFP8) | 226.5 KB | 177 KB | **49.25 KB** |
| AIV UB (非MXFP8 float) | 244.5 KB | 195 KB | **49.25 KB** |
| 256KB 剩余 (MXFP8) | 29.5 KB | 78.75 KB | — |

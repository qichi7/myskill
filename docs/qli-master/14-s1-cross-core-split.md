# 14 - S1 跨核拆分（少核场景并行加速）

> 本模块记录当 V2 Metadata 启用逻辑核数 K 远小于物理核数 N 时，在 **metadata 不变** 的前提下，
> 于 kernel 侧招募闲置物理核作"合伙人"、把每个逻辑基本块的 `s1BaseSize` 行沿 S1 维度拆给 F 个物理核并行算的优化方法。
> 实施时间：2026-07-21，仅 arch35（950，CVRATIO=1，独立核），未移植 arch22。

---

## 14.1 背景与动机

### 问题
V2 Metadata 算子（AICPU）按三级递进策略把任务分给 K 个逻辑核（core 0..K-1 enabled，连续）。
当 batch/seq 都很小时，K 可能远小于物理核数 N（950 上 N=64）：
- 例：B=1, S1=4, S2=128, G=64 → 仅 1 个逻辑核 enabled，其余 63 个物理核闲置。
- 这些场景下算子时延被单核串行处理 s1BaseSize=4 行的 MM1+Vec1+TopK 主导，无法利用多核。

### 思路
**不动 metadata**（AICPU 负载均衡逻辑保持不变，回归风险低），仅在 kernel 侧增加一层"物理→逻辑"映射：
- 招募闲置物理核作为"合伙人"，与主核共享同一 `[bN2, gS1, s2]` 区间。
- 把主核本应串行算的 s1BaseSize=4 行沿 S1 拆给 F 个物理核并行算。
- 合伙人间无 CV 同步耦合（CV 同步是核内 1:1 配对），天然并行。

### 为什么是拆 S1 而非 S2
- S2 拆分会引入 FlashDecode 归约（FD），metadata 已有完整 FD 路径，改它回归风险高。
- S1 拆分后每核独立做自己的 s1 行 TopK → 直接写输出绝对行，无归约、无新增同步。
- s1BaseSize=4 恰好可被 2 和 4 整除，天然支持 F=2/F=4。

---

## 14.2 核心设计

### 14.2.1 splitFactor 选择规则

| 条件 | F (splitFactor) | 每核 s1 行数 (s1BaseShare) | 合伙人物理核偏移 | mGranule (M对齐粒度) |
|------|------|------|------|------|
| `K > 0 且 N%4==0 且 K*4 ≤ N` | 4 | 1 | N/4, N/2, 3N/4 | `gSize` (64) |
| `K > 0 且 N%2==0 且 K*2 ≤ N` | 2 | 2 | N/2 | `2*gSize` (128) |
| 其它 | 1 | 4（不变） | — | `2*gSize` (128) |

> F=1 时所有行为与原逻辑字节级等价（mGranule=2*gSize、mProcSize=整块 M），**零回归**。

### 14.2.2 物理→逻辑核映射

```
物理核 P (0..N-1)
  ├── logicalCoreIdx = P % (N/F)    ← 对应的 metadata 逻辑核
  ├── splitIdx       = P / (N/F)    ← 在拆分组内的索引 [0, F)
  └── 激活条件: logicalCoreIdx < K  (isPartnerActive)
```

**例**（N=64, F=2）：物理核 P=0..31 → logicalCoreIdx=P, splitIdx=0；P=32..63 → logicalCoreIdx=P-32, splitIdx=1。
即用户要的"另一半核给当前核+32的核去算"。

**例**（N=64, F=4）：合伙人偏移 +16/+32/+48，即用户要的"不满1/4核时拆到4个核"。

### 14.2.3 每基本块 s1 行切片（CalcS1Split）

对每个 gS1 基本块，合伙人只算自己那一份 s1 行：

```
s1BlockStart  = gS1Idx * s1BaseSize           // 逻辑块起始绝对 s1 行
s1SplitOffset = splitIdx * s1BaseShare         // 本合伙人在块内偏移
blockRows     = min(s1Base, actS1Size - s1BlockStart)  // 本块有效行数(尾块可能<4)
s1ProcNum     = min(s1BaseShare, blockRows - s1SplitOffset)  // 本合伙人实际行数(可能0)
mProcSize     = s1ProcNum * gSize              // 本合伙人 M
s1PartnerStart= s1BlockStart + s1SplitOffset   // 本合伙人绝对 s1 起点
```

- 尾块（actS1Size 不是 s1BaseSize 整数倍）时，靠后的合伙人可能分到 0 行 → 走"仅握手跳过"路径。
- actMBaseSize 被覆盖为 mProcSize（原为整块 M），cube/vec1 据此限制 M 方向处理量。

---

## 14.3 关键正确性不变量

### 14.3.1 CV 同步是核内 1:1 配对（合伙人互不干扰）
- 每个合伙人物理核自成一对（1 AIC : 1 AIV，CVRATIO=1）。
- `CROSS_CV_EVENT` / `CROSS_VC_EVENT` 握手在配对内部，用 `loop % 2` ping-pong。
- 合伙人 A 与合伙人 B 之间无任何同步耦合 → 天然并行。

### 14.3.2 零行合伙人的握手对称性（防死锁）
当 `mProcSize==0`（s1ProcNum==0）时，AIC 与 AIV 都走"仅握手、跳过计算"路径：

| 核 | wait | set | 计算 |
|----|------|-----|------|
| AIC (ComputeMm1) | `VC_EVENT + loop%2` | `CV_EVENT + loop%2` | 跳过 |
| AIV (ProcessVec1) | `CV_EVENT + loop%2` | `VC_EVENT + loop%2` | 跳过 |
| AIV (ProcessTopK) | — | — | 直接 return（MTE3_MTE2 已平衡） |

**关键**：ping-pong 语义要求每个 loop 两侧都 set 一次，否则下一轮 wait 永久阻塞。AIC 和 AIV 的跳过路径都对称地完成了 set，故无死锁。

### 14.3.3 qkVLstride == Fixpipe dstNdStride（UB 布局不变量）
据 `07-mxfp8-bugfix.md` §7，qkVLstride 必须等于 Fixpipe 的 dstNdStride。
- cube 端 `CeilAlign(s1gL1RealSize, mGranule)` 决定 L0C→UB 的行距。
- vector 端 `Align(actMBaseSize, mGranule) * (UB_BANK_DEPTH_STRIDE/sizeof)` 必须与之匹配。
- 本方案把两端的 `2*gSize` 统一替换为 `mGranule`，且 `actMBaseSize = mProcSize`，故不变量保持。

### 14.3.4 score workspace 按物理核分槽（无冲突）
- `scoreGm[aiCoreIdx * singleCoreScoreSize]`：每个物理核独立槽位。
- 合伙人写自己的 s1 行到自己的槽 → 无写冲突。
- vec1OutGmOffset 用 `vecOffset`（本核槽内局部行）= i（CVRATIO=1）。

### 14.3.5 TopK 按 s1 行独立（无归约）
- 每核对自己 s1 行做 TopK，直接写到输出绝对行：
  - `indiceOutGm[indiceOutOffset + (s1BlockStart + rowIdx) * topkCount_]`
  - `rowIdx = s1SplitOffset + i`（块内绝对行）
- 输出布局：`[B, S1, N2, K]`，每行独立 → 合伙人写不同行，无冲突。

### 14.3.6 LD（FlashDecode）自动汇合
- 合伙人按块内绝对 rowIdx 写共享 ldScoreGm/ldIndexGm：
  `offset = saveWorkSpaceIdx * s1BaseSize * topkCountAlign16 + rowIdx * topkCountAlign16`
- LD 归约核按绝对 `mStart..mStart+mNum` 读 → 自动汇合所有合伙人贡献。
- `SyncAll()`（ProcessDecode 入口）保证合伙人写完才进 LD。

### 14.3.7 批级 GM 写仅主核执行（防并发冲突）
以下写共享 GM 输出的操作仅 `splitIdx==0` 执行：
- `DealActSeqLenIsZero`（actS1==0 或 BSND 尾部清理）
- `DoTndPadding`（TND padding 清理）

原因：这些操作写整个 batch 的输出区域，合伙人并发写会冲突。主核已覆盖全部需清理的行。

---

## 14.4 代码修改清单（arch35）

### 14.4.1 `quant_lightning_indexer_v2_common.h`

**ConstInfo 新增字段**（S1 跨核拆分配置）：

| 字段 | 类型 | 含义 |
|------|------|------|
| `splitFactor` | uint32_t | F (1/2/4) |
| `splitIdx` | uint32_t | 本物理核在拆分组内的索引 [0, F) |
| `logicalCoreIdx` | uint32_t | 本物理核对应的逻辑(metadata)核索引 |
| `s1BaseShare` | uint32_t | 每核 s1 行数 = s1BaseSize / F |
| `mGranule` | uint64_t | M 方向对齐粒度: F>=4 用 gSize, 否则 2*gSize |
| `totalCoreNum` | uint32_t | 物理核总数 N = GetBlockNum() |
| `usedLogicalCoreNum` | uint32_t | metadata 启用的逻辑核数 K |
| `isPartnerActive` | bool | 本物理核是否激活(logicalCoreIdx < K) |

**RunInfo 新增字段**（每基本块级别，由 CalcS1Split 填充）：

| 字段 | 含义 |
|------|------|
| `s1BlockStart` | 逻辑块起始 s1 绝对行 = gS1Idx * s1BaseSize |
| `s1SplitOffset` | 本合伙人在块内 s1 行偏移 = splitIdx * s1BaseShare |
| `s1ProcNum` | 本合伙人本次 s1 行数(可能 0) |
| `mProcSize` | 本合伙人本次 M = s1ProcNum * gSize |
| `s1PartnerStart` | 本合伙人绝对 s1 起点 = s1BlockStart + s1SplitOffset |

### 14.4.2 `quant_lightning_indexer_v2_kernel.h`

**新增函数**：

| 函数 | 位置 | 作用 |
|------|------|------|
| `InitS1Split(metadataGm)` | Init 中，SplitCoreByAICPU 前 | 扫描 metadata 统计 K，选 F，建物理→逻辑映射 |
| `CalcS1Split(gS1Idx, actS1Size, runInfo)` | CalcRunInfo 中 | 计算本合伙人在当前 gS1 块的 s1 行切片 |

**修改点**：

| 修改点 | 行号(改后) | 说明 |
|--------|---------|------|
| `Init` 调用顺序 | Init | `InitS1Split` → `SplitCoreByAICPU(logicalCoreIdx, ...)` |
| `CalcRunInfo` 注入拆分 | CalcRunInfo | `CalcS1Split` + `actMBaseSize = mProcSize` |
| query offset | CalcRunInfo | `s1PartnerStart * gSize * headDim`（原 `gS1Idx * mBaseSize * headDim`） |
| qScale offset (MXFP8) | CalcRunInfo | `s1PartnerStart * gSize * (headDim/32)` |
| DealActSeqLenIsZero ×2 | ProcessMain | 仅 `splitIdx==0` 执行 |
| DoTndPadding | CalcRunInfo | 仅 `splitIdx==0` 执行 |

### 14.4.3 `quant_lightning_indexer_v2_service_cube.h`

| 修改点 | 说明 |
|--------|------|
| `ComputeMm1` 入口 | `mProcSize==0` 时仅 CV 握手返回（wait VC + set CV），防死锁 |
| `s1gProcessSize` | 已为 mProcSize（actMBaseSize 被覆盖） |
| `CeilAlign(s1gL1RealSize, 2*gSize)` → `mGranule` | 2 处（s2BasicBlock==128 和 ==256 分支） |
| `QueryNd2Nz` dstNzC0Stride | `CeilAlign(s1gL1RealSize, mGranule)` |

### 14.4.4 `quant_lightning_indexer_v2_service_vector.h`

| 修改点 | 函数 | 说明 |
|--------|------|------|
| curS1Idx/curAivS1Idx/curS1ProcNum | ProcessVec1 | 取自 `info.s1BlockStart`/`s1PartnerStart`/`s1ProcNum` |
| qkVLstride | ProcessVec1 | `Align(actMBaseSize, mGranule) * ...`（原 `2*gSize`） |
| curS1Idx/curAivS1Idx/curS1ProcNum | ProcessTopK | 同 ProcessVec1 |
| s1ProcNum==0 跳过 | ProcessTopK | 直接 return（MTE3_MTE2 已平衡） |
| rowIdx | ProcessTopK | `s1SplitOffset + i`（块内绝对行，原 `blockId%CVRATIO * ...`） |
| vecOffset | ProcessTopK | `i`（本核槽内局部行，CVRATIO=1） |

---

## 14.5 F=4 的关键风险点：mGranule 从 2*gSize 降到 gSize

### 14.5.1 为什么 F=4 需要降粒度
- s1BaseSize=4, F=4 → 每核 1 s1 行 → M = 1*gSize = 64。
- 原 `CeilAlign(M, 2*gSize=128)` 会把 64 对齐到 128，过读 1 行（query GM 有 padding，不越界，但浪费）。
- 降到 `gSize=64` 后精确对齐，无过读。

### 14.5.2 为什么 gSize 粒度对单行已验证可行
- 现有 F=1 尾块 `actS1=1` 已走 M=64 路径：
  - `BatchMulWeightAndReduceSum_MxFP8` 奇数尾支调用 `MulWeightAndReduceSum_MxFP8`（单行 reduce）。
  - `MulWeightAndReduceSum2_MxFP8` 内层循环 `128 * i` 步进，i 以 2 为步长，奇数尾单独处理。
- 故 gSize 粒度对单行 reduce 已在尾块路径验证，F=4 只是让满块也走这条路。

### 14.5.3 风险
- `MulWeightAndReduceSum2_MxFP8` 的 `128 * i` 硬编码步进假设 qkVLstride=128 float（512B）。
- F=4 时 qkVLstride = `Align(64, 64) * 128 = 8192 float` = 128 行 × 64 float/行... 需上板验证。
- 若验证失败，可回退 F=4 仅用 F=2（mGranule 不变，零风险）。

---

## 14.6 验证要点

1. **F=1 不回归**：K > N/2 时所有场景与原逻辑等价，重点跑全量 ST。
2. **F=2 精度**：K ∈ (N/4, N/2]，每核 2 s1 行，mGranule 不变，风险最低。
3. **F=4 精度**：K ≤ N/4，每核 1 s1 行，mGranule=gSize，**重点验证 MXFP8 路径**（qkVLstride 耦合）。
4. **LD 路径**：含 cmpRatio + causal mask 的 case，验证合伙人 LD workspace 汇合正确。
5. **TND padding**：TND + sequsedQ < cuSeqlensQ 的 case，验证主核 padding 不被合伙人破坏。
6. **零行合伙人**：尾块（actS1 不是 4 整数倍）时靠后合伙人分 0 行，验证无死锁。
7. **性能**：对比 F=1 vs F=2/F=4 在少核场景的时延，确认加速比。

---

## 14.7 局限性与未来工作

### 14.7.1 当前局限
- **仅 arch35**：未移植 arch22（910B, CVRATIO=2）。arch22 的 CVRATIO=2 下物理→逻辑映射需考虑 AIC:AIV=1:2 配比。
- **F 上限为 4**：受 s1BaseSize=4 限制。若 s1BaseSize=3（大 topk 场景），F 只能是 1 或 3（3 不在当前实现中）。
- **K 统计假设连续**：`InitS1Split` 假设 core 0..K-1 连续 enabled。若 metadata 出现非连续 enabled（当前 AICPU 不会），需改扫描逻辑。

### 14.7.2 未来扩展
- **F=3 支持**：当 s1BaseSize=3 且 K ≤ N/3 时，每核 1 行。需 `N%3==0` 且 mGranule=gSize。
- **动态 F**：当前 F 在 kernel init 时按 K/N 比例静态选择。可考虑 per-batch 动态（不同 batch 的 K 不同），但需 per-batch metadata 扩展。
- **S2 方向拆分**：若 s1BaseSize 拆分仍不够，可进一步拆 S2，但会引入额外 FD 归约，复杂度高。
- **移植 arch22**：需处理 CVRATIO=2 下的合伙人 AIC:AIV 配比（1 个 AIC 合伙人需 2 个 AIV 合伙人）。

---

## 14.8 与其他模块的关系

| 模块 | 关系 |
|------|------|
| [08 V2 Metadata](08-v2-metadata.md) | **metadata 不变**，本方案纯 kernel 侧增强，不修改 AICPU 负载均衡 |
| [06 CV 比例适配](06-cv-ratio-adapt.md) | CVRATIO=1 是本方案前提（1 AIC:1 AIV 配对）；CVRATIO=2 需额外处理 |
| [07 MXFP8 优化](07-mxfp8-bugfix.md) | §7 qkVLstride==dstNdStride 不变量是本方案 F=4 的核心风险点 |
| [10 同步陷阱](10-sync-pitfalls.md) | §14.3.2 零行合伙人握手对称性是防死锁关键，属同类同步陷阱 |
| [09 Tiling 与性能](09-tiling-perf.md) | 本方案不改 Tiling，属 kernel 侧运行时优化 |

---

## 14.9 核心经验总结

1. **metadata 不变是关键降险手段**：AICPU 负载均衡逻辑复杂且回归风险高，纯 kernel 侧增强可隔离风险。
2. **拆 S1 优于拆 S2**：S1 拆分无归约、无新增同步；S2 拆分引入 FD 归约复杂度。
3. **CV 同步是核内配对**：合伙人互不干扰是本方案成立的根本前提，CVRATIO=1 下天然满足。
4. **零行握手必须对称**：AIC 和 AIV 的跳过路径都必须完成 set，否则 ping-pong 死锁。
5. **mGranule 是 F=4 的核心风险**：从 2*gSize 降到 gSize 影响 qkVLstride/Fixpipe 耦合，需重点验证。
6. **批级 GM 写需主核独占**：合伙人并发写共享输出区会冲突，用 splitIdx==0 守卫。
7. **F=1 零回归是安全网**：K > N/2 时所有场景与原逻辑等价，即使 F=2/F=4 有问题也不影响常见场景。

# QLI V2 Metadata 算子详解

## 概述

QLI V2 引入了独立的 **AICPU Metadata 算子**（`aclnnQuantLightningIndexerV2Metadata`），作为主算子 `aclnnQuantLightningIndexerV2` 的**前置算子**。

### 核心设计理念

| 对比项 | V1 | V2 |
|--------|----|----|
| 负载均衡时机 | Tiling 阶段（编译期/Host 侧） | **运行时 AICPU 算子** |
| 负载均衡输出 | TilingData 中的静态参数 | **独立 Metadata Tensor**（1024 int32） |
| 动态 shape 支持 | 需重新 Tiling | **运行时自适应** |
| 分核粒度 | 简单均分 | **三级递进（Batch→Row→Block）** |
| FD（FlashDecode）归约 | 无 | **独立 AIV 核负载均衡** |

### 工作流

```
用户 → aclnnQuantLightningIndexerV2Metadata（AICPU）→ metadata[1024]
                                                         ↓
用户 → aclnnQuantLightningIndexerV2（NPU Kernel）← metadata 作为输入
```

## Metadata 数据结构

### 总体布局

```cpp
// 固定大小: 1024 个 int32_t = 4096 bytes
struct QliV2Metadata {
    uint32_t qliV2Metadata[36][8];   // AIC 核 metadata: 36核 × 8字段
    uint32_t qldV2Metadata[72][8];   // AIV 核 metadata: 72核 × 8字段
};
// 36×8 + 72×8 = 288 + 576 = 864 uint32_t < 1024 (有预留)
```

### AIC (FA) Metadata 字段

| 索引 | 常量名 | 含义 |
|------|--------|------|
| 0 | `QLI_V2_CORE_ENABLE_INDEX` | 核是否启用 (0/1) |
| 1 | `QLI_V2_BN2_START_INDEX` | BN2 起始索引（双闭区间） |
| 2 | `QLI_V2_M_START_INDEX` | M(S1G) 起始索引 |
| 3 | `QLI_V2_S2_START_INDEX` | S2 起始索引 |
| 4 | `QLI_V2_BN2_END_INDEX` | BN2 结束索引 |
| 5 | `QLI_V2_M_END_INDEX` | M(S1G) 结束索引 |
| 6 | `QLI_V2_S2_END_INDEX` | S2 结束索引 |
| 7 | `QLI_V2_FIRST_QLD_V2_DATA_WORKSPACE_IDX_INDEX` | 该核首个 FD workspace 索引 |

### AIV (FD/LD) Metadata 字段

| 索引 | 常量名 | 含义 |
|------|--------|------|
| 0 | `QLD_V2_CORE_ENABLE_INDEX` | 核是否启用 (0/1) |
| 1 | `QLD_V2_BN2_IDX_INDEX` | 归约任务的 BN2 索引 |
| 2 | `QLD_V2_M_IDX_INDEX` | 归约任务的 M(S1G) 索引 |
| 3 | `QLD_V2_WORKSPACE_IDX_INDEX` | workspace 中的存放位置 |
| 4 | `QLD_V2_WORKSPACE_NUM_INDEX` | S2 核间切分份数 |
| 5 | `QLD_V2_M_START_INDEX` | 子任务 M 轴起点 |
| 6 | `QLD_V2_M_NUM_INDEX` | 子任务 M 轴行数 |

### 绝对索引计算

```cpp
// AIC: absIndex = 8 * coreIdx + metaIdx
// AIV: absIndex = 8 * 36 + 8 * coreIdx + metaIdx = 288 + 8*coreIdx + metaIdx
uint32_t GetAttrAbsIndex(uint32_t coreIdx, uint32_t metaIdx, bool isAIV = false);
```

## API 接口

### 函数原型（两段式）

```cpp
// 第一段：获取 workspace 大小 + 参数校验 + 创建 executor
aclnnStatus aclnnQuantLightningIndexerV2MetadataGetWorkspaceSize(
    const aclTensor *cuSeqlensQOptional,     // [B+1] INT32, TND 必传
    const aclTensor *cuSeqlensKOptional,     // [B+1] INT32, TND 必传
    const aclTensor *sequsedQOptional,       // [B] INT32, 可选
    const aclTensor *sequsedKOptional,       // [B] INT32, 可选
    const aclTensor *cmpResidualKOptional,   // [B] INT32, cmpRatio!=1 且 causal 时必传
    int64_t numHeadsQ,                       // [1, 64]
    int64_t numHeadsK,                       // 固定=1
    int64_t headDim,                         // 固定=128
    int64_t topk,                            // A5:[1,8192], A2/A3:[1,2048]
    int64_t quantMode,                       // 1/2/4
    int64_t batchSize,                       // BSND 场景使用
    int64_t maxSeqlenQ,                      // BSND 场景使用
    int64_t maxSeqlenK,                      // BSND 场景使用
    char *layoutQOptional,                   // "BSND" / "TND"
    char *layoutKOptional,                   // "BSND" / "TND" / "PA_BBND"
    int64_t maskMode,                        // 0: no mask, 3: rightDownCausal
    int64_t cmpRatio,                        // [1, 128], key 压缩率
    const aclTensor *metadata,               // [1024] INT32 输出
    uint64_t *workspaceSize,                 // 输出: workspace=0
    aclOpExecutor **executor);

// 第二段：执行计算（AICPU 上运行）
aclnnStatus aclnnQuantLightningIndexerV2Metadata(
    void *workspace, uint64_t workspaceSize,
    aclOpExecutor *executor, aclrtStream stream);
```

### 参数约束

| 参数 | Ascend950 (A5) | Ascend910B (A2/A3) |
|------|----------------|---------------------|
| numHeadsQ | [1, 64] | 固定=64 |
| topk | [1, 8192] | [1, 2048] |
| quantMode | 1 / 2 / 4 | 固定=2 |
| cmpRatio | [1, 128] 任意整数 | 2的幂次: 1/2/4/8/16/32/64/128 |
| layoutK | BSND / TND / PA_BBND | 仅 PA_BBND |

### Batch 取值优先级

```
1. sequsedQOptional 存在 → batchSize = sequsedQ.shape[0]
2. layoutQ=="TND" 且 cuSeqlensQOptional 存在 → batchSize = cuSeqlensQ.shape[0] - 1
3. 否则 → batchSize = batchSize 参数
```

### Sequence Length 取值优先级

```
1. sequsedQ/K 存在 → 使用 sequsedQ/K[bIdx]
2. layoutQ/K=="TND" → cuSeqlensQ/K[bIdx+1] - cuSeqlensQ/K[bIdx]
3. 否则 → maxSeqlenQ/K
```

## 负载均衡算法

### 三级递进分配策略

```
对每个 AIC 核:
  costLimit = unassignedCost / (剩余核数)
  如果 !supportFd_: costLimit = max(costLimit, maxS1GCost)

  1. AssignByBatch: 按整 batch 分配
     - 条件: costLimit + tolerance >= curCost + bN2Cost
     - tolerance = bN2LastBlockCost / FA_TOLERANCE_RATIO(=2)

  2. AssignByRow: 按 S1G 行分配
     - 条件: costLimit + tolerance >= curCost + s1GCost
     - tolerance = s1GLastBlockCost / FA_TOLERANCE_RATIO(=2)

  3. AssignByBlock: 按单个块分配（仅 supportFd_）
     - 条件: costLimit + tolerance >= curCost + blockCost

  4. ForceAssign: 强制分配至少1块（仅 supportFd_，且当前核0块时）
```

### 开销模型

```cpp
// 基本块开销计算
int64_t CalcCost(uint32_t basicM, uint32_t basicS2) {
    uint32_t alignM = (basicM + 15) >> 4;     // 按16对齐
    uint32_t alignS2 = (basicS2 + 63) >> 6;   // 按64对齐
    return COST_WEIGHT_M * alignM + COST_WEIGHT_S2 * alignS2;
    // COST_WEIGHT_M = 6, COST_WEIGHT_S2 = 10
}
```

### 块类型开销表

```cpp
BlockCost = [NORMAL/TAIL][NORMAL/TAIL] 的 2x2 矩阵:
  [NORMAL][NORMAL] = CalcCost(mBaseSize, s2BaseSize)
  [TAIL][NORMAL]   = CalcCost(s1GTailSize, s2BaseSize)  // s1G尾块
  [NORMAL][TAIL]   = CalcCost(mBaseSize, s2TailSize)    // s2尾块
  [TAIL][TAIL]     = CalcCost(s1GTailSize, s2TailSize)  // 双尾块
```

### Causal Mask 下的 S2 有效范围

```cpp
// 计算 S1G 行对应的 S2 token 范围
Range<int64_t> CalcS2TokenRange(uint32_t s1GIdx, const BatchCache &batchCache) {
    // no mask: [0, revertS2Size)
    // rightDownCausal:
    //   s1FirstToken = s1GFirstToken / groupSize
    //   s1LastToken = s1GLastToken / groupSize
    //   s2FirstToken = s1FirstToken - preTokenLeftUp
    //   s2LastToken = s1LastToken + nextTokenLeftUp
    //   其中 nextTokenLeftUp = s2Size - s1Size (rightDownCausal)
}
```

### FD（FlashDecode）负载均衡

当 S2 方向被跨核切分时，产生归约任务（FD），由 AIV 核处理：

```cpp
void SplitFD(SplitResult &splitRes) {
    // 1. 计算总 FD 负载
    totalFDLoad = Σ(fdS2SplitNum[i] * fdMSize[i])

    // 2. 平均分配到 AIV 核
    averageLoad = ceil(totalFDLoad / aivCoreNum)

    // 3. 每个归约任务分配若干 AIV 核
    for each FD task:
        curFDVectorNum = max(1, taskLoad / averageLoad)
        curAveMSize = ceil(fdMSize / curFDVectorNum)
        // 每个 AIV 核处理 [fdMStart, fdMStart+fdMNum) 行
}
```

### FD 归约触发条件

```cpp
bool IsNeedRecordFDInfo(assignContext, splitRes) {
    // 1. 核0 无需归约
    // 2. curKvSplitPart <= 1 无跨核行
    // 3. 当前 BN2/S1G 与上一核切分点相同 → 还未处理完
    // 4. 其余情况 → 需要记录归约信息
}
```

## 基本块参数

### Metadata 算子内部参数

```cpp
// ParamsInit() 根据 SoC 版本设置
if (Ascend910B) {
    mBaseSize_ = s1BaseSize_ * groupSize_;  // = 4 * G
    s2BaseSize_ = 2048;
} else if (Ascend950) {
    mBaseSize_ = s1BaseSize_ * groupSize_;  // = 4 * G
    s2BaseSize_ = 128;
}
// s1BaseSize_ 固定为 4
```

### 切分信息计算

```cpp
// 每个 batch 的切分
s1GBaseNum = ceil(s1Size * groupSize / mBaseSize)
s1GTailSize = (s1Size * groupSize) % mBaseSize
s2BaseNum = ceil(s2Size / s2BaseSize)
s2TailSize = s2Size % s2BaseSize
```

### revertS2Size（压缩还原）

```cpp
// cmpRatio 表示 key 的压缩率
revertS2Size = s2Size * cmpRatio + cmpResidualK[bIdx]
// cmpResidualK 仅在 cmpRatio!=1 且 causal mask 时传入
```

## V2 Kernel 消费 Metadata

### Kernel 侧 SplitCoreInfo

```cpp
struct SplitCoreInfo {
    uint32_t s2Start;      // S2 循环起始
    uint32_t s2End;        // S2 循环上限
    uint32_t bN2Start;     // BN2 起始
    uint32_t bN2End;       // BN2 结束
    uint32_t gS1Start;     // S1G 起始
    uint32_t gS1End;       // S1G 结束
    bool isLD;             // 是否参与 FD 归约
    bool isCoreEnable;     // 核是否启用
};
```

### Kernel 侧 LdSplitCoreInfo

```cpp
struct LdSplitCoreInfo {
    bool isLdCoreEnable;       // AIV 核是否参与归约
    uint32_t saveWorkSpaceIdx; // workspace 存放位置
    uint32_t bn2Idx;           // 归约任务 BN2 索引
    uint32_t mIdx;             // 归约任务 M 索引
    uint32_t workspaceIdx;     // 当前 AIV 核上归约任务索引
    uint32_t workspaceNum;     // S2 切分数量
    uint32_t mStart;           // 子任务 M 轴起点
    uint32_t mNum;             // 子任务 M 轴行数
};
```

## V2 Kernel Tiling

### Workspace 计算

```cpp
// arch35 (Ascend950):
workspaceSize = libApiWorkSpaceSize
    + s1Base * ceil(s2Size/s2Base) * s2Base * sizeof(uint16_t) * aicNum  // 主流程
    + 2 * s1Base * 2 * 2048 * 4 * aicNum     // Decode 中间结果
    + 2 * s1Base * 16 * 8 * aicNum;           // Decode 中间参数

// arch22 (Ascend910B):
workspaceSize = libApiWorkSpaceSize
    + mBaseSize * s2BaseSize * 4 * 2 * aicNum  // MM1 结果双缓冲
    + 2 * s1Base * 2 * 2048 * 4 * aicNum       // Decode 中间结果
    + 2 * s1Base * 16 * 8 * aicNum;             // Decode 中间参数
```

### TilingData 字段

```cpp
tilingData_.set_bSize(bSize);
tilingData_.set_s2Size(s2Size);
tilingData_.set_s1Size(s1Size);
tilingData_.set_sparseCount(topk);
tilingData_.set_gSize(gSize);
tilingData_.set_blockSize(blockSize);
tilingData_.set_maxBlockNumPerBatch(maxBlockNumPerBatch);
tilingData_.set_sparseMode(maskMode);
tilingData_.set_cmpRatio(cmpRatio);
tilingData_.set_returnValue(returnValue);
tilingData_.set_maxSeqlenQ(maxSeqlenQ);
tilingData_.set_keyStride0(keyStride0);
tilingData_.set_keyDequantScaleStride0(keyDequantScaleStride0);
tilingData_.set_quantMode(quantMode);
tilingData_.set_usedCoreNum(blockDim);
```

## V2 与 V1 的关键差异总结

| 维度 | V1 | V2 |
|------|----|----|
| **负载均衡** | Tiling 阶段静态分配 | AICPU 运行时动态分配 |
| **Metadata** | 无独立输出 | 1024 int32 Tensor |
| **FD 归约** | 无 | AIV 核独立负载均衡 |
| **quantMode** | 2 (per-token-head) | 1/2/4 (per-head/per-token-head/group-scaling) |
| **cmpRatio** | 无压缩 | [1,128]，支持 key 压缩 |
| **topk 上限** | 2048 | A5: 8192, A2/A3: 2048 |
| **layoutK** | BSND/TND/PA_BSND | BSND/TND/PA_BBND |
| **核配比** | 1:2 (CVRATIO=2) | 支持 1:1 (CVRATIO=1) |
| **分核算法** | 简单均分 + 余数分配 | 三级递进 + 容忍度 + 强制分配 |
| **开销模型** | 块数均分 | `6*align16(M) + 10*align64(S2)` 加权 |
| **S2 有效范围** | 静态 causal mask | 动态 revertS2Size + cmpRatio |

## V2 Metadata 代码位置速查

| 功能 | 文件路径 | 关键函数/结构 |
|------|---------|---------------|
| API 入口 | op_host/op_api/aclnn_*.cpp | `aclnnQuantLightningIndexerV2MetadataGetWorkspaceSize` |
| L0 OP 注册 | op_host/op_api/quant_lightning_indexer_v2_metadata.cpp | `ADD_TO_LAUNCHER_LIST_AICPU` |
| 参数校验 | op_host/quant_lightning_indexer_v2_metadata_check.h | `ParamsCheckQliV2` |
| AICPU 核心 | op_kernel_aicpu/quant_lightning_indexer_v2_metadata_aicpu.cpp | `BalanceSchedule`, `GenMetadata` |
| Metadata 结构 | quant_lightning_indexer_v2/op_kernel/quant_lightning_indexer_v2_metadata.h | `QliV2Metadata` |
| V2 Kernel Tiling | quant_lightning_indexer_v2/op_host/quant_lightning_indexer_v2_tiling.cpp | `QuantLightningIndexerV2Tiling` |
| V2 Kernel Common | quant_lightning_indexer_v2/op_kernel/arch35/quant_lightning_indexer_v2_common.h | `SplitCoreInfo`, `LdSplitCoreInfo` |

## V2 Metadata 内部数据结构

```cpp
// 分核结果
struct SplitResult {
    uint32_t usedCoreNum;                    // 实际使用核数
    vector<uint32_t> bN2End;                 // 每核 BN2 结束点
    vector<uint32_t> gS1End;                 // 每核 S1G 结束点
    vector<uint32_t> s2End;                  // 每核 S2 结束点
    vector<uint32_t> firstFdDataWorkspaceIdx;// 每核首个 FD workspace 索引
    int64_t maxCost;                         // 慢核开销
    uint32_t numOfFdHead;                    // 归约任务数量
    uint32_t maxS2SplitNum;                  // 单归约任务最大分核数
    FlashDecodeResult fdRes;                 // FD 信息
};

// FD 归约结果
struct FlashDecodeResult {
    uint32_t fdUsedVecNum;                   // 使用的 AIV 核数
    vector<uint32_t> fdBN2Idx;               // 归约任务 BN2 索引
    vector<uint32_t> fdMIdx;                 // 归约任务 M 索引
    vector<uint32_t> fdWorkspaceIdx;          // workspace 位置
    vector<uint32_t> fdS2SplitNum;            // S2 切分份数
    vector<uint32_t> fdMSize;                // M 轴大小
    vector<uint32_t> fdIdx;                  // AIV 核→归约任务映射
    vector<uint32_t> fdMStart;               // AIV 核 M 轴起点
    vector<uint32_t> fdMNum;                 // AIV 核 M 轴行数
};

// 分配上下文
struct AssignContext {
    uint32_t curBIdx, curBN2Idx, curS1GIdx, curS2Idx, curCoreIdx;
    int64_t unassignedCost;
    uint32_t curKvSplitPart;                 // 当前 S2 跨核切分份数
    uint32_t preFdDataNum;                   // 已记录的 FD 数据量
    BatchCache batchCache;                   // 当前 batch 缓存
    S1GCache s1GCache;                       // 当前 S1G 行缓存
    CoreCache coreCache;                     // 当前核负载
};
```

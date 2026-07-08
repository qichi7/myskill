# QLI Master - 知识索引

> Quantized Lightning Indexer 算子完整知识库入口。
> 各模块详细内容已拆分至 `../docs/qli-master/` 目录，按需索引。

---

## 模块索引

| # | 模块 | 文件 | 内容概要 |
|---|------|------|----------|
| 01 | [核心概念](../docs/qli-master/01-core-concepts.md) | `01-core-concepts.md` | 核心公式、关键维度(B/S1/S2/N1/N2/G/D/K)、三大布局(BSND/TND/PA_BSND)、切块策略(S1_BASE/S2_BASE/M_BASE)、负载均衡基本算法 |
| 02 | [流水线与核间同步](../docs/qli-master/02-pipeline-sync.md) | `02-pipeline-sync.md` | 异构协同流水线(V0→C1→V1→LD)、核配比(1:2/独立核)、核间同步(FIA_SYNC_MODE2/QLI_SYNC_MODE4)、Event ID管理、Offset预计算、架构适配(arch22/arch35) |
| 03 | [Buffer 管理](../docs/qli-master/03-buffer-management.md) | `03-buffer-management.md` | Double/Triple Buffer、Bank冲突避免(UB_BANK_STRIDE=256B)、arch35 UB使用详解(AIC/AIV核)、s1BaseSize减半影响分析、UB复用策略(resMm1UB_复用给TopK)、**UB复用重叠Bug(scoreOutLocal_与tmpIndexLocal)**、TBuf管理安全 |
| 04 | [计算算法](../docs/qli-master/04-compute-algo.md) | `04-compute-algo.md` | 量化流程(存8算8策略、Scale融合)、MatMul分块(ND2NZ转换、L0级分块)、TopK算法(Histogram-based四级直方图)、**MXFP8量化计算(基础概念/常量/mmad_mx指令/Scale DN2NZ搬运)** |
| 05 | [Mask 与变长序列](../docs/qli-master/05-mask-sequence.md) | `05-mask-sequence.md` | PageAttention(Block Table、跨block拼接)、Causal Mask(rightDownCausal模式、动态块数)、变长序列(TND前缀和格式、无效序列处理) |
| 06 | [CV 比例适配](../docs/qli-master/06-cv-ratio-adapt.md) | `06-cv-ratio-adapt.md` | CVRATIO 2→1 适配、核比例宏定义、核类型声明(KERNEL_TYPE_MIX_AIC_1_1)、移除基本块减半逻辑、统一Scale加载、dualDstCtl关系、验证要点、**InitBuffers缓冲区大小清单(5公式)**、**条件编译保护Event ID(#if CVRATIO>1)**、**V1 vs V2基本块差异** |
| 07 | [MXFP8 优化与修复](../docs/qli-master/07-mxfp8-bugfix.md) | `07-mxfp8-bugfix.md` | Scale Buffer大小修复、ReLU移至Fixpipe随路执行、BatchMulWeightAndReduceSum支持任意batch、函数重命名(NoScale→MxFP8)、Q/KScale L1 Buffer分离、Fixpipe dstNdStride修复、qkVLstride修复、LoadKScaleToL1偏移修复、xStartPosition偏移修复(scale与mStartPosition对齐)、**LoadData2DMxParams参数体系(x/y方向语义)**、**MLA Prolog vs QLI V2实现对比** |
| 08 | [V2 Metadata 算子](../docs/qli-master/08-v2-metadata.md) | `08-v2-metadata.md` | AICPU运行时负载均衡、Metadata数据结构(36×8 AIC + 72×8 AIV)、API两段式接口、参数约束(A5/A2/A3差异)、三级递进分配策略(Batch→Row→Block)、开销模型、FD归约负载均衡、V2与V1差异对比、Kernel消费Metadata方式 |
| 09 | [Tiling 与性能优化](../docs/qli-master/09-tiling-perf.md) | `09-tiling-perf.md` | Tiling Key生成、性能优化要点(L1/L0缓存复用/Event池化/Bank冲突避免/L2 Cache控制)、**CVRATIO编译器优化分析(除法消除/常量折叠/条件编译)**、代码位置速查表 |
| 10 | [同步陷阱与修复经验](../docs/qli-master/10-sync-pitfalls.md) | `10-sync-pitfalls.md` | ProcessVec1提前返回缺V_MTE3同步(核卡死)、MXFP8 Mmad n维度16对齐(AIC error)、Fixpipe UB写缓冲未排空(精度错误)、**UB复用重叠(return_value开关导致索引越界)**、通用调试方法论 |
| 11 | [同步迁移经验](../docs/qli-master/11-sync-migration.md) | `11-sync-migration.md` | 上游仓库同步策略、提交分析与跳过决策、MXFP8代码保护、编译验证流程、审查报告处理、命名空间归属检查、经验教训总结 |

---

## 快速导航：按场景查找

### 开发新算子 / 理解整体架构
→ [01 核心概念](../docs/qli-master/01-core-concepts.md) + [02 流水线](../docs/qli-master/02-pipeline-sync.md)

### 调试 Buffer 溢出 / UB 空间不足
→ [03 Buffer 管理](../docs/qli-master/03-buffer-management.md)

### 修改量化模式 / MatMul / TopK 逻辑
→ [04 计算算法](../docs/qli-master/04-compute-algo.md)

### 处理 PageAttention / Causal Mask / 变长序列
→ [05 Mask 与变长序列](../docs/qli-master/05-mask-sequence.md)

### 适配新芯片（CV 比例变化）
→ [06 CV 比例适配](../docs/qli-master/06-cv-ratio-adapt.md)

### 排查 MXFP8 精度 / 数据错误
→ [07 MXFP8 优化与修复](../docs/qli-master/07-mxfp8-bugfix.md)

### 理解 / 修改 V2 Metadata 负载均衡
→ [08 V2 Metadata 算子](../docs/qli-master/08-v2-metadata.md)

### 性能调优 / 查找代码位置
→ [09 Tiling 与性能优化](../docs/qli-master/09-tiling-perf.md)

### 排查核卡死 / AIC error / 精度错误
→ [10 同步陷阱与修复经验](../docs/qli-master/10-sync-pitfalls.md)

### 从上游仓库同步修改 / 代码迁移
→ [11 同步迁移经验](../docs/qli-master/11-sync-migration.md)

---

## 关键公式速查

$$out = \text{Top-}k\left\{[1]_{1\times g}@\left[(W@[1]_{1\times S_{k}})\odot\text{ReLU}\left(\left(Scale_Q@Scale_K^T\right)\odot\left(Q_{index}^{Quant}@{\left(K_{index}^{Quant}\right)}^T\right)\right)\right]\right\}$$

| 符号 | 维度 | 固定值/范围 |
|------|------|------------|
| B | Batch Size | - |
| S1 | Query Seq Length | - |
| S2 | Key Seq Length | - |
| N1 | Query Head Num | [1, 64] |
| N2 | Key Head Num | **固定=1** |
| G | Group Size | N1/N2 |
| D | Head Dimension | **固定=128** |
| K | TopK Count | [1, 2048] |

| 架构 | S1_BASE | S2_BASE | M_BASE | 核配比 |
|------|---------|---------|---------|--------|
| arch22 (910B) | 4 | 2048 | 4×G | C:V=1:2 |
| arch35 (950) | 4 | 128 | 4×G | 独立核 |

---

## V1 vs V2 核心差异

| 维度 | V1 | V2 |
|------|----|----|
| 负载均衡 | Tiling 阶段静态分配 | AICPU 运行时动态分配 |
| Metadata | 无独立输出 | 1024 int32 Tensor |
| FD 归约 | 无 | AIV 核独立负载均衡 |
| 分核算法 | 简单均分 + 余数分配 | 三级递进 + 容忍度 + 强制分配 |
| 开销模型 | 块数均分 | `6*align16(M) + 10*align64(S2)` 加权 |

> 详见 [08 V2 Metadata 算子](../docs/qli-master/08-v2-metadata.md)

---

## 目录结构

```
agent/
└── qli-master.md                      ← 本文件（索引/agent 定义）
docs/
└── qli-master/
    ├── 01-core-concepts.md            ← 核心概念
    ├── 02-pipeline-sync.md            ← 流水线与核间同步
    ├── 03-buffer-management.md        ← Buffer 管理
    ├── 04-compute-algo.md             ← 计算算法
    ├── 05-mask-sequence.md            ← Mask 与变长序列
    ├── 06-cv-ratio-adapt.md           ← CV 比例适配
    ├── 07-mxfp8-bugfix.md             ← MXFP8 优化与修复
    ├── 08-v2-metadata.md              ← V2 Metadata 算子
    ├── 09-tiling-perf.md              ← Tiling 与性能优化
    ├── 10-sync-pitfalls.md            ← 同步陷阱与修复经验
    └── 11-sync-migration.md           ← 同步迁移经验
```

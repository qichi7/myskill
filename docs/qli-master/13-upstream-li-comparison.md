# 13 - 上游 LI 对比与 TopK 1-8192 设计经验

> 本模块记录从上游 `lightning_indexer_v2`（非量化版，FP16/BF16）学到的 TopK 1-8192 支持经验，
> 以及它与本仓 `quant_lightning_indexer_v2`（MXFP8 fork）的关键设计差异。
> 学习时间：2026-07-10，仅聚焦 arch35（950），不考虑 arch22（910B）。

---

## 13.1 两条路线的根本区别

上游 LI 和本仓 QLI 都支持 TopK 1-8192，但走了**完全不同的路线**：

| 维度 | 上游 LI (arch35) | 本仓 QLI (arch35) |
|------|------------------|-------------------|
| **大 topk 的 UB 压力由谁消化** | TopK 子系统自己消化 | Cube 子系统减小基本块来让 |
| **分档对象** | trunkLen（TopK 侧） | s1BaseSize（Cube 侧） |
| **分档数量** | 三档（5120/7168 两阈值） | 二档（2048 一阈值） |
| **trunkLen 是否随 topk 变** | 是（8K/4K/2K 自适应） | 否（固定 16384） |
| **基本块是否随 topk 变** | 否（只看 gSize） | 是（4→3） |
| **valueOutBuf 复用** | topk>2048 复用 mrgValueBuf | 始终独立分配 |
| **scoreOutBuf/topkSharedTmpBuf** | 独立分配 | 折叠进 resMm1UB_ |
| **TopK 算法宽度** | uint32 4趟基数排序 | uint16 2趟基数排序 |
| **LD/FlashDecode** | 无（arch35 不做 LD） | 有（完整 ProcessLD 路径） |

**核心结论**：上游 LI 的路线（不动基本块 + 自适应 trunkLen）回归风险更低，本仓 QLI 的路线（改基本块）UB 控制更激进但有溢出风险。

---

## 13.2 上游 LI 的三档自适应 trunkLen（核心经验）

### 13.2.1 机制

`lightning_indexer_v2_service_vector.h:29-34,196-197`：

```cpp
constexpr uint32_t TRUNK_LEN_8K = 8192;
constexpr uint32_t TRUNK_LEN_4K = 4096;
constexpr uint32_t TRUNK_LEN_2K = 2048;
constexpr uint32_t TOPK_LEN_7K = 7168;
constexpr uint32_t TOPK_LEN_5K = 5120;
...
trunkLen_ = constInfo.topk > TOPK_LEN_5K ?
            (constInfo.topk > TOPK_LEN_7K ? TRUNK_LEN_2K : TRUNK_LEN_4K) : TRUNK_LEN_8K;
```

| topk 范围 | trunkLen_ | mrgValueBuf_ = (Align(topk,256)+trunkLen)*2B |
|-----------|-----------|----------------------------------------------|
| 1 ~ 5120 | 8192 | 17KB ~ 27KB |
| 5121 ~ 7168 | 4096 | 19KB ~ 23KB |
| 7169 ~ 8192 | 2048 | 19KB ~ 20KB |

### 13.2.2 关键设计思想

`trunkLen_` 随 topk **反向变化**，使 `Align(topk,256) + trunkLen_`（即 mrgValueBuf_ 大小）**基本恒定**在 ~20-27KB，UB 不爆。trunk 越小，流式趟数越多（用算力换 UB），但 950 算力充足。

### 13.2.3 对本仓的启示

本仓 trunkLen 固定 16K，topk=8192 时 mrgValueBuf_ 达 49KB（是上游的 ~2.5 倍）。若引入自适应 trunkLen，topk>5120 时可省 16-24KB，可能不再需要减小基本块（s1BaseSize 保持 4），消除 Cube 侧的 topk 分支风险。

---

## 13.3 上游 LI 的基本块不动原则

### 13.3.1 s1BaseSize 与 topk 完全解耦

`lightning_indexer_v2_kernel.h:203-210`：

```cpp
if (constInfo.gSize == 64) {
    constInfo.mBaseSize = S1_BASE_SIZE_SMALL * constInfo.gSize;  // 2*64=128
    constInfo.s1BaseSize = S1_BASE_SIZE_SMALL;                   // 2
} else {
    constInfo.mBaseSize = S1_BASE_SIZE * constInfo.gSize;        // 4*gSize
    constInfo.s1BaseSize = S1_BASE_SIZE;                         // 4
}
```

- `constInfo.topk` 赋值后，**整个 arch35 kernel 中没有任何一处用它改 mBaseSize/s1BaseSize**。
- Cube 侧 `service_cube.h` **完全没有 topk 分支**，所有 buffer 用运行时 `constInfo_.mBaseSize`。
- `ConstInfo` 里的 `isSparseCountOver2K`、`mBaseSizeAlign` 字段在 arch35 中**从未被赋值/读取**（死字段）。

### 13.3.2 设计哲学

**Cube/Vector 基本块只服务 MatMul+ReduceSum，TopK 的 UB 压力由 TopK 自己消化**。基本块稳定 → Cube 路径零回归风险。

---

## 13.4 上游 LI 的 valueOutBuf 复用

`lightning_indexer_v2_service_vector.h:158-163`：

```cpp
if (topkCount_ <= 2048) {
    pipe->InitBuffer(valueOutBuf_, topkCountAlign256_ * sizeof(float));
    valueOutLocal_ = valueOutBuf_.Get<float>();
} else {
    valueOutLocal_ = mrgValueBuf_.Get<float>();   // topk>2048 直接别名 mrgValueBuf_
}
```

- topk≤2048：单独分配 valueOutBuf_（max 8KB）。
- topk>2048：**不分配新 buffer**，直接 Get 同一块 mrgValueBuf_，靠时序互斥保证安全（ProcessVec1 写完 score 到 GM 后，mrgValueBuf_ 才被 TopK 接管）。

**本仓 QLI 未采用此优化**，valueOutBuf_ 始终独立分配（topk=8192 时 16KB）。

---

## 13.5 上游 LI 的 topkCount_>trunkLen_ 退化路径

`lightning_indexer_v2_service_vector.h:441-515`：

```cpp
bool useSingleLoop = (s2LoopNum == 1) ||
                     ((topkCount_ > trunkLen_) && (validS2Len <= topkCountAlign256_));
...
if (topkCount_ > trunkLen_) {
    actS2LoopNum = 1 + (validS2Len - topkCountAlign256_ + trunkLen_ - 1) / trunkLen_;
}
// loopIdx==0 && topkCount_>trunkLen_ 时：纯拷贝 topkCountAlign256_ 个 score，不算 TopK
```

**精巧优化**：当 topk 比 trunk 还大，第一段直接全收进 scoreOutLocal，避免对 topk 个元素做无意义的 TopK 排序。

**本仓 QLI 无此路径**：trunk=16K > max topk=8K，`topkCount_ > trunkLen_` 永远为 false。若引入自适应 trunkLen（topk=8192+trunk=2048），此路径才有意义。

---

## 13.6 TopK 算法对比

### 13.6.1 上游 LI：uint32 4趟基数排序

- 分数类型：uint32（可排序键）
- 基数趟数：4 趟（32位拆 4 字节，每字节 256 桶）
- 算法文件：`vf_topk_gather.h`（命名空间 `liV2Topkb32gather`）
- 直方图 buffer：5×256（histograms + idx0~idx3）
- trunkLen 默认：8192

### 13.6.2 本仓 QLI：uint16 2趟基数排序

- 分数类型：uint16（可排序键，MXFP8 精度足够）
- 基数趟数：2 趟（16位拆 2 字节，每字节 256 桶）
- 算法文件：`vf_topk_16_gather.h`（命名空间 `topkb16gather`）
- 直方图 buffer：3×256（histograms + idxHigh + idxLow）
- trunkLen 默认：16384（因 uint16 省一半空间，可翻倍）

### 13.6.3 本仓 QLI 的 uint16 路径是核心创新

MXFP8 分数本身是 uint16 精度，用 2 趟基数排序替代 4 趟：
- 直方图 buffer 从 5×256 减到 3×256
- trunkLen 可翻倍到 16K
- 这是本仓 trunkLen=16K 仍可行的前提条件（上游 uint32 用 16K 会爆 UB）

### 13.6.4 流式多趟归并（两者共有）

核心是 `FindRealIndexVFImpl` 的双路索引映射：

```cpp
// tmpIdx < topK  → 指向"上一趟的 topK 历史" → Gather(hisIdxBuf[tmpIdx])
// tmpIdx >= topK → 指向"本趟新 trunk"      → tmpIdx + loopBasicIdx
MicroAPI::Compares<CMPMODE::GT>(pregNow, tmpIdx, topK-1, pregB32);
MicroAPI::Xor(pregHis, pregNow, pregB32, pregB32);
MicroAPI::Gather(outputGatherIdx, hisIdxBuf, tmpIdx, pregHis);
MicroAPI::Adds(outputAddsIdx, tmpIdx, loopIndex, pregNow);
MicroAPI::Add(outputGatherIdx, outputGatherIdx, outputAddsIdx, pregB32);
```

`hisIndexLocal[2]` 双缓冲 ping-pong，`hisIndexLocal[0]` 复用 `indicesOutBuf_`。两者算法一致，仅宽度不同。

---

## 13.7 op_host 层对比

### 13.7.1 TopK 校验

| 维度 | 上游 LI | 本仓 QLI |
|------|---------|---------|
| 上限常量 | `TOPK_MAX=8192`（`tiling.h:83`） | `SPARSE_LIMIT=8192`（`tiling.h:84`） |
| 死常量 | `SPARSE_LIMIT=2048`（从未引用） | 无 |
| 阈值常量 | 无 TOPK_THRESHOLD | `TOPK_THRESHOLD=2048`（用于 s1BaseSize 切换） |
| 校验范围 | 1-8192 连续 | 1-8192 连续 |

### 13.7.2 Workspace 计算

| 维度 | 上游 LI (arch35) | 本仓 QLI (arch35) |
|------|------------------|-------------------|
| scoreGm | `s1BaseSize * ceil(s2/128)*128 * sizeof(uint16_t) * aicNum` | 同（但 sizeof(uint16_t)） |
| LD workspace | **无** | `2*s1BaseSize*2*topk*4*aicNum` + params |
| topk 依赖 | **无**（workspace 与 topk 无关） | **有**（LD 中间结果项随 topk 线性增长） |

上游 arch35 workspace 与 topk 完全无关（TopK 全在 UB 内）；本仓因有 LD 路径，workspace 随 topk 增长。

### 13.7.3 tiling key

两者一致：**topk 不进 tiling key**，1-8192 共用同一编译产物。tiling key 6 个维度：DT_Q / DT_K / DT_OUT / PA / Q_LAYOUT / K_LAYOUT。

---

## 13.8 上游 LI 测试覆盖缺口

repo 内实测的 topk 值：

| 位置 | topk 值 |
|------|---------|
| pytest 单测 | 仅 128 |
| UT infershape | 128, 2048, 64 |
| UT tiling | 128, 2048, 10000（负例） |
| examples | 32 |

**3072/4096/5120/6144/7168/8192 在 repo 内没有任何测试用例**。上游代码支持 1-8192，但测试覆盖严重不足。

---

## 13.9 对本仓的改进建议

### 13.9.1 引入自适应 trunkLen（最高价值）

```cpp
// 建议在本仓 service_vector.h 引入
trunkLen_ = constInfo.sparseCount > TOPK_LEN_5K ?
            (constInfo.sparseCount > TOPK_LEN_7K ? TRUNK_LEN_4K : TRUNK_LEN_8K) : TRUNK_LEN_16K;
```

注意：本仓是 uint16，trunkLen 可以比上游 uint32 翻倍。建议分档：topk≤5120→16K，5121~7168→8K，7169~8192→4K。

**收益**：topk>5120 时 mrgValueBuf_ 可省 16-24KB，可能不再需要减小基本块（s1BaseSize 保持 4），消除 Cube 侧 topk 分支和 scoreOutLocal_ UB 溢出风险。

### 13.9.2 valueOutBuf 复用 mrgValueBuf

topk>2048 时 valueOutLocal_ 复用 mrgValueBuf_（抄上游:158-163），可省 16KB（topk=8192 时）。

### 13.9.3 保持不变的正确决策

| 决策 | 状态 |
|------|------|
| topk 不进 tiling key | ✅ 与上游一致 |
| TopK 算法零硬编码上限 | ✅ 与上游一致 |
| InferShape 用 topk 直传 | ✅ 与上游一致 |
| G 支持 [1,64] 含奇数尾部 | ✅ 比上游更完善 |
| uint16 2趟基数排序 | ✅ 比上游更精简（MXFP8 适配） |
| resMm1UB_ 折叠 scoreOut/sharedTmp | ✅ 比上游更省 UB（需注意重叠风险） |
| LD/FlashDecode 支持 | ✅ 上游 arch35 没有，本仓领先 |

---

## 13.10 关键代码位置速查

| 内容 | 上游 LI file:line | 本仓 QLI file:line |
|------|-------------------|---------------------|
| TopK 分档阈值 | `service_vector.h:33-34`（5120/7168） | `common.h:27`（2048） |
| trunkLen 取值 | `service_vector.h:196-197`（自适应） | `service_vector.h:241`（固定16K） |
| s1BaseSize 分档 | `kernel.h:203-210`（不看topk） | `kernel.h:216-217`（看topk） |
| valueOutBuf 复用 | `service_vector.h:158-163` | `service_vector.h:205`（不复用） |
| 退化拷贝路径 | `service_vector.h:441-515` | 无 |
| TopK 算法 | `vf_topk_gather.h`（uint32 4趟） | `vf_topk_16_gather.h`（uint16 2趟） |
| Cube topk 分支 | 无 | `kernel.h:216-217` |
| host topk 上限 | `tiling.h:83`（TOPK_MAX=8192） | `tiling.h:84`（SPARSE_LIMIT=8192） |
| host workspace | `tiling.cpp:942-947`（与topk无关） | `tiling.cpp:1037-1047`（含topk项） |

---

## 13.11 核心经验十二条

1. **基本块能不动就不动**：上游把 topk 压力完全关在 TopK 子系统内，Cube/Vector 基本块对 topk 透明，回归风险最低。
2. **自适应 trunkLen 是大 topk 的钥匙**：trunkLen 随 topk 反向变化，锁死 mrgValueBuf 和 shared tmp，UB 不爆。
3. **valueOut 复用 mrgValueBuf**：topk>2048 时不单独分配 valueOut，直接别名，省一整块 UB。
4. **topkCountAlign256 驱动 buffer 一族**：indicesOutBuf/scoreOutBuf/sharedTmp 随 Align(topk,256) 线性增长，但 trunkLen 自适应抵消，总量可控。
5. **TopK 算法零改动**：基数直方图 + 流式归并天然支持任意 topk，循环次数全运行时推导。
6. **流式归并双路索引映射**：tmpIdx<topK 走 Gather 历史，tmpIdx≥topK 走 Adds 新trunk，ping-pong 双缓冲。
7. **第 0 趟退化拷贝**：topk>trunkLen 时第 0 趟纯拷贝不算 TopK，避免对 topk 个元素做无意义排序。
8. **topk 不进 tiling key**：1-8192 共用一份编译产物，避免变体爆炸。
9. **host 校验用 TOPK_MAX=8192**：别碰死常量 SPARSE_LIMIT。
10. **arch35 workspace 与 topk 无关**：950 上 TopK 全在 UB 内完成，workspace 只看 s2 和核数。
11. **G 循环 unroll2 无奇数尾部**：上游假设 G 偶数，本仓支持奇数 G 必须自补尾部。
12. **测试要补大 topk**：上游 repo 内 3072~8192 全无测试，务必在 pytest paramset 补全。

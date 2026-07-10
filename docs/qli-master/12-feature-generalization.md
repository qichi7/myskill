# 12 - 特性泛化与 aclGraph 支持

> 本模块记录 G 泛化(64→1~64)、cmpRatio 放宽(枚举→2的幂全集)、aclGraph 支持三个特性的开发经验。
> 以及从上游提交对比中发现的 UB 布局对齐、Fixpipe 16 对齐、Vector1 尾部处理等关键知识点。

---

## 12.1 G 泛化：从固定 64 到 1~64

### 12.1.1 设计原则

```
s1BaseSize = 4 (topk<=2048) 或 3 (topk>2048)  ← 固定
gBaseSize = gSize (1~64)                       ← 随实际 g 改变
mBaseSize = s1BaseSize * gSize                 ← 派生量
```

G=64 时 mBaseSize=256/192，与原固定常量完全一致，保证向后兼容。

### 12.1.2 核心概念：两类用途分离

泛化后必须严格区分 mBaseSize 的两类用途：

| 用途 | 变量 | 说明 |
|------|------|------|
| **Buffer 分配** | `mBaseSizeAlign16 = CeilAlign(mBaseSize, 16)` | NZ 格式要求 M 维按 16 对齐，G 非 4 的倍数时 mBaseSize 不是 16 的倍数 |
| **循环步进 / 实际数据量** | `mBaseSize`（原始值） | 控制实际处理的元素数，不需要对齐 |

```cpp
// ConstInfo 新增字段
uint32_t mBaseSizeAlign16 = 1ULL;  // mBaseSize 按16向上对齐（NZ格式要求），用于Buffer分配

// InitTilingData 中
constInfo.mBaseSize = constInfo.s1BaseSize * constInfo.gSize;
constInfo.mBaseSizeAlign16 = CeilAlign(constInfo.mBaseSize, (uint32_t)16);
```

### 12.1.3 G=64 兼容性保证

G=64 时 mBaseSize（256 或 192）恒为 16 的倍数，使得 `mBaseSizeAlign16 == mBaseSize`：
- 所有 Buffer 大小不变
- 循环步进变量不变
- Vector1 奇数尾部 `if(64 & 1) = false` 不执行

### 12.1.4 需修改的 7 处（arch35 only，arch22 不改）

| # | 文件 | 修改 |
|---|------|------|
| 1 | `tiling.h` | `G_SIZE_LIMIT` → `G_SIZE_MIN=1`/`G_SIZE_MAX=64` |
| 2 | `tiling.cpp` | `GetGSize()` 放宽到 `[1,64]` |
| 3 | `common.h` | ConstInfo 新增 `mBaseSizeAlign16` |
| 4 | `kernel.h` | `M_BASE_SIZE_*` → `S1_BASE_SIZE_*`；mBaseSize 派生 |
| 5 | `service_cube.h` | Buffer offset 用 mBaseSizeAlign16；循环步进保持 mBaseSize |
| 6 | `service_vector.h` | resMm1Buf_ 用 mBaseSizeAlign16 |
| 7 | `vf/vector1.h` | S1=1 路径 4 个函数添加奇数 G 尾部处理 |

### 12.1.5 已泛化部分（无需修改）

- **Metadata AICPU**：已使用 `mBaseSize = s1BaseSize * groupSize_` 泛化模式
- **Kernel 偏移计算**：全部使用 `constInfo.gSize`/`constInfo.mBaseSize` 运行时变量
- **Cube G 相关循环**：`CeilAlign(s1gL1RealSize, gSize)` 已泛化
- **Fixpipe mSize**：按实际 `s1gL0RealSize` 控制，不依赖固定 G

---

## 12.2 Fixpipe 16 对齐

### 12.2.1 问题

L0C 是 NZ 格式，按 16 行 tile 存储（`mmadParams.m = CeilAlign(s1gL0RealSize, 16)`）。
但 Fixpipe 的 mSize 只做偶数对齐，不做 16 对齐。

G=64 时 s1gL0RealSize=256（恰好 16 对齐），无问题。
G=3 时 s1gL0RealSize=12，mSize=12（**不是 16 对齐**），Fixpipe 读取错误的 tile 边界。

### 12.2.2 修改

```cpp
// float 路径（MXFP8 使用）
// 修改前：偶数对齐
uint32_t mSize = (s1gL0RealSize + 1) >> 1 << 1;
// 修改后：CeilAlign 到 16
uint32_t mSize = ((s1gL0RealSize + 15) / 16) * 16;

// bf16 路径
// 修改前
uint32_t mSize = (s1gL0RealSize + 1) >> 1 << 1;
uint32_t srcStride = ((mSize + 15) / 16) * 16;
// 修改后
uint32_t mSize = ((s1gL0RealSize + 15) / 16) * 16;
uint32_t srcStride = mSize; // 已16对齐
```

### 12.2.3 qkVLstride 联动修改

Fixpipe 写入 UB 的行数改为 16 对齐后，Vector1 读取 qk 的行间距必须匹配：

```cpp
// 修改前
auto qkVLstride = (uint64_t)info.actMBaseSize * (UB_BANK_DEPTH_STRIDE / sizeof(QK_T));
// 修改后
auto qkVLstride = (uint64_t)CeilAlign(info.actMBaseSize, 16) * (UB_BANK_DEPTH_STRIDE / sizeof(QK_T));
```

---

## 12.3 UB 行间距动态化（dstStride/srcStride）

### 12.3.1 问题

每行数据在 UB 中应占满 `UB_BANK_DEPTH_STRIDE`（512B）。旧代码假设 blockLen 恒等于 `UB_BANK_STRIDE`（256B，即 G=64）：

```cpp
// 旧代码（仅 G=64 正确）
qwDataCopyExtParams.dstStride = (UB_BANK_DEPTH_STRIDE - UB_BANK_STRIDE) / 32;  // = (512-256)/32 = 8
```

G=64 时 blockLen=256，行宽 = 256 + 8×32 = 512 ✅
G=32 时 blockLen=128，行宽 = 128 + 8×32 = 384 ❌（应为 512）

### 12.3.2 修改

```cpp
// weight/qScale DataCopyPad
qwDataCopyExtParams.dstStride = (UB_BANK_DEPTH_STRIDE - qwDataCopyExtParams.blockLen) / 32;

// scoreGm DataCopyPad
copyOutParams.srcStride = (UB_BANK_DEPTH_STRIDE - copyOutParams.blockLen) / 32;
```

**原理**：行总宽度 = `blockLen + dstStride * 32 = blockLen + (512 - blockLen) = 512`，任意 G 均正确。

### 12.3.3 G=64 等价性

G=64 时 blockLen=256=UB_BANK_STRIDE，`(512-256)/32 = 8`，与旧代码一致。

---

## 12.4 Vector1 奇数 G 尾部处理

### 12.4.1 问题

S1=1 路径使用 unroll2 循环（步长 2），奇数 G 时最后一次迭代越界：

```cpp
for (uint16_t i = 0; i < gSize; i += 2) {
    // 处理 i 和 i+1 → i+1=gSize 越界！
}
```

### 12.4.2 修复

```cpp
uint16_t gSizeEven = (uint16_t)(gSize) & 0xFFFE;  // 偶数部分
for (uint16_t i = 0; i < gSizeEven; i += 2) {
    // 原有 unroll2 逻辑
}
// 奇数 G 尾部：最后一个元素累加到 regSum0（regSum1 保持 0）
if ((uint16_t)(gSize) & 1) {
    uint16_t i = gSizeEven;
    // 加载并累加到 regSum0
}
```

### 12.4.3 影响范围

4 个 S1=1 变体函数需修改（均在 `vf/quant_lightning_indexer_v2_vector1.h`）：
- `MulWeightAndReduceSum` (float, 非 MXFP8)
- `MulWeightAndReduceSum_MxFP8` (float)
- `MulWeightAndReduceSum_MxFP8` (bf16)
- `MulWeightAndReduceSumPerTensor`

S1=2 变体使用 `i++` 步长，不受影响。

### 12.4.4 上游替代方案

上游用 `CeilAlign(s1gL1RealSize, 2 * gSize)` 对齐保证完整 G 对，不需要尾部处理。两种方案不应混用。

---

## 12.5 cmpRatio 放宽

### 12.5.1 修改

arch35 tiling 校验从枚举 `{1, 4, 128}` 放宽到 1~128 所有 2 的幂：

```cpp
// 修改前
OP_CHECK_IF(*opParamInfo_.cmpRatio != 1 && *opParamInfo_.cmpRatio != 4 && *opParamInfo_.cmpRatio != 128, ...);

// 修改后（与 arch22 一致）
OP_CHECK_IF((*opParamInfo_.cmpRatio <= 0) || (*opParamInfo_.cmpRatio > 128) ||
            ((*opParamInfo_.cmpRatio & (*opParamInfo_.cmpRatio - 1)) != 0), ...);
```

### 12.5.2 无需 kernel 修改

cmpRatio 在 kernel 中仅用于通用乘除法（`GetActualSeqLenKey`、`GetS1S2ActualSeqLen`、`GetS2BaseBlockNumOnMask`），无硬编码或位运算优化。

---

## 12.6 aclGraph 支持

### 12.6.1 ProcessTopK LD 路径 SetFlag 补充

**问题**：ProcessTopK 的 LD 路径（第二个分支）在两次 DataCopyPad 后缺少 `SetFlag<MTE3_V>`，非 LD 路径（第一个分支）已有。aclGraph 对流水线同步要求更严格。

```cpp
// LD 路径补充
AscendC::DataCopyPad(ldScoreGm[offset], scoreOutLocal_, ldCopyScoreOutParams);
AscendC::DataCopyPad(ldIndexGm[offset], indicesOutLocal_.ReinterpretCast<int32_t>(), ldCopyOutParams);
SetFlag<HardEvent::MTE3_V>(TOPK_MTE3_V_EVENT);  // 新增
```

### 12.6.2 golden.py hifp8 q_scale/k_scale 修复

```python
# 修改前（标量，所有元素相同）
q_scale = random.uniform(q_scale_datarange[0], q_scale_datarange[1])
query_dequant_scale = torch.tensor(np.random.uniform(q_scale, q_scale, ...))

# 修改后（范围随机）
query_dequant_scale = torch.tensor(np.random.uniform(q_scale_datarange[0], q_scale_datarange[1], ...))
```

mxfp8 路径此前已正确，仅 hifp8 路径需修复。

### 12.6.3 aclGraph 测试文件

新增 `quant_lightning_indexer_v2_acl_graph.py`，核心模式：

```python
import torchair
from torchair.configs.compiler_config import CompilerConfig

class QLIV2Network(nn.Module):
    def forward(self, query, key, weights, q_descale, k_descale, ...):
        return torch.ops.cann_ops_transformer.quant_lightning_indexer(...)

config = CompilerConfig()
npu_backend = torchair.get_npu_backend(compiler_config=config)
config.mode = "reduce-overhead"
npu_mode = torch.compile(QLIV2Network().npu(), fullgraph=True, backend=npu_backend, dynamic=False)
```

在 `test_single.py` 中 eager 测试后追加 acl_graph 测试。

---

## 12.7 上游对比经验

### 12.7.1 上游 G 泛化提交（250c3862）

上游仅支持 G=32/64（枚举扩展），当前实现支持 G=1~64（范围校验），更完整：

| 特性 | 上游 | 当前 |
|------|------|------|
| G 范围 | 仅 32/64 | 1~64 全泛化 |
| topk>2048 | ❌ | ✅ |
| NZ 16 对齐 | ❌ | ✅ |
| 奇数 G 支持 | ❌ | ✅ |

### 12.7.2 UB 行间距动态化（从上游对比发现）

上游修复了 weight/qScale/out 的 DataCopyPad dstStride/srcStride，当前实现遗漏。这是 **G≠64 时 UB 布局错位**的根因，必须修复。

### 12.7.3 aclGraph 提交分类（fbed7b38）

上游提交标题"拦截补充 & 支持aclGraph"包含两部分：
1. **拦截补充**：tiling 层输入校验完善（通用校验，与 aclGraph 无关）
2. **支持 aclGraph**：SetFlag 补充 + golden 修复 + 测试新增

需区分哪些修改真正与 aclGraph 相关，哪些是通用校验。

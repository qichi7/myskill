# 计算算法

## 量化流程

### 存8算8策略

- **输入**: INT8 / FLOAT8_E4M3 / HIFLOAT8
- **Scale**: FLOAT16 / FLOAT32
- **累加**: INT32
- **输出**: FP16 / FP32

### Scale融合

```cpp
// (Q@K) * (qScale @ kScale^T)
Mul(resMm1UB_, resMm1UB_, qScaleUB_);
Mul(resMm1UB_, resMm1UB_, kScaleUB_);
```

## MatMul分块

### ND2NZ转换

ND格式 `[B,S,N,D]` → NZ格式 `[N,(S/16),(D/16),16,16]`

```cpp
Load3Dv2(queryL1_, queryGm, loadParams, s1gL1RealSize, D_BASIC_BLOCK);
```

### L0级分块

```cpp
// arch35 (quant_lightning_indexer_v2_service_cube.h)
M_BASIC_BLOCK    = 256   // L1 的 M 维基本块
M_BASIC_BLOCK_L0 = 256   // L0 的 M 维基本块
D_BASIC_BLOCK_L0 = 128   // D 维基本块
S2_BASIC_BLOCK_L0 = 128  // S2 维基本块

// [S1G_L0, S2_L0] x [S2_L0, D] = [S1G_L0, D]
```

> 注：`M_BASIC_BLOCK = M_BASIC_BLOCK_L0 = 256`，因此 L1→L0 的内层 `s1gL1Offset` 循环只执行一次，恒为 0。

## MXFP8 量化计算

### 基础概念

| 项 | 说明 |
|----|------|
| 数据类型 | `fp8_e4m3fn_t`（8位浮点，4位指数+3位尾数） |
| Scale 类型 | `fp8_e8m0_t`（8位指数，无尾数，表示 2 的幂次） |
| 量化粒度 | 每 **32 个 D 维元素** 共享一个 scale（per-block） |
| 硬件指令 | `mmad_mx`（通过 `mx_fp8_e4m3_t` 类型触发） |
| 矩阵乘输出 | `float` |

### 关键常量

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_common.h`

```cpp
constexpr uint32_t MXFP8_BLOCK_SIZE = 32;  // 每32个D维度元素一个scale
constexpr uint32_t FP8_TWO = 2;            // 2个fp8_e8m0打包成1个bf16
```

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_service_cube.h`

```cpp
static constexpr uint64_t FP8_BLOCK_CUBE = 32;  // K轴步进32B（对应1个block的scale）
```

### MXFP8 vs 普通 FP8

| 对比项 | 普通 FP8 | MXFP8 |
|--------|---------|-------|
| Scale 类型 | `float`（per-tensor/per-channel） | `fp8_e8m0_t`（per-block） |
| Scale 粒度 | 1个/128个元素 | 1个/32个元素 |
| Scale 搬运 | 独立搬运 | 与数据一起通过 DN2NZ 搬运 |
| 矩阵乘指令 | 普通 `Mmad` | `Mmad` + `mx_fp8_e4m3_t` 类型 |
| 判断标志 | `!IS_MXFP8` | `IS_MXFP8`（`QLIV2T::isMxFp8`） |

### mmad_mx 硬件指令机制

通过将 L0 tensor `ReinterpretCast` 为 `mx_fp8_e4m3_t` 类型，触发硬件的 MX 矩阵乘指令，scale 在 L0 加载阶段通过 `LoadData` 的 Mx 变体一并传入。

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_service_cube.h` → `ComputeL0c`

```cpp
if constexpr (IS_MXFP8) {
    LocalTensor<mx_fp8_e4m3_t> qL0Mx =
        queryL0_[...].template ReinterpretCast<mx_fp8_e4m3_t>();
    LocalTensor<mx_fp8_e4m3_t> kL0Mx =
        keyL0_[...].template ReinterpretCast<mx_fp8_e4m3_t>();
    Mmad(cL0_[...], qL0Mx, kL0Mx, mmadParams);  // 输出 float
}
```

### Scale 的 GM→L1 搬运（DN2NZ）

Q/K 的 scale 通过 `Dn2NzParams` 从 GM 搬运到 L1，2 个 `fp8_e8m0` 打包成 1 个 `bf16` 存储。

> 文件：`op_kernel/arch35/quant_lightning_indexer_v2_service_cube.h` → `LoadQScaleToL1` / `LoadKScaleToL1`

```cpp
uint32_t scalePerToken = constInfo_.headDim / MXFP8_BLOCK_SIZE;  // D/32 = 4

Dn2NzParams dn2Nzparam;
dn2Nzparam.dnNum = 1;
dn2Nzparam.nValue = scalePerToken / FP8_TWO;        // 2个fp8=1个bf16 → nValue=2
dn2Nzparam.dValue = s1gL1RealSize;                   // 行数=token数
dn2Nzparam.srcDValue = scalePerToken / FP8_TWO;      // 源stride
dn2Nzparam.dstNzC0Stride = scalePerToken / FP8_TWO;  // NZ输出C0 stride
dn2Nzparam.dstNzNStride = 1;                          // 每个token在N维占1个位置
dn2Nzparam.dstNzMatrixStride = scalePerToken / FP8_TWO;

uint64_t gmOffset = runInfo.tensorQScaleOffset + s1gGmOffset * scalePerToken;
DataCopy(scaleL1, scaleGmCast[gmOffset / FP8_TWO], dn2Nzparam);
```

**参数含义**：
- `nValue`：单个 DN 矩阵的列数（scale 数 / FP8_TWO，因为 2 个 fp8 打包成 1 个 bf16）
- `dValue`：行数（= token 数）
- `dstNzNStride=1`：每个 token 在 N 维占 1 个位置，与 D 维压缩比无关

## TopK算法

### Histogram-based

- **复杂度**: O(N)
- **四级直方图**: 32位 → 4个8位
- **VF指令**: Histograms, Squeeze, FindTargetBin

```cpp
HistogramsFirstVFImpl(...);  // 高8位
HistogramsSecondVFImpl(...); // 中8位
HistogramsThirdVFImpl(...);  // 低8位
HistogramsLastVFImpl(...);   // 最后8位
```

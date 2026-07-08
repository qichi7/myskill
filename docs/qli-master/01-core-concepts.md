# QLI 核心概念

## 核心公式

$$out = \text{Top-}k\left\{[1]_{1\times g}@\left[(W@[1]_{1\times S_{k}})\odot\text{ReLU}\left(\left(Scale_Q@Scale_K^T\right)\odot\left(Q_{index}^{Quant}@{\left(K_{index}^{Quant}\right)}^T\right)\right)\right]\right\}$$

## 关键维度

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

## 三大布局

- **BSND**: [B, S, N, D]
- **TND**: [T, N, D] (T=S累加)
- **PA_BSND**: [block_num, block_size, N, D]

## 切块策略

### 基本块大小

| 架构 | S1_BASE | S2_BASE | M_BASE | 特点 |
|------|---------|---------|---------|------|
| arch22 | 4 | **2048** | 4*G | 大块，充分利用Cube |
| arch35 | 4 | **128** | 128 | 小块，向量流水线 |

### 负载均衡算法

```cpp
minBlockPerCore = totalBlockNum / coreNum;
deal1MoreBlockCoreNum = totalBlockNum % coreNum;
// 前deal1MoreBlockCoreNum个核多处理一块
```

**记录方式**: 双闭区间 `[start, end]`

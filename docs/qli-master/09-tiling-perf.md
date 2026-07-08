# Tiling Key 与性能优化

## Tiling Key

```cpp
uint32_t tilingKey = GET_TPL_TILING_KEY(
    inputQType,      // DT_Q
    inputKType,      // DT_K
    outputType,      // DT_OUT
    pageAttentionFlag,
    inputQLayout,
    inputKLayout
);
```

## 性能优化要点

✅ **L1/L0缓存复用**: Key Cache在S1方向多次使用  
✅ **Event池化**: 动态分配释放，避免冲突  
✅ **Offset预计算**: 减少循环内重复计算  
✅ **Bank冲突避免**: UB_BANK_STRIDE=256B  
✅ **L2 Cache控制**: 小序列禁用，减少miss  
✅ **Workspace规划**: 按核分配，双缓冲布局  
✅ **CVRATIO编译时常量**: 编译器常量折叠+条件编译消除无用代码  

## CVRATIO 编译器优化分析

CVRATIO 定义为编译时常量（`#define CVRATIO 1`），编译器可进行三类优化：

### 1. 除法消除

```cpp
// 源代码
aiCoreIdx = tmpBlockIdx / CVRATIO;  // kernel.h:476

// CVRATIO=1 时编译器优化为：
aiCoreIdx = tmpBlockIdx;  // 直接赋值，无除法操作
```

### 2. 常量折叠

```cpp
// 源代码
auto s1BaseSizePerAIV = CeilDiv(s1BaseSize_, CVRATIO);  // service_vector.h:433

// CVRATIO=1 时 CeilDiv(x, 1) 直接返回 x：
s1BaseSizePerAIV = s1BaseSize_;
```

### 3. 条件编译消除无用代码

```cpp
// CVRATIO=1 时，#if CVRATIO > 1 内的代码完全消除
#if CVRATIO > 1
CrossCoreWaitFlag<...>(... + AIV0_AIV1_OFFSET);  // 编译后不存在
#endif
```

**收益**：
- 消除所有除法运算（最慢的算术运算）
- 条件编译代码完全消除，减少代码体积
- 零运行时开销

## 代码位置速查

| 功能 | 文件路径 | 关键函数 |
|------|---------|---------|
| Tiling逻辑 | op_host/...tiling.cpp | SplitCore |
| 核协同 | kernel.h | ProcessBaseBlock |
| Cube计算 | service_cube.h | ComputeMm1 |
| Vector计算 | service_vector.h | ProcessVec1 |
| TopK算法 | vf/vf_topk.h | LiTopKVF |
| PageAttention | service_vector.h | GetKeyScale |
| Causal Mask | kernel.h | GetS2BaseBlockNumOnMask |
| ND2NZ转换 | service_cube.h | QueryNd2Nz |
| CVRATIO宏定义 | arch35/...v2_common.h:28 | `#define CVRATIO 1` |
| CVRATIO编译器优化 | kernel.h:476, service_vector.h:433, service_cube.h:196 | 除法消除/常量折叠/条件编译 |

## Memory检索命令

```bash
# 查找流水线设计
mem-find --query "流水线设计" --types discovery

# 查找切块策略
mem-find --query "切块策略" --types discovery

# 查找量化优化
mem-find --query "量化" --types discovery

# 查找PageAttention
mem-find --query "PageAttention" --types discovery
```

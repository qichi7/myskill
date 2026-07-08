# Mask 与变长序列

## PageAttention

### Block Table

```cpp
block_table: [B, maxBlockNumPerSeq]
block_size: 16倍数, max=1024

// 映射逻辑block到物理block
int32_t blockId = blockTableGm.GetValue(batchOffset + blockIdx);
```

### 跨block拼接

```cpp
if (startBlockTableOffset > 0) {
    // 第一部分：当前block尾部
    DataCopyPad(kScaleUB[blockId * blockSize + offset], ...);
    // 第二部分：后续block头部
    blockId = blockTableGm.GetValue(...);
    DataCopyPad(kScaleUB[blockId * blockSize], ...);
}
```

## Causal Mask

### rightDownCausal模式

每个Query只能看到之前的Key

```cpp
validS2Len = actS2SizeOrig - actS1Size + s1Offset + s1BaseSize;
validS2Len = Min(validS2Len, actS2SizeOrig);
validS2Len = Max(validS2Len, 1);
```

**动态块数**: `GetS2BaseBlockNumOnMask(s1gIdx, ...)`

## 变长序列

### TND格式（前缀和）

```cpp
// [10, 20, 35] 表示 batch0:10, batch1:10, batch2:15
actLen = actualSeqLengthsGm[bIdx] - actualSeqLengthsGm[bIdx-1];
```

### 无效序列处理

```cpp
if (actS2Size == 0 || actS1Size == 0) {
    Duplicate(outInvalidLocal_, INVALID_IDX, sparseCount);
}
```

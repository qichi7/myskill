# 流水线与核间同步

## 异构协同流水线

### 核配比

- **arch22**: C:V = 1:2
- **arch35**: 独立核

### 流水线阶段

```
V0 → C1 → V1 → LD
预处理 → 矩阵乘 → 后处理 → 归约
```

### 核间同步

```cpp
// Cube核
CrossCoreWaitFlag(syncV1C1);
ComputeMm1(runInfo);
CrossCoreSetFlag<FIA_SYNC_MODE2>(syncC1V1);

// Vector核
CrossCoreWaitFlag(syncC1V1);
ProcessVec1(runInfo);
CrossCoreSetFlag<FIA_SYNC_MODE2>(syncV1C1);
```

**同步模式**:
- FIA_SYNC_MODE2 (arch22)
- QLI_SYNC_MODE4 (arch35)

## Event ID管理

```cpp
// 动态分配
void AllocEventID() {
    SetFlag<V_MTE2>(VEC1_V_MTE2_EVENT_KSCALE + 0);
    SetFlag<V_MTE2>(VEC1_V_MTE2_EVENT_KSCALE + 1);
}

// 动态释放
void FreeEventID() {
    WaitFlag<V_MTE2>(VEC1_V_MTE2_EVENT_KSCALE + 0);
    WaitFlag<V_MTE2>(VEC1_V_MTE2_EVENT_KSCALE + 1);
}
```

## Offset预计算

```cpp
// 第一次循环计算所有base offset
if (runInfo.isFirstS2InnerLoop) {
    queryCoreOffset = ...;
    weightsCoreOffset = ...;
    indiceOutCoreOffset = ...;
}

// 后续循环复用
runInfo.tensorQueryOffset = queryCoreOffset;
```

## 架构适配

```cpp
NpuArch npuArch = ascendcPlatform.GetCurNpuArch();

if (npuArch == DAV_2201) {
    // arch22: S2_BASE_SIZE=2048
} else if (npuArch == DAV_3510) {
    // arch35: S2_BASE_SIZE=128
}
```

# 10 - 同步陷阱与修复经验

> QLI V2 算子开发中遇到的三类典型同步问题，涵盖核间同步、硬件指令对齐、流水线栅栏。

---

## 10.1 ProcessVec1 提前返回路径缺少 V_MTE3 同步

### 问题现象
- 特定 `actS1Size` 值下核卡死（hang），无报错
- 触发条件：`actS1Size` 是 `s1BaseSize_(4)` 的整数倍（如 4, 8, 12...）

### 根因分析

`service_vector.h` 的 `ProcessVec1` 中，当 `curAivS1ProcNum == 0` 时提前返回。
正常路径末尾有完整的 MTE3 同步序列：

```cpp
// 正常路径末尾 (lines 478-491)
SetFlag<HardEvent::V_MTE3>(VEC1_V_MTE3_EVENT + pingpong);    // ①
WaitFlag<HardEvent::V_MTE3>(VEC1_V_MTE3_EVENT + pingpong);   // ②
// ... DataCopyPad(scoreGm) ...                                // ③ MTE3搬运
SetFlag<HardEvent::MTE3_V>(VEC1_MTE3_V_EVENT + pingpong);    // ④
CrossCoreSetFlag(CROSS_VC_EVENT + pingpong + aivOffset);     // ⑤
```

但提前返回路径**缺少 ①②④**，导致：
- 下一迭代的 `WaitFlag(MTE3_V)` (line 434) 永久阻塞
- 或 ProcessTopK 的 `WaitFlag(MTE3_V)` (line 581) 永久阻塞

### 修复方案

```cpp
if (curAivS1ProcNum == 0) {
    CrossCoreWaitFlag<...>(CROSS_CV_EVENT + pingpong + aivOffset);
    SetFlag<HardEvent::V_MTE3>(VEC1_V_MTE3_EVENT + pingpong);    // 新增
    WaitFlag<HardEvent::V_MTE3>(VEC1_V_MTE3_EVENT + pingpong);   // 新增
    SetFlag<HardEvent::MTE3_V>(VEC1_MTE3_V_EVENT + pingpong);    // 新增
    CrossCoreSetFlag<...>(CROSS_VC_EVENT + pingpong + aivOffset);
    return;
}
```

### 检查要点
- **任何提前返回路径**都必须检查是否遗漏了正常路径末尾的同步操作
- 特别关注 ping-pong buffer 的 Event 同步（V_MTE3/MTE3_V、V_MTE2/MTE2_V 等）
- 触发条件：`actS1Size % s1BaseSize_ == 0` 时尾部 gS1 块的 `curS1ProcNum = 0`

---

## 10.2 MXFP8 Mmad 指令 n 维度必须 16 对齐

### 问题现象
- MXFP8 场景下 AIC error（非法指令错误）
- 特定 `actS2Size` 值触发，如 red_2(actS2=8)、red_3(actS2=1~4)

### 根因分析

`mmad_mx` 硬件指令要求 n 维度必须是 `BLOCK_CUBE(16)` 的倍数。
QLI 的 S2 长度是运行时动态值：

```
actS2SizeOrig = seqused_k × cmp_ratio + cmp_residual_k
actS2Size = actS2SizeOrig / cmp_ratio
tail_n = actS2Size % s2BaseSize(128)   // 尾块的 n 值
```

当 `tail_n` 不是 16 的倍数时（如 8, 4, 1），直接传入 Mmad 触发 AIC error：

```cpp
// 修复前 (ComputeL0c)
mmadParams.n = s2L0RealSize;  // 可能是 8, 4, 1 → AIC error!
Mmad(cL0, qL0Mx, kL0Mx, mmadParams);
```

### 修复方案

在 `ComputeL0c` 的 MXFP8 分支中，运行时对齐 n 并清零 L0B extra columns：

```cpp
if constexpr (IS_MXFP8) {
    uint64_t alignedN = CeilAlign(s2L0RealSize, (uint64_t)BLOCK_CUBE);
    if (alignedN > s2L0RealSize) {
        mmadParams.n = alignedN;
        // NZ layout: [k/D_BASIC_BLOCK, n/BLOCK_CUBE, D_BASIC_BLOCK, BLOCK_CUBE]
        // extra columns 在最后一个 NZ block 内连续，一次 Duplicate 清零
        uint64_t extraColStart = (s2L0RealSize / BLOCK_CUBE) * BLOCK_CUBE * D_BASIC_BLOCK;
        uint64_t extraColBytes = (alignedN - s2L0RealSize) * D_BASIC_BLOCK;
        LocalTensor<uint8_t> keyL0Bytes = keyL0_[...].template ReinterpretCast<uint8_t>();
        Duplicate(keyL0Bytes[extraColStart], (uint8_t)0, extraColBytes);
    }
    Mmad(cL0, qL0Mx, kL0Mx, mmadParams);
}
```

### 与 MLA Prolog 的对比

| 维度 | MLA Prolog | QLI V2 |
|------|-----------|--------|
| n 来源 | Tiling 阶段静态计算 | 运行时动态值 |
| 对齐保证 | `CalcSingleCoreN` 在 tiling 层保证 | **必须在 kernel 层处理** |
| 尾块风险 | 无（数学保证） | 有（actS2Size 可为任意值） |

MLA Prolog 通过 `CalcSingleCoreN(n, coreNum, alignNum)` 确保 `para.n` 是 `alignNum(≥16)` 的倍数，
`baseN` 硬编码为 64/128（均为 16 倍数），尾块 = `para.n - k × baseN` 也天然是 16 倍数。

### 检查要点
- MXFP8 路径下所有传入 `Mmad` 的 n 值必须检查是否 16 对齐
- Fixpipe 的 nSize 做 32B 对齐 `(n+7)>>3<<3`，通常已满足（8×4B=32B），但需验证
- 非 MXFP8 路径的普通 Mmad 无此限制

---

## 10.3 Fixpipe UB 写缓冲未排空导致跨核精度错误

### 问题现象
- 精度失败（结果不正确，非崩溃）
- 在 matmul 开头插入 `PipeBarrier<PIPE_ALL>()` 可恢复正确 → 指向流水线同步问题

### 根因分析

Cube 核的 Fixpipe 将 Mmad 结果从 L0C 写入 UB (`mm1ResUB_`) 后，
UB 写入可能还在 PIPE_FIX 的**写缓冲**中未提交到物理 UB。
此时 `CrossCoreSetFlag<PIPE_FIX>` 发出信号，Vector 核收到信号后
立即读取 `mm1ResUB_`，读到的是**上一轮残留数据**。

```
Cube (AIC)                              Vector (AIV)
─────────────                           ─────────────
Fixpipe → mm1ResUB_  (PIPE_FIX 写缓冲)
                                        等待信号...
CrossCoreSetFlag(CVEvent, PIPE_FIX) ──→ CrossCoreWaitFlag(CVEvent)
                                        读取 mm1ResUB_ ← 读到旧数据！
```

### 修复方案

在 `ComputeMm1` 的 S2 循环结束后、`CrossCoreSetFlag` 之前插入 `PipeBarrier<PIPE_FIX>()`：

```cpp
// s2BasicBlock_==128 路径
for (s2GmOffset ...) {
    // ... Fixp() 写 mm1ResUB_ ...
    keyL1BufIdx_++;
}
PipeBarrier<PIPE_FIX>();   // ← 新增：排空 PIPE_FIX 写缓冲

// s2BasicBlock_==256 路径
for (s2GmOffset ...) {
    // ... Fixp() 写 mm1ResUB_ ...
}
PipeBarrier<PIPE_FIX>();   // ← 新增：排空 PIPE_FIX 写缓冲

CrossCoreSetFlag<...>(CROSS_CV_EVENT + runInfo.loop % 2);  // 现在安全
```

### 为什么 Vector 侧不需要对称 PipeBarrier

Vector 核写出的数据走 **GM（全局内存）**，Cube 核不读取 Vector 写出的 GM 数据。
GM 写入通过硬件内存一致性模型保证可见性，无需额外 PipeBarrier。
只有 **UB 共享**（同一块 UB 被不同流水线/不同核访问）才需要 PipeBarrier。

### 性能影响
- PipeBarrier 在 S2 循环**外部**，每次 ComputeMm1 仅执行 1 次
- 此时最后一个 Fixpipe 已通过 FIX_M 同步链基本完成，实际 stall 极短
- 非 MXFP8 路径同样受益（此问题与数据类型无关）

### 检查要点
- **任何跨核共享 UB 的场景**，写方在发信号前必须确保写缓冲排空
- 判断方法：写操作和信号发送是否在同一 PIPE 上？如果是，需要 PipeBarrier
- 典型模式：`Fixpipe(UB)` → `CrossCoreSetFlag(PIPE_FIX)` 之间需要 `PipeBarrier<PIPE_FIX>()`

---

## 同步问题速查表

| 问题类型 | 现象 | 定位方法 | 修复模式 |
|---------|------|---------|---------|
| 提前返回缺同步 | 核卡死/hang | 检查所有 return 路径的 Event Set/Wait 配对 | 补齐缺失的 SetFlag/WaitFlag |
| Mmad n 不对齐 | AIC error | 检查 MXFP8 路径 n 是否 16 倍数 | CeilAlign + Duplicate 清零 L0B |
| Fixpipe 写缓冲 | 精度错误 | 插入 PipeBarrier 二分定位 | 在 CrossCoreSetFlag 前加 PipeBarrier |
| ping-pong 不同步 | 偶发数据错误 | 检查 Event ID 的 Set/Wait 计数 | 确保每个 Set 都有对应 Wait |
| UB 复用重叠 | return_value 开关导致索引变分数值越界 | 核算 resMm1UB_ 各子 buffer 地址范围 | 修正 scoreOutOffsetInU32 加入 tmpIndexLocal 大小 |

---

## 通用调试方法论

### 1. 核卡死问题
```
步骤：
1. 确认是 AIC 还是 AIV 核卡死（plog 日志）
2. 找到最后执行的 WaitFlag/CrossCoreWaitFlag
3. 检查对应的 SetFlag/CrossCoreSetFlag 是否在所有路径上都被执行
4. 特别关注提前返回（early return）和条件分支
```

### 2. 精度错误问题
```
步骤：
1. 在最外层插入 PipeBarrier<PIPE_ALL> 确认是否为流水线同步问题
2. 逐步缩小 PipeBarrier 范围（PIPE_FIX → PIPE_M → PIPE_V）
3. 定位到具体流水线后，检查该流水线的写操作与后续读操作之间是否有栅栏
4. 特别关注跨核共享的 UB buffer（如 mm1ResUB_）
```

### 3. AIC error 问题
```
步骤：
1. 检查 Mmad 参数 m/n/k 是否满足硬件对齐要求
2. MXFP8 (mmad_mx): m 对齐 16, n 对齐 16, k 对齐 32 (FP8_BLOCK_CUBE)
3. 普通 Mmad: m 对齐 16, n 无特殊要求, k 对齐 16
4. 检查 L0 buffer 的 LoadData 参数是否与 Mmad 参数一致
```

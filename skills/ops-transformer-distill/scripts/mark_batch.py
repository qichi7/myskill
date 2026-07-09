#!/usr/bin/env python3
"""阶段4c: 标记一个批次的合入记录为已完成

将批次内所有记录编号写入 batch_progress.json 的 completed_records 列表，
供断点续传判断从哪个批次继续。

用法:
    python3 -u mark_batch.py {output_dir} {batch_num}

输入:
    {output_dir}/03_knowledge/batch_{batch_num}/batch_plan.json
输出:
    {output_dir}/02_intermediate/batch_progress.json（更新）
    {output_dir}/03_knowledge/batch_{batch_num}/.DONE（创建）
"""
import sys
import os
import json

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
BATCH_NUM = int(sys.argv[2]) if len(sys.argv) > 2 else 1

BATCH_SIZE = 20
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "02_intermediate", "batch_progress.json")


def main():
    # 读取批次计划，获取本批次的记录范围
    batch_dir = os.path.join(OUTPUT_DIR, "03_knowledge", f"batch_{BATCH_NUM:02d}")
    plan_file = os.path.join(batch_dir, "batch_plan.json")

    if not os.path.exists(plan_file):
        print(f"[mark] 错误: 批次计划文件不存在: {plan_file}", flush=True)
        sys.exit(1)

    with open(plan_file, "r") as f:
        plan = json.load(f)

    record_range = plan.get("record_range", [])
    if not record_range:
        print(f"[mark] 错误: 批次计划中无记录范围", flush=True)
        sys.exit(1)

    start_num, end_num = record_range

    # 读取已有进度
    progress = {
        "batch_size": BATCH_SIZE,
        "completed_batches": [],
        "completed_records": [],
    }
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            progress = json.load(f)

    # 读取 merges.json 获取本批次所有记录编号
    merges_file = os.path.join(OUTPUT_DIR, "01_download", "merges.json")
    with open(merges_file, "r") as f:
        all_merges = json.load(f)

    start_idx = (BATCH_NUM - 1) * BATCH_SIZE
    end_idx = min(start_idx + BATCH_SIZE, len(all_merges))
    batch_numbers = [all_merges[i]["number"] for i in range(start_idx, end_idx)]

    # 更新进度
    if BATCH_NUM not in progress["completed_batches"]:
        progress["completed_batches"].append(BATCH_NUM)
        progress["completed_batches"].sort()

    for num in batch_numbers:
        if num not in progress["completed_records"]:
            progress["completed_records"].append(num)
    progress["completed_records"].sort()

    # 计算下一批次
    total_records = len(all_merges)
    completed_count = len(progress["completed_records"])
    next_batch = None
    if completed_count < total_records:
        next_batch = (completed_count // BATCH_SIZE) + 1
    progress["total_records"] = total_records
    progress["completed_count"] = completed_count
    progress["next_batch"] = next_batch

    # 保存
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

    # 创建 .DONE 标记
    done_file = os.path.join(batch_dir, ".DONE")
    open(done_file, "w").close()

    print(f"[mark] 批次 {BATCH_NUM} 已标记完成: {len(batch_numbers)} 条记录 (#{start_num}-#{end_num})", flush=True)
    print(f"  总进度: {completed_count}/{total_records}", flush=True)
    if next_batch:
        print(f"  下一批次: {next_batch}", flush=True)
    else:
        print(f"  全部记录已完成", flush=True)


if __name__ == "__main__":
    main()

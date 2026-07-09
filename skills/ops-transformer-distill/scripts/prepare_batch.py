#!/usr/bin/env python3
"""阶段4a: 准备一个批次的分片

取 20 条合入记录，按代码量均分给 5 个分片（子 agent）。
- 一条合入记录不可拆分给多个 agent
- 若两条记录修改了相同文件（有依赖），必须分到同一个 agent
- 按代码量（additions+deletions）均衡分配

用法:
    python3 -u prepare_batch.py {output_dir} {batch_num}

输入:
    {output_dir}/01_download/merges.json
    {output_dir}/02_intermediate/diffs.json
输出:
    {output_dir}/03_knowledge/batch_{batch_num}/shard_{1-5}.json
    {output_dir}/03_knowledge/batch_{batch_num}/batch_plan.json

⚠️ 分配算法:
1. 计算每条记录的代码量和修改文件集合
2. 用并查集合并有文件交集的记录（依赖合并）
3. 将合并后的组按代码量降序排列
4. 贪心分配：每次把组分配给当前总量最小的分片
"""
import sys
import os
import json

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
BATCH_NUM = int(sys.argv[2]) if len(sys.argv) > 2 else 1

BATCH_SIZE = 20
NUM_SHARDS = 5
PATCH_MAX_LINES = 100


def truncate_patch(patch_str, max_lines=PATCH_MAX_LINES):
    if not patch_str:
        return "", False
    lines = patch_str.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]), True
    return patch_str, False


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def main():
    merges_file = os.path.join(OUTPUT_DIR, "01_download", "merges.json")
    diffs_file = os.path.join(OUTPUT_DIR, "02_intermediate", "diffs.json")

    with open(merges_file, "r") as f:
        all_merges = json.load(f)
    with open(diffs_file, "r") as f:
        all_diffs = json.load(f)

    # 构建 diff 查找表
    diff_map = {}
    for d in all_diffs:
        key = (d["platform"], d["repo"], d["number"])
        diff_map[key] = d

    # 确定本批次的记录范围（按 merges.json 顺序，已按合入时间升序）
    start_idx = (BATCH_NUM - 1) * BATCH_SIZE
    end_idx = min(start_idx + BATCH_SIZE, len(all_merges))
    batch_merges = all_merges[start_idx:end_idx]

    if not batch_merges:
        print(f"[prepare] 批次 {BATCH_NUM}: 无记录可处理（已全部完成）", flush=True)
        return

    print(f"[prepare] 批次 {BATCH_NUM}: 记录 #{batch_merges[0]['number']}-#{batch_merges[-1]['number']}（{len(batch_merges)} 条）", flush=True)

    # 构建本批次记录的代码量和文件集合
    records = []
    for i, merge in enumerate(batch_merges):
        key = (merge["platform"], merge["repo"], merge["number"])
        diff = diff_map.get(key)
        if not diff or diff.get("error"):
            # 跳过无 diff 的记录，但仍计入批次
            records.append({
                "index": i,
                "number": merge["number"],
                "title": merge.get("title", ""),
                "html_url": merge.get("html_url", ""),
                "merged_at": merge.get("merged_at", ""),
                "code_volume": 0,
                "files_set": set(),
                "file_summaries": [],
                "has_diff": False,
            })
            continue

        files = diff.get("files", [])
        files_set = set(f.get("filename", "") for f in files if f.get("filename"))
        code_volume = diff.get("total_additions", 0) + diff.get("total_deletions", 0)

        file_summaries = []
        for f in files:
            patch, truncated = truncate_patch(f.get("patch", ""))
            file_summaries.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": patch,
                "patch_truncated": truncated,
            })

        records.append({
            "index": i,
            "number": merge["number"],
            "title": merge.get("title", ""),
            "html_url": merge.get("html_url", ""),
            "merged_at": merge.get("merged_at", ""),
            "code_volume": code_volume,
            "files_set": files_set,
            "file_summaries": file_summaries,
            "has_diff": True,
        })

    # ── 依赖合并：用并查集合并有文件交集的记录 ──
    uf = UnionFind(len(records))
    # 建立文件 → 记录索引列表 的映射
    file_to_records = {}
    for i, r in enumerate(records):
        for fname in r["files_set"]:
            file_to_records.setdefault(fname, []).append(i)

    # 合并有共同文件的记录
    for fname, indices in file_to_records.items():
        for j in range(1, len(indices)):
            uf.union(indices[0], indices[j])

    # 收集合并后的组
    groups_map = {}
    for i in range(len(records)):
        root = uf.find(i)
        groups_map.setdefault(root, []).append(i)

    groups = list(groups_map.values())

    # 计算每组的总代码量
    group_data = []
    for group_indices in groups:
        group_records = [records[i] for i in group_indices]
        total_volume = sum(r["code_volume"] for r in group_records)
        group_data.append({
            "indices": group_indices,
            "records": group_records,
            "total_volume": total_volume,
        })

    # 按代码量降序排列
    group_data.sort(key=lambda g: g["total_volume"], reverse=True)

    # ── 贪心分配：每次把组分配给当前总量最小的分片 ──
    shards = [[] for _ in range(NUM_SHARDS)]
    shard_volumes = [0] * NUM_SHARDS

    for gd in group_data:
        # 找当前总量最小的分片
        min_shard = min(range(NUM_SHARDS), key=lambda s: shard_volumes[s])
        shards[min_shard].extend(gd["records"])
        shard_volumes[min_shard] += gd["total_volume"]

    # ── 输出分片文件 ──
    batch_dir = os.path.join(OUTPUT_DIR, "03_knowledge", f"batch_{BATCH_NUM:02d}")
    os.makedirs(batch_dir, exist_ok=True)

    batch_plan = {
        "batch_num": BATCH_NUM,
        "record_range": [batch_merges[0]["number"], batch_merges[-1]["number"]],
        "record_count": len(batch_merges),
        "shards": [],
    }

    for s_idx in range(NUM_SHARDS):
        shard_records = shards[s_idx]
        if not shard_records:
            shard_records = []  # 空分片

        shard_data = []
        for r in shard_records:
            shard_data.append({
                "number": r["number"],
                "title": r["title"],
                "html_url": r["html_url"],
                "merged_at": r["merged_at"],
                "files": r["file_summaries"],
                "total_additions": sum(f["additions"] for f in r["file_summaries"]),
                "total_deletions": sum(f["deletions"] for f in r["file_summaries"]),
            })

        shard_file = os.path.join(batch_dir, f"shard_{s_idx + 1}.json")
        with open(shard_file, "w") as f:
            json.dump(shard_data, f, ensure_ascii=False, indent=2)

        batch_plan["shards"].append({
            "shard_num": s_idx + 1,
            "record_count": len(shard_data),
            "code_volume": shard_volumes[s_idx],
            "file": f"03_knowledge/batch_{BATCH_NUM:02d}/shard_{s_idx + 1}.json",
        })

        print(f"  分片{s_idx + 1}: {len(shard_data)} 条, 代码量 {shard_volumes[s_idx]}", flush=True)

    # 保存批次计划
    plan_file = os.path.join(batch_dir, "batch_plan.json")
    with open(plan_file, "w") as f:
        json.dump(batch_plan, f, ensure_ascii=False, indent=2)

    print(f"[prepare] 批次 {BATCH_NUM} 准备完成: {len(groups)} 个依赖组 → {NUM_SHARDS} 个分片", flush=True)
    print(f"  代码量分布: {shard_volumes}", flush=True)


if __name__ == "__main__":
    main()

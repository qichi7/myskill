#!/usr/bin/env python3
"""阶段5a: 按分类拆分片，生成紧凑摘要

将每个分类的合入记录拆为分片（上限 25 条 / 80 KB），生成 merge_summary_{N}.json
（patch 截断到 100 行，供 Task 读取做分析）+ shard_map.json（分片映射表）。

用法:
    python3 -u prepare_shards.py {output_dir}

输入:
    {output_dir}/02_intermediate/merge_classification.json
    {output_dir}/02_intermediate/diffs.json
输出:
    {output_dir}/02_intermediate/shard_map.json
    {output_dir}/03_knowledge/{分类}/merge_summary_{N}.json

⚠️ 关键经验（来自 code-commit-analyzer）:
- patch 截断到 100 行（原 diffs.json 是 500 行，subagent 读取会卡死）
- 单分片上限 25 条 / 80 KB，防止 Task prompt 过大
"""
import sys
import os
import json

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

MAX_RECORDS_PER_SHARD = 25  # 单分片合入记录数上限
MAX_BYTES_PER_SHARD = 80    # 单分片大小上限（KB）
PATCH_MAX_LINES = 100       # 紧凑摘要中 patch 截断行数


def truncate_patch(patch_str, max_lines=PATCH_MAX_LINES):
    """按行截断 patch"""
    if not patch_str:
        return "", False
    lines = patch_str.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]), True
    return patch_str, False


def main():
    classification_file = os.path.join(OUTPUT_DIR, "02_intermediate", "merge_classification.json")
    diffs_file = os.path.join(OUTPUT_DIR, "02_intermediate", "diffs.json")
    shard_map_file = os.path.join(OUTPUT_DIR, "02_intermediate", "shard_map.json")

    with open(classification_file, "r") as f:
        classification = json.load(f)
    with open(diffs_file, "r") as f:
        all_diffs = json.load(f)

    # 构建 diff 查找表: (platform, repo, number) → diff
    diff_map = {}
    for record in all_diffs:
        key = (record["platform"], record["repo"], record["number"])
        diff_map[key] = record

    shard_map = {}  # {分类: [{shard_num, record_count, file, done}]}
    knowledge_dir = os.path.join(OUTPUT_DIR, "03_knowledge")

    for category, record_list in classification.items():
        if not record_list:
            continue

        cat_dir = os.path.join(knowledge_dir, category)
        os.makedirs(cat_dir, exist_ok=True)

        # 检查该分类是否已完成（.DONE 存在）
        done_marker = os.path.join(cat_dir, ".DONE")
        is_done = os.path.exists(done_marker)

        shards = []
        shard_num = 1
        current_shard = []
        current_size = 0

        for record_info in record_list:
            key = (record_info["platform"], record_info["repo"], record_info["number"])
            record_diff = diff_map.get(key)

            if not record_diff:
                continue

            # 构建紧凑摘要（patch 截断到 100 行）
            file_summaries = []
            for f in record_diff.get("files", []):
                patch, truncated = truncate_patch(f.get("patch", ""))
                file_summaries.append({
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "patch": patch,
                    "patch_truncated": truncated,
                })

            record_summary = {
                "number": record_info["number"],
                "title": record_info["title"],
                "html_url": record_info["html_url"],
                "state": record_info["state"],
                "merged_at": record_info["merged_at"],
                "files": file_summaries,
                "total_additions": record_diff.get("total_additions", 0),
                "total_deletions": record_diff.get("total_deletions", 0),
            }

            # 估算大小（序列化后的字节数）
            record_size = len(json.dumps(record_summary, ensure_ascii=False).encode("utf-8"))

            # 检查是否需要新分片
            if (len(current_shard) >= MAX_RECORDS_PER_SHARD or
                    current_size + record_size > MAX_BYTES_PER_SHARD * 1024):
                # 保存当前分片
                shard_file = os.path.join(cat_dir, f"merge_summary_{shard_num}.json")
                with open(shard_file, "w") as f:
                    json.dump(current_shard, f, ensure_ascii=False, indent=2)

                shards.append({
                    "shard_num": shard_num,
                    "record_count": len(current_shard),
                    "file": f"03_knowledge/{category}/merge_summary_{shard_num}.json",
                    "done": is_done,
                })
                print(f"  {category} 分片{shard_num}: {len(current_shard)} 条, {current_size//1024}KB", flush=True)

                shard_num += 1
                current_shard = []
                current_size = 0

            current_shard.append(record_summary)
            current_size += record_size

        # 保存最后一个分片
        if current_shard:
            shard_file = os.path.join(cat_dir, f"merge_summary_{shard_num}.json")
            with open(shard_file, "w") as f:
                json.dump(current_shard, f, ensure_ascii=False, indent=2)

            shards.append({
                "shard_num": shard_num,
                "record_count": len(current_shard),
                "file": f"03_knowledge/{category}/merge_summary_{shard_num}.json",
                "done": is_done,
            })
            print(f"  {category} 分片{shard_num}: {len(current_shard)} 条, {current_size//1024}KB", flush=True)

        if shards:
            shard_map[category] = shards

    # 保存 shard_map.json
    with open(shard_map_file, "w") as f:
        json.dump(shard_map, f, ensure_ascii=False, indent=2)

    # 统计
    total_shards = sum(len(v) for v in shard_map.values())
    done_shards = sum(1 for v in shard_map.values() for s in v if s["done"])
    pending_shards = total_shards - done_shards

    print(f"\n=== 分片完成 ===", flush=True)
    print(f"  总分片数: {total_shards}", flush=True)
    print(f"  已完成: {done_shards}", flush=True)
    print(f"  待分析: {pending_shards}", flush=True)
    print(f"  分片映射已保存到 {shard_map_file}", flush=True)


if __name__ == "__main__":
    main()

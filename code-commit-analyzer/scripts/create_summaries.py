#!/usr/bin/env python3
"""阶段4 Step 4: 为每个分类创建紧凑的分析摘要文件（供 subagent 读取）

将 pr_diffs.json 按分类拆分为紧凑摘要，每个文件的 patch 截断到 100 行，
避免 subagent 读取过大文件导致 think 卡死。

用法:
    python3 -u create_summaries.py {output_dir}

输入:
    {output_dir}/02_intermediate/pr_classification.json
    {output_dir}/02_intermediate/pr_diffs.json
    {output_dir}/01_download/all_prs.json

输出:
    {output_dir}/03_knowledge/{category}/pr_summary.json

⚠️ 关键设计:
- patch 截断到 100 行（原 500 行的 pr_diffs.json 太大，subagent 读取会卡死）
- 日期从 all_prs.json 获取（pr_diffs.json 不含完整日期字段）
- 状态标签中文化（已合入/未合入-open/未合入-closed）
"""
import json
import os
import sys

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

CAT_NAMES = {
    "operator_dev": "算子开发", "bugfix": "Bug 修复", "feature": "特性功能",
    "perf_opt": "性能优化", "test": "测试", "code_quality": "代码质量",
    "doc": "文档", "infra": "基础设施", "tool": "工具",
}


def main():
    with open(os.path.join(OUTPUT_DIR, "02_intermediate", "pr_classification.json")) as f:
        classification = json.load(f)
    with open(os.path.join(OUTPUT_DIR, "02_intermediate", "pr_diffs.json")) as f:
        diffs = json.load(f)
    with open(os.path.join(OUTPUT_DIR, "01_download", "all_prs.json")) as f:
        all_prs = json.load(f)

    diff_map = {(r["platform"], r["repo"], r["number"]): r for r in diffs}
    prs_map = {(pr["platform"], pr["repo"], pr["number"]): pr for pr in all_prs}

    for cat, prs in classification.items():
        summaries = []
        for pr in prs:
            key = (pr["platform"], pr["repo"], pr["number"])
            diff = diff_map.get(key, {})
            full_pr = prs_map.get(key, {})

            # 截断 patch 到 100 行
            file_summaries = []
            for f in diff.get("files", []):
                patch = f.get("patch", "")
                patch_trunc = False
                if patch:
                    lines = patch.split("\n")
                    if len(lines) > 100:
                        patch = "\n".join(lines[:100])
                        patch_trunc = True
                file_summaries.append({
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "patch": patch,
                    "patch_truncated": patch_trunc or f.get("patch_truncated", False),
                })

            state = pr.get("state", "")
            state_label = {"merged": "已合入", "open": "未合入-open",
                           "closed": "未合入-closed"}.get(state, state)
            date = full_pr.get("merged_at") or full_pr.get("updated_at") or full_pr.get("created_at") or ""

            summaries.append({
                "platform": pr["platform"], "repo": pr["repo"], "number": pr["number"],
                "title": pr.get("title", ""), "state": state_label,
                "html_url": pr.get("html_url", ""), "date": date[:10] if date else "",
                "total_additions": diff.get("total_additions", 0),
                "total_deletions": diff.get("total_deletions", 0),
                "files": file_summaries,
            })

        summary_file = os.path.join(OUTPUT_DIR, "03_knowledge", cat, "pr_summary.json")
        with open(summary_file, "w") as f:
            json.dump(summaries, f, ensure_ascii=False, indent=2)
        print(f"{cat} ({CAT_NAMES.get(cat, cat)}): {len(summaries)} PRs, "
              f"{os.path.getsize(summary_file)/1024:.0f} KB", flush=True)


if __name__ == "__main__":
    main()

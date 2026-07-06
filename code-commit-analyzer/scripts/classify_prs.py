#!/usr/bin/env python3
"""阶段4 Step 1-3: PR 分类 + 生成索引 + 创建分类目录

分类依据以 PR 标题为主、patch 内容为辅（仅看标题无法判断时参考文件路径）。
当一个 PR 同时匹配多个分类时，按优先级取最高的。

用法:
    python3 -u classify_prs.py {output_dir}

输入:
    {output_dir}/01_download/all_prs.json
    {output_dir}/02_intermediate/pr_diffs.json

输出:
    {output_dir}/02_intermediate/pr_index.md         — PR 分类索引表
    {output_dir}/02_intermediate/pr_classification.json — 分类结果（供后续分析）
    {output_dir}/03_knowledge/{category}/            — 分类目录

分类优先级:
    operator_dev > bugfix > feature > perf_opt > test > code_quality > doc > infra > tool
"""
import json
import os
import sys
from collections import defaultdict

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

# 分类关键词映射（按优先级排序，列表顺序即优先级）
CATEGORIES = [
    ("operator_dev", ["flash attention", "flashattention", "mxfp8", "qsmla", "新增算子", "新算子", "算子实现", "kernel实现", "内核实现", "attention", "quant", "量化", "matmul", "softmax", "layernorm", "addcmul", "指数运算", "prefix", "编码", "解码", "encoder", "decoder"]),
    ("bugfix", ["编译", "卡死", "scale", "修复", "fix", "异常", "bug", "错误", "越界", "溢出", "段错误", "core", "死锁", "race", "竞争", "内存泄漏", "对齐", "校验", "崩溃", "fault", "panic", "abort"]),
    ("feature", ["anti-sparse", "antisparse", "rope", "mask", "规格", "特性", "支持", "新增功能", "下沉", "接口", "适配", "兼容", "特性支持", "扩展"]),
    ("perf_opt", ["tiling", "ub", "l1", "vec", "性能", "优化", "perf", "优化性能", "流水线", "pipeline", "并行", "async", "同步", "tblock", "block", "内存", "buffer", "流水", "双缓冲", "doublebuffer", "latency", "吞吐"]),
    ("test", ["pytest", "ut", "golden", "覆盖率", "测试", "test", "st", "用例", "验证", "断言", "mock", "fixture"]),
    ("code_quality", ["告警", "红线", "注释", "检视", "整改", "删除", "开源", "清理", "重构", "规范", "lint", "warning", "code review", "format", "格式化", "dead code", "无用代码"]),
    ("doc", ["readme", "设计文档", "接口文档", "文档", "doc", "说明", "guide", "注释补充"]),
    ("infra", ["ci", "子仓", "构建脚本", "cmake", "makefile", "build", "编译脚本", "pipeline", "jenkins", "docker", "环境"]),
    ("tool", ["脚本", "辅助工具", "tool", "工具", "util", "helper", "converter", "生成器"]),
]

CATEGORY_NAMES = {
    "operator_dev": "算子开发", "bugfix": "Bug 修复", "feature": "特性功能",
    "perf_opt": "性能优化", "test": "测试", "code_quality": "代码质量",
    "doc": "文档", "infra": "基础设施", "tool": "工具",
}

PRIORITY = [cat for cat, _ in CATEGORIES]


def classify_pr(title, files_info=None):
    """根据标题关键词分类 PR（优先级匹配）

    注意：分类关键词不要从 patch 内容中全文匹配（patch 中可能包含各种无关关键词），
    应优先根据 PR 标题中的关键词判断，仅在标题不含明确关键词时参考修改的文件路径。
    """
    title_lower = title.lower()
    matched = []
    for cat, keywords in CATEGORIES:
        for kw in keywords:
            if kw.lower() in title_lower:
                matched.append(cat)
                break

    if not matched and files_info:
        file_paths = " ".join(f.get("filename", "") for f in files_info).lower()
        for cat, keywords in CATEGORIES:
            for kw in keywords:
                if kw.lower() in file_paths:
                    matched.append(cat)
                    break

    if not matched:
        return "code_quality"  # 默认分类

    for cat in PRIORITY:
        if cat in matched:
            return cat
    return matched[0]


def main():
    with open(os.path.join(OUTPUT_DIR, "01_download", "all_prs.json")) as f:
        all_prs = json.load(f)
    with open(os.path.join(OUTPUT_DIR, "02_intermediate", "pr_diffs.json")) as f:
        all_diffs = json.load(f)

    diff_map = {(r["platform"], r["repo"], r["number"]): r for r in all_diffs}

    classified = defaultdict(list)
    pr_index_entries = []

    for pr in all_prs:
        key = (pr["platform"], pr["repo"], pr["number"])
        diff = diff_map.get(key, {})
        category = classify_pr(pr.get("title", ""), diff.get("files", []))
        classified[category].append(pr)

        pr_date = pr.get("merged_at") or pr.get("updated_at") or pr.get("created_at") or ""
        state_label = {"merged": "已合入", "open": "未合入-open", "closed": "未合入-closed"}.get(
            pr.get("state", ""), pr.get("state", ""))
        pr_index_entries.append({
            "number": pr["number"], "platform": pr["platform"], "repo": pr["repo"],
            "title": pr.get("title", ""), "date": pr_date[:10] if pr_date else "",
            "state": state_label, "category": category,
            "category_name": CATEGORY_NAMES[category], "html_url": pr.get("html_url", ""),
        })

    print("=== PR 分类统计 ===", flush=True)
    for cat in PRIORITY:
        if cat in classified:
            print(f"  {CATEGORY_NAMES[cat]} ({cat}): {len(classified[cat])} 个 PR", flush=True)

    # 创建分类目录
    for cat in PRIORITY:
        if cat in classified:
            os.makedirs(os.path.join(OUTPUT_DIR, "03_knowledge", cat), exist_ok=True)

    # 生成 pr_index.md
    lines = ["# PR 分类索引表\n", f"总计 {len(all_prs)} 个 PR\n"]
    lines.append("| 编号 | 标题 | 日期 | 状态 | 分类 | 链接 |")
    lines.append("|------|------|------|------|------|------|")
    pr_index_entries.sort(key=lambda x: (
        PRIORITY.index(x["category"]) if x["category"] in PRIORITY else 99,
        x["repo"], -x["number"]))
    for i, e in enumerate(pr_index_entries, 1):
        title = e["title"][:50].replace("|", "\\|")
        lines.append(f"| {i} | {title} | {e['date']} | {e['state']} | {e['category_name']} | "
                     f"[{e['platform']}:{e['repo']}#{e['number']}]({e['html_url']}) |")

    index_file = os.path.join(OUTPUT_DIR, "02_intermediate", "pr_index.md")
    with open(index_file, "w") as f:
        f.write("\n".join(lines))
    print(f"\n索引已保存到 {index_file}", flush=True)

    # 保存分类结果
    result = {cat: [{"platform": p["platform"], "repo": p["repo"], "number": p["number"],
                     "title": p.get("title", ""), "state": p.get("state", ""),
                     "html_url": p.get("html_url", "")} for p in prs]
              for cat, prs in classified.items()}
    cls_file = os.path.join(OUTPUT_DIR, "02_intermediate", "pr_classification.json")
    with open(cls_file, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"分类结果已保存到 {cls_file}", flush=True)


if __name__ == "__main__":
    main()

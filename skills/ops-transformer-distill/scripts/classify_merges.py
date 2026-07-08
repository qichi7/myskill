#!/usr/bin/env python3
"""阶段4: 按关键词对合入记录分类

按优先级匹配关键词（标题优先，文件路径兜底），输出分类结果 + 创建分类目录。

用法:
    python3 -u classify_merges.py {output_dir}

输入:
    {output_dir}/02_intermediate/diffs.json
输出:
    {output_dir}/02_intermediate/merge_classification.json
    {output_dir}/03_knowledge/{分类}/ 目录

分类优先级（降序）:
    功能实现 > 性能优化 > 精度修复 > Bug修复 > 重构 > 测试 > 文档 > 基础设施
"""
import sys
import os
import json

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

# 分类定义（优先级降序，索引越小优先级越高）
CATEGORIES = [
    ("功能实现", [
        "flash attention", "mxfp8", "qsmla", "attention", "quant", "matmul",
        "softmax", "新增", "支持", "实现", "add", "support", "implement",
        "feature", "新增算子", "kernel", "operator",
    ]),
    ("性能优化", [
        "tiling", "ub", "l1", "vec", "性能", "流水线", "pipeline", "优化",
        "cache", "分块", "perf", "performance", "optimize", "加速", "带宽",
        "双缓冲", "double buffer", "ping", "pong", "bank",
    ]),
    ("精度修复", [
        "精度", "precision", "atol", "rtol", "数值", "scale", "误差",
        "对齐", "accuracy", "cast", "转换", "饱和", "saturate",
    ]),
    ("Bug修复", [
        "编译", "卡死", "修复", "fix", "异常", "bug", "越界", "溢出",
        "段错误", "死锁", "race", "crash", "error", "fail", "fault",
        "空指针", "null", "边界", "boundary",
    ]),
    ("重构", [
        "重构", "refactor", "抽取", "合并", "清理", "移除", "统一",
        "simplify", "remove", "merge", "cleanup",
    ]),
    ("测试", [
        "测试", "test", "ut", "st", "用例", "覆盖率", "pytest",
        "case", "assert", "验证", "verify",
    ]),
    ("文档", [
        "文档", "doc", "readme", "注释", "comment", "说明",
        "guide", "tutorial",
    ]),
    ("基础设施", [
        "ci", "构建脚本", "cmake", "makefile", "安装", "依赖", "infra",
        "build", "deploy", "config", "环境",
    ]),
]

DEFAULT_CATEGORY = "功能实现"  # 算子仓绝大多数合入记录属于功能


def classify_record(title, files_info=None):
    """对单条合入记录分类

    1. 标题转小写，遍历分类，每个分类匹配任一关键词就加入 matched
    2. 标题无匹配时才参考 files_info（文件路径拼接字符串）
    3. 无任何匹配 → 默认分类
    4. 按 PRIORITY 顺序取 matched 中优先级最高的
    """
    title_lower = title.lower()
    matched = []

    for cat_name, keywords in CATEGORIES:
        for kw in keywords:
            if kw.lower() in title_lower:
                matched.append(cat_name)
                break

    # 标题无匹配时，参考文件路径
    if not matched and files_info:
        for cat_name, keywords in CATEGORIES:
            for kw in keywords:
                if kw.lower() in files_info.lower():
                    matched.append(cat_name)
                    break

    if not matched:
        return DEFAULT_CATEGORY

    # 按优先级取第一个（CATEGORIES 顺序即优先级）
    for cat_name, _ in CATEGORIES:
        if cat_name in matched:
            return cat_name
    return DEFAULT_CATEGORY


def main():
    diffs_file = os.path.join(OUTPUT_DIR, "02_intermediate", "diffs.json")
    output_file = os.path.join(OUTPUT_DIR, "02_intermediate", "merge_classification.json")

    with open(diffs_file, "r") as f:
        all_diffs = json.load(f)

    # 分类
    classification = {cat: [] for cat, _ in CATEGORIES}

    for record in all_diffs:
        title = record.get("title", "")
        files = record.get("files", [])
        files_info = " ".join(f.get("filename", "") for f in files) if files else None

        category = classify_record(title, files_info)

        classification[category].append({
            "platform": record.get("platform", "gitcode"),
            "repo": record.get("repo", ""),
            "number": record.get("number", 0),
            "title": title,
            "state": record.get("state", ""),
            "html_url": record.get("html_url", ""),
            "merged_at": record.get("merged_at", ""),
            "file_count": len(files),
            "total_additions": record.get("total_additions", 0),
            "total_deletions": record.get("total_deletions", 0),
        })

    # 创建分类目录
    for cat_name, _ in CATEGORIES:
        cat_dir = os.path.join(OUTPUT_DIR, "03_knowledge", cat_name)
        os.makedirs(cat_dir, exist_ok=True)

    # 保存分类结果
    with open(output_file, "w") as f:
        json.dump(classification, f, ensure_ascii=False, indent=2)

    # 打印统计
    print(f"=== 分类完成 ===", flush=True)
    for cat_name, _ in CATEGORIES:
        count = len(classification[cat_name])
        print(f"  {cat_name}: {count} 条合入记录", flush=True)
    print(f"\n分类结果已保存到 {output_file}", flush=True)


if __name__ == "__main__":
    main()

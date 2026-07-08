#!/usr/bin/env python3
"""阶段5c: 合并分片分析为完整 analysis.md

将某分类下所有 analysis_part{N}.md 合并为完整 analysis.md，
添加 header/summary，删除 part 文件，创建 .DONE 标记。

用法:
    python3 -u merge_analysis.py {output_dir} {category}

输入:
    {output_dir}/03_knowledge/{category}/analysis_part*.md
输出:
    {output_dir}/03_knowledge/{category}/analysis.md
    {output_dir}/03_knowledge/{category}/.DONE
"""
import sys
import os
import glob

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
CATEGORY = sys.argv[2] if len(sys.argv) > 2 else ""

if not CATEGORY:
    print("用法: python3 merge_analysis.py {output_dir} {category}", flush=True)
    sys.exit(1)

CAT_DIR = os.path.join(OUTPUT_DIR, "03_knowledge", CATEGORY)


def extract_record_analysis(text):
    """从 part 文件中提取 '## 逐条合入记录代码级分析' 标记之后的内容

    各 part 文件可能包含重复的标题和技术能力点，只保留逐条分析部分。
    """
    marker = "## 逐条合入记录代码级分析"
    idx = text.find(marker)
    if idx >= 0:
        return text[idx:].strip()
    # 无标记则返回全文（去掉首尾空行）
    return text.strip()


def main():
    if not os.path.isdir(CAT_DIR):
        print(f"[ERROR] 分类目录不存在: {CAT_DIR}", flush=True)
        sys.exit(1)

    # 查找所有 part 文件
    part_files = sorted(glob.glob(os.path.join(CAT_DIR, "analysis_part*.md")))

    if not part_files:
        print(f"[WARN] 无 analysis_part*.md 文件: {CATEGORY}", flush=True)
        # 检查是否已有 analysis.md
        analysis_file = os.path.join(CAT_DIR, "analysis.md")
        if os.path.exists(analysis_file):
            print(f"  analysis.md 已存在，直接标记 .DONE", flush=True)
            done_marker = os.path.join(CAT_DIR, ".DONE")
            open(done_marker, "w").close()
            return
        sys.exit(1)

    print(f"=== 合并 {CATEGORY}: {len(part_files)} 个分片 ===", flush=True)

    # 提取各 part 的逐条分析内容
    record_sections = []
    for pf in part_files:
        with open(pf, "r") as f:
            text = f.read()
        section = extract_record_analysis(text)
        if section:
            record_sections.append(section)
        print(f"  读取 {os.path.basename(pf)}: {len(text)} 字符", flush=True)

    # 生成 header
    header = f"# {CATEGORY} — 代码级蒸馏分析\n\n"
    header += f"本分类共 {len(part_files)} 个分片，以下为逐条合入记录代码级分析。\n\n"

    # 生成 summary（最小化版本）
    summary = "\n\n---\n\n## 技术能力点总结\n\n"
    summary += "> 请根据上方逐条合入记录分析，提炼本分类的核心技术能力点。\n"

    # 合并
    merged = header + "\n\n".join(record_sections) + summary

    # 写入 analysis.md
    analysis_file = os.path.join(CAT_DIR, "analysis.md")
    with open(analysis_file, "w") as f:
        f.write(merged)
    print(f"  已生成 {analysis_file} ({len(merged)} 字符)", flush=True)

    # 创建 .DONE 标记
    done_marker = os.path.join(CAT_DIR, ".DONE")
    open(done_marker, "w").close()
    print(f"  已创建 .DONE 标记", flush=True)

    # 删除 part 文件
    for pf in part_files:
        os.remove(pf)
    print(f"  已删除 {len(part_files)} 个 part 文件", flush=True)

    print(f"=== {CATEGORY} 合并完成 ===\n", flush=True)


if __name__ == "__main__":
    main()

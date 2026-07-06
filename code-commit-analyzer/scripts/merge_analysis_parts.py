#!/usr/bin/env python3
"""阶段4: 合并分类的 analysis_part{N}.md 为完整 analysis.md

当某分类的 PR 数 > 25 时，阶段4会拆分为多个 subagent 并行处理，各输出
analysis_part{N}.md。本脚本负责合并为完整的 analysis.md，添加统一的
标题、技术能力点和分类总结，删除 part 文件，创建 .DONE 标记。

用法:
    python3 -u merge_analysis_parts.py {output_dir} {category} [header_md] [summary_md]

参数:
    output_dir   — 输出目录
    category     — 分类目录名（如 operator_dev）
    header_md    — 可选：自定义 header 内容的 markdown 文件路径
    summary_md   — 可选：自定义 summary 内容的 markdown 文件路径

若无 header_md/summary_md 参数，则生成最小化的 header 和 summary。

⚠️ 使用场景:
- 阶段4大分类拆分批次后的合并（PR 数 > 25 的分类）
- 合并后删除 analysis_part{N}.md，创建 .DONE 标记
"""
import os
import sys
import glob

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
CATEGORY = sys.argv[2] if len(sys.argv) > 2 else "operator_dev"
HEADER_FILE = sys.argv[3] if len(sys.argv) > 3 else None
SUMMARY_FILE = sys.argv[4] if len(sys.argv) > 4 else None

CAT_DIR = os.path.join(OUTPUT_DIR, "03_knowledge", CATEGORY)


def extract_pr_analysis(text):
    """提取 '## 逐 PR 代码级分析' 之后的内容（去掉各 part 的标题和技术能力点）"""
    marker = "## 逐 PR 代码级分析"
    idx = text.find(marker)
    return text[idx:] if idx != -1 else text


def main():
    # 查找所有 part 文件
    part_files = sorted(glob.glob(os.path.join(CAT_DIR, "analysis_part*.md")))
    if not part_files:
        print(f"未找到 {CAT_DIR}/analysis_part*.md 文件", flush=True)
        sys.exit(1)

    print(f"找到 {len(part_files)} 个 part 文件: {[os.path.basename(p) for p in part_files]}", flush=True)

    # 读取并提取各 part 的 PR 分析部分
    pr_sections = []
    for pf in part_files:
        with open(pf) as f:
            content = f.read()
        pr_sections.append(extract_pr_analysis(content))

    # 读取自定义 header/summary 或生成最小化版本
    if HEADER_FILE and os.path.exists(HEADER_FILE):
        with open(HEADER_FILE) as f:
            header = f.read()
    else:
        header = f"# {CATEGORY} — 代码级深度分析\n\n## 技术能力点\n\n（见各 PR 分析）\n\n---\n\n"

    if SUMMARY_FILE and os.path.exists(SUMMARY_FILE):
        with open(SUMMARY_FILE) as f:
            summary = f.read()
    else:
        summary = "\n---\n\n## 分类总结\n\n（见各 PR 分析）\n"

    # 合并
    full_content = header + "\n\n".join(pr_sections) + summary

    # 写入 analysis.md
    analysis_file = os.path.join(CAT_DIR, "analysis.md")
    with open(analysis_file, "w") as f:
        f.write(full_content)
    print(f"已生成 {analysis_file}，大小: {len(full_content)} 字符", flush=True)

    # 创建 .DONE 标记
    with open(os.path.join(CAT_DIR, ".DONE"), "w") as f:
        pass
    print("已创建 .DONE 标记", flush=True)

    # 删除 part 文件
    for pf in part_files:
        os.remove(pf)
        print(f"已删除 {os.path.basename(pf)}", flush=True)


if __name__ == "__main__":
    main()

---
name: code-commit-reviewer
display_name: 审核skill
description: PR 知识提炼产出检视技能。对 code-commit-analyzer 技能的输出目录进行结构化审查，验证目录合规性、两份最终文件的内容质量（knowledge_detail.md / resume_skills.md）、中间产物的完整性和代码级分析深度。触发：用户需要对 code-commit-analyzer 产出的结果做质量把关时。
---

# PR 知识提炼产出检视技能

## 一、概述

本技能用于检视 `code-commit-analyzer` 技能的输出目录，验证其产出是否满足规范要求。检视维度覆盖目录结构、内容质量、代码分析深度和断点续传标记。

**输入**：`code-commit-analyzer` 的输出目录路径（即 `{output_dir}`）。

**输出**：检视报告写入 `{output_dir}/04_review/review_report.md`。`04_review/` 目录由本技能自动创建，与被检视目录的编号体系（01/02/03）保持一致。

---

## 二、检视维度与检查项

### 维度 A：目录结构合规性

| 编号 | 检查项 | 通过条件 | 严重程度 | 检测方式 |
|------|--------|---------|---------|---------|
| A-1 | 顶层文件合规 | `{output_dir}/` 下**仅**存在 `knowledge_detail.md` 和 `resume_skills.md` 两个文件（不限扩展名），且二者均存在且非空 | 严重 | 自动 |
| A-2 | 子目录存在性 | `01_download/`、`02_intermediate/`、`03_knowledge/` 三个子目录均存在 | 严重 | 自动 |
| A-3 | 阶段标记完整性 | `02_intermediate/.PHASE_1` ~ `.PHASE_5` 共 5 个标记文件均存在 | 重要 | 自动 |
| A-4 | 下载数据存在且有效 | `01_download/all_prs.json` 存在、非空且为合法 JSON（含有效 PR 数组，非 `{}` 或 `[]`） | 重要 | 自动 |
| A-5 | diff 数据存在且有效 | `02_intermediate/pr_diffs.json` 存在、非空且为合法 JSON（含有效 PR 数组，非 `{}` 或 `[]`） | 重要 | 自动 |
| A-6 | PR 索引表存在 | `02_intermediate/pr_index.md` 存在且非空 | 一般 | 自动 |
| A-7 | 错误日志规范 | 若 `02_intermediate/errors.log` 存在且非空：(1) 统计 **ERROR 级别**记录条数作为遗漏项数量（非总行数），(2) 检查是否仅含 ERROR/WARNING 级别（不应含 INFO），(3) 提示用户存在遗漏项 | 重要 | 自动 |
| A-8 | 令牌泄露检查 | 扫描所有输出文件（`knowledge_detail.md`、`resume_skills.md`、`errors.log`、`pr_diffs.json`），检测是否含疑似令牌（40 位 hex、`access_token=` 前缀等模式） | 重要 | 自动 |

**A-1 检查方法**：
```bash
# 检查所有类型多余文件（不仅是 .md）
extra=$(find "{output_dir}" -maxdepth 1 -type f \
  ! -name 'knowledge_detail.md' ! -name 'resume_skills.md' 2>/dev/null)
[ -z "$extra" ] || echo "违规: 顶层存在多余文件: $extra"

# 检查两个目标文件存在且非空
for f in knowledge_detail.md resume_skills.md; do
  [ -s "{output_dir}/$f" ] || echo "违规: $f 不存在或为空"
done
```

**A-4/A-5 JSON 有效性检查**：
```bash
# 验证 JSON 格式合法且含有效 PR 数组
python3 -c "
import json, sys
with open('{output_dir}/01_download/all_prs.json') as f:
    data = json.load(f)
assert isinstance(data, list), 'not a list'
assert len(data) > 0, 'empty list'
# 检查每条 PR 记录的必填字段
required_fields = ['html_url', 'title', 'number', 'state']
for i, pr in enumerate(data):
    missing = [f for f in required_fields if f not in pr]
    if missing:
        print(f'PR #{i}: 缺少字段 {missing}')
        sys.exit(1)
print(f'OK: {len(data)} PRs, 字段完整')
"
```

**A-7 errors.log 级别规范检查**：
```bash
# 检查 errors.log 中仅含 ERROR/WARNING，统计 ERROR 级别条数
if [ -f "{output_dir}/02_intermediate/errors.log" ] && [ -s "{output_dir}/02_intermediate/errors.log" ]; then
  # 统计 ERROR 级别行数
  error_count=$(grep -c '\[ERROR\]' "{output_dir}/02_intermediate/errors.log" 2>/dev/null || echo 0)
  # 检查是否有非 ERROR/WARNING 的行
  illegal=$(grep -v -E '\[ERROR\]|\[WARNING\]|^\s*$' "{output_dir}/02_intermediate/errors.log" 2>/dev/null)
  if [ -n "$illegal" ]; then
    echo "违规: errors.log 含非 ERROR/WARNING 级别日志（如 INFO）"
    echo "$illegal"
  fi
  echo "遗漏项数量（ERROR 级别）: $error_count"
fi
```

**A-8 令牌泄露检查**：
```bash
# 扫描输出文件中的疑似令牌
python3 -c "
import re, sys, os

output_dir = '{output_dir}'
files_to_check = ['knowledge_detail.md', 'resume_skills.md']
optional_files = ['02_intermediate/errors.log', '02_intermediate/pr_diffs.json']

for f in files_to_check:
    path = os.path.join(output_dir, f)
    if not os.path.exists(path):
        continue
    with open(path, errors='ignore') as fh:
        content = fh.read()
    # 40 位 hex 令牌
    if re.search(r'\b[a-f0-9]{40}\b', content, re.IGNORECASE):
        print(f'警告: {f} 含疑似 40 位 hex 令牌')
        sys.exit(1)
    # access_token 赋值
    if re.search(r'access_token\s*[=:]\s*\S+', content):
        print(f'警告: {f} 含疑似 access_token')
        sys.exit(1)

for f in optional_files:
    path = os.path.join(output_dir, f)
    if not os.path.exists(path):
        continue
    with open(path, errors='ignore') as fh:
        content = fh.read()
    if re.search(r'\b[a-f0-9]{40}\b', content, re.IGNORECASE):
        print(f'警告: {f} 含疑似 40 位 hex 令牌')
        sys.exit(1)
    if re.search(r'access_token\s*[=:]\s*\S+', content):
        print(f'警告: {f} 含疑似 access_token')
        sys.exit(1)

print('OK: 未检测到令牌泄露')
"
```

---

### 维度 B：knowledge_detail.md 内容质量

| 编号 | 检查项 | 通过条件 | 严重程度 | 检测方式 |
|------|--------|---------|---------|---------|
| B-1 | 分类覆盖 | 按 9 个分类组织内容，**全部覆盖** `03_knowledge/` 中有 `analysis.md` 的分类，无遗漏 | 严重 | 半自动 |
| B-2 | 每个分类含三大模块 | 每个分类章节包含「技术能力点」「逐 PR 代码级分析」「分类总结」三个子模块 | 重要 | 半自动 |
| B-3 | PR 链接格式 | 所有 PR 引用为完整 URL，且按平台使用正确路径格式：Gitee 社区版 `gitee.com/{o}/{r}/pulls/{n}`，Gitee 企业版 `e.gitee.com/{p}/code/pulls/{n}`，GitCode `gitcode.com/{o}/{r}/merge_requests/{n}`，非 `#xxx` 简写 | 严重 | 自动 |
| B-4 | PR 信息完整性 | 每个 PR 条目包含：链接、标题、日期（已合入取合入日期，未合入取创建/更新日期）、合入状态 | 重要 | 半自动 |
| B-5 | 代码级分析深度 | 至少 80% 的 PR 条目包含具体函数名/代码块引用，非仅 PR 标题或 commit message 概括（允许 ≤20% 的 PR 因 GitCode 无 patch 等原因仅做文件级统计，需标注原因） | 严重 | 半自动 |
| B-6 | 代码级分析标志检测 | 逐条检查 PR 分析内容，标记仅使用 commit message 概括（无代码行级细节）的 PR | 严重 | 半自动 |
| B-7 | 末尾总结 | 文末包含「核心技术能力总结」和「可复用经验」两个章节，且章节内容非空 | 一般 | 半自动 |
| B-8 | 无占位符内容 | 全文不含 "TODO"、"待补充"、"占位"、"xxx"、"TBD" 等未完成标记 | 重要 | 自动 |
| B-9 | PR 总数交叉验证 | `knowledge_detail.md` 中的 PR 条目总数（按唯一 PR URL 计数）与 `all_prs.json` 的 PR 数一致（允许 ±1 偏差用于说明性文字中的非正式引用） | 重要 | 自动 |
| B-10 | PR 合入状态标注 | `knowledge_detail.md` 中每个 PR 条目显式标注合入状态（"已合入"/"未合入-open"/"未合入-closed"） | 严重 | 半自动 |
| B-11 | PR 链接真实性 | `knowledge_detail.md` 中引用的所有 PR 链接均在 `all_prs.json` 中真实存在 | 重要 | 自动 |
| B-12 | 与 analysis.md 内容一致性 | 对每个分类，`knowledge_detail.md` 中该分类章节的 PR 数量与 `03_knowledge/{category}/analysis.md` 中的 PR 数量一致（允许 ±1 偏差） | 重要 | 半自动 |

**B-1 交叉一致性检查方法**：
```bash
# 提取 03_knowledge/ 下有 analysis.md 的分类目录名
dirs=$(for d in "{output_dir}/03_knowledge/"*/; do
  [ -f "$d/analysis.md" ] && basename "$d"
done)

# 检查 knowledge_detail.md 中是否包含每个分类的章节
for cat in $dirs; do
  grep -q "$cat" "{output_dir}/knowledge_detail.md" || echo "遗漏分类: $cat"
done
```

**B-5/B-6 深度检测方法**：
对每个 PR 条目，扫描是否包含以下任一代码级特征：
- 函数名/类名引用（如 `Foo::Bar`、`Compute` 函数）
- 具体代码行修改描述（如"将 `block_cnt` 计算从...改为..."）
- 技术参数/变量名（如 `BLOCK`、`eventID`、`totalLength`）
- 架构概念（如 "vec 读取 L1"、"MTE2_V 同步屏障"）

缺少以上特征的 PR 条目标记为"表面分析"，统计比例。

**B-5 深度交叉验证**：对被标记为"表面分析"的 PR，交叉引用 `pr_diffs.json` 验证其 patch 是否确实为空。若 patch 非空但分析仍停留在标题层面，应判为失败而非豁免。

**B-8 占位符检测**：
```bash
grep -n -i 'TODO\|待补充\|占位\|xxx\|TBD\|FIXME' "{output_dir}/knowledge_detail.md" && echo "违规: 存在占位符"
```

**B-9 PR 计数方法**：PR 条目总数按唯一 PR URL 计数，即在 `knowledge_detail.md` 全文中去重统计出现的完整 PR URL。

**B-10 合入状态检测方法**：
```bash
# 检查 knowledge_detail.md 中每个 PR 是否含合入状态标注
python3 -c "
import json, re, sys
with open('{output_dir}/01_download/all_prs.json') as f:
    prs = json.load(f)
with open('{output_dir}/knowledge_detail.md') as f:
    text = f.read()

state_keywords = ['已合入', '未合入-open', '未合入-closed']
issues = []
for pr in prs:
    num = str(pr.get('number', ''))
    if num not in text:
        continue
    # 搜索 PR 号附近是否有合入状态标注
    idx = text.find(num)
    context = text[max(0,idx-100):idx+500]
    if not any(kw in context for kw in state_keywords):
        issues.append(f'PR #{num} 缺少合入状态标注')
if issues:
    for i in issues:
        print(i)
    sys.exit(1)
print('OK: 所有 PR 均含合入状态标注')
"
```

**B-11 PR 链接真实性检查**：
```bash
# 验证 knowledge_detail.md 中引用的所有 PR 链接在 all_prs.json 中存在
python3 -c "
import json, re, sys
with open('{output_dir}/01_download/all_prs.json') as f:
    prs = json.load(f)
with open('{output_dir}/knowledge_detail.md') as f:
    text = f.read()

url_pattern = re.compile(
    r'https://(?:e\\\\.gitee\\\\.com/[^/]+/code/pulls/\\\d+'
    r'|gitee\\\\.com/[^/]+/[^/]+/pulls/\\\d+'
    r'|gitcode\\\\.com/[^/]+/[^/]+/merge_requests/\\\d+)'
)
urls = set(url_pattern.findall(text))
pr_urls = {p.get('html_url','') for p in prs}
missing = urls - pr_urls
if missing:
    for u in missing:
        print(f'不存在: {u}')
    sys.exit(1)
print('OK: knowledge_detail.md 中所有 PR 链接均存在于数据集中')
"
```

**B-12 与 analysis.md 内容一致性检查**：
```bash
# 对每个分类，比对 knowledge_detail.md 与 analysis.md 中的 PR 数量
python3 -c "
import json, re, os, sys

output_dir = '{output_dir}'
categories = [d for d in os.listdir(os.path.join(output_dir, '03_knowledge'))
              if os.path.isdir(os.path.join(output_dir, '03_knowledge', d))]

with open(os.path.join(output_dir, 'knowledge_detail.md')) as f:
    kd_text = f.read()

for cat in categories:
    analysis_path = os.path.join(output_dir, '03_knowledge', cat, 'analysis.md')
    if not os.path.exists(analysis_path):
        continue
    with open(analysis_path) as f:
        a_text = f.read()

    # 统计 analysis.md 中的 PR 数量（按 ### PR 标题）
    a_pr_count = len(re.findall(r'^###\s+PR\b', a_text, re.MULTILINE))

    # 统计 knowledge_detail.md 中该分类章节的 PR 数量
    # 定位分类章节
    cat_section_start = kd_text.find(f'## {cat}')
    if cat_section_start == -1:
        print(f'警告: 分类 {cat} 在 knowledge_detail.md 中未找到章节')
        continue
    next_section = re.search(r'\n##\s+', kd_text[cat_section_start+1:])
    cat_section_end = cat_section_start + 1 + next_section.start() if next_section else len(kd_text)
    cat_section = kd_text[cat_section_start:cat_section_end]

    kd_pr_count = len(re.findall(r'^###\s+PR\b', cat_section, re.MULTILINE))

    if abs(kd_pr_count - a_pr_count) > 1:
        print(f'违规: 分类 {cat}: knowledge_detail.md 有 {kd_pr_count} 个 PR，analysis.md 有 {a_pr_count} 个 PR，偏差 > 1')
        sys.exit(1)

print('OK: knowledge_detail.md 与 analysis.md 内容一致')
"
```

---

### 维度 C：resume_skills.md 内容质量

| 编号 | 检查项 | 通过条件 | 严重程度 | 检测方式 |
|------|--------|---------|---------|---------|
| C-1 | 简洁性 | 每条技能一行（含佐证），无冗余空行或重复表述 | 一般 | 半自动 |
| C-2 | 每条含 PR 佐证 | 每条技能条目后附带完整 PR 链接作为佐证 | 严重 | 自动 |
| C-3 | PR 链接完整 | 佐证链接为完整 URL，非 `#xxx` 简写 | 严重 | 自动 |
| C-4 | 与 knowledge_detail 一致 | 技能条目覆盖的主要技术方向与 `knowledge_detail.md` 中的分类匹配；若某分类 PR 数 ≥ 3，则 `resume_skills.md` 中须有对应技能条目 | 重要 | 半自动 |
| C-5 | 可直接用于简历 | 每条技能表述为简历风格（动词开头或技术名词开头，精炼无废话） | 一般 | 人工 |
| C-6 | PR 链接真实性 | `resume_skills.md` 中引用的每个 PR 链接对应的 PR 在 `all_prs.json` 中真实存在 | 重要 | 自动 |
| C-7 | resume_skills 未合入标注 | `resume_skills.md` 中引用未合入 PR 时，括号内含"未合入"字样（如 `（未合入，见 <链接>）`） | 重要 | 自动 |

**C-1 简洁性检查**：
```bash
# 检查是否存在连续空行（冗余）和每条技能是否为一行
python3 -c "
import re
with open('{output_dir}/resume_skills.md') as f:
    lines = f.readlines()
# 检查连续空行
blank_runs = []
run = 0
for i, line in enumerate(lines):
    if line.strip() == '':
        run += 1
    else:
        if run >= 2:
            blank_runs.append((i-run+1, run))
        run = 0
if run >= 2:
    blank_runs.append((len(lines)-run+1, run))
if blank_runs:
    for start, cnt in blank_runs:
        print(f'违规: 第 {start} 行起有连续 {cnt} 个空行')
else:
    print('OK: 无连续空行')
"
```

**C-6 链接真实性检查**：
```bash
# 提取 resume_skills.md 中所有 PR URL，验证其存在于 all_prs.json
python3 -c "
import json, re, sys
with open('{output_dir}/01_download/all_prs.json') as f:
    prs = json.load(f)
with open('{output_dir}/resume_skills.md') as f:
    text = f.read()

# 三种合法 URL 格式：Gitee 社区版、Gitee 企业版、GitCode
url_pattern = re.compile(
    r'https://'
    r'(?:'
    r  'e\\.gitee\\.com/[^/]+/code/pulls/\d+'           # Gitee 企业版
    r'|gitee\\.com/[^/]+/[^/]+/pulls/\d+'               # Gitee 社区版
    r'|gitcode\\.com/[^/]+/[^/]+/merge_requests/\d+'    # GitCode
    r')'
)
urls = set(url_pattern.findall(text))
pr_urls = {p.get('html_url','') for p in prs}
missing = urls - pr_urls
if missing:
    for u in missing:
        print(f'不存在: {u}')
    sys.exit(1)
print('OK: 所有 PR 链接均存在于数据集中')
"
```

**C-7 未合入标注检查**：
```bash
# 检查 resume_skills.md 中引用未合入 PR 时是否标注"未合入"
python3 -c "
import json, re
with open('{output_dir}/01_download/all_prs.json') as f:
    prs = json.load(f)
with open('{output_dir}/resume_skills.md') as f:
    text = f.read()

# 找出未合入的 PR 链接
unmerged_urls = {p['html_url'] for p in prs if p.get('state') != 'merged'}
for url in unmerged_urls:
    # 提取 URL 路径最后一段用于匹配
    m = re.search(r'/(\d+)$', url)
    if not m:
        continue
    pr_num = m.group(1)
    # 在 resume_skills.md 中查找该 PR 引用
    if pr_num in text:
        # 检查该引用附近是否有'未合入'标注
        idx = text.find(url) if url in text else -1
        if idx == -1:
            # URL 不在文本中，尝试按 PR 号匹配
            pattern = re.compile(r'https://\S*' + pr_num + r'\b')
            match = pattern.search(text)
            if match:
                idx = match.start()
        if idx >= 0:
            context = text[max(0,idx-50):idx+150]
            if '未合入' not in context:
                print(f'违规: PR #{pr_num} 为未合入状态但 resume_skills.md 中未标注\"未合入\"')
                sys.exit(1)
print('OK: 所有未合入 PR 均已正确标注')
"
```

---

### 维度 D：03_knowledge/ 分章内容质量

| 编号 | 检查项 | 通过条件 | 严重程度 | 检测方式 |
|------|--------|---------|---------|---------|
| D-1 | 每个分类有 analysis.md | `03_knowledge/{category}/` 下每个目录均有 `analysis.md` | 严重 | 自动 |
| D-2 | analysis.md 结构完整 | 每个 `analysis.md` 包含：技术能力点、逐 PR 代码级分析（每个 PR 一节）、分类总结 | 重要 | 半自动 |
| D-3 | PR 分析深度 | 至少 80% 的 PR 分析包含函数名/代码块引用，非 commit message 概括（与 B-5 阈值一致；允许 ≤20% 的 PR 因 GitCode 无 patch 仅做文件级统计，需标注） | 严重 | 半自动 |
| D-4 | .DONE 标记双向一致性 | `.DONE` 存在 ⟺ `analysis.md` 存在且非空。即：(1) 有 .DONE 则 analysis.md 必存在且非空，(2) analysis.md 存在且非空则 .DONE 必存在 | 重要 | 自动 |
| D-5 | 目录名合法 | 所有分类目录名属于 9 个合法值之一 | 一般 | 自动 |
| D-6 | 无多余目录 | `03_knowledge/` 下仅存在属于 9 个合法分类名的目录，无临时目录、备份目录等杂项目录 | 一般 | 自动 |
| D-7 | PR 链接格式 | 每个 PR 条目中的引用为完整 URL（同 B-3 要求，含平台路径格式校验） | 重要 | 自动 |
| D-8 | analysis.md 合入状态标注 | 每个 `analysis.md` 中 PR 条目含合入状态标注（"已合入"/"未合入-open"/"未合入-closed"） | 重要 | 半自动 |

**9 个合法分类目录名**：
`operator_dev` `feature` `perf_opt` `bugfix` `test` `code_quality` `doc` `infra` `tool`

**D-4 双向检查方法**：
```bash
# 正向：有 .DONE 但无 analysis.md（或为空）
for done in "{output_dir}/03_knowledge/"*/.DONE; do
  [ -f "$done" ] || continue
  dir=$(dirname "$done")
  [ -s "$dir/analysis.md" ] || echo "违规: $(basename "$dir") 有 .DONE 但 analysis.md 不存在或为空"
done

# 反向：有 analysis.md（非空）但无 .DONE
for analysis in "{output_dir}/03_knowledge/"*/analysis.md; do
  [ -f "$analysis" ] || continue
  [ -s "$analysis" ] || continue
  dir=$(dirname "$analysis")
  [ -f "$dir/.DONE" ] || echo "违规: $(basename "$dir") 的 analysis.md 非空但缺少 .DONE"
done

# 空目录检查：既无 .DONE 也无 analysis.md
for d in "{output_dir}/03_knowledge/"*/; do
  [ -z "$(ls -A "$d" 2>/dev/null)" ] && echo "违规: $(basename "$d") 为空目录"
done
```

---

### 维度 E：中间文件质量

| 编号 | 检查项 | 通过条件 | 严重程度 | 检测方式 |
|------|--------|---------|---------|---------|
| E-1 | pr_index.md 结构 | 表格包含列：编号、标题、日期、合入状态、分类、链接 | 一般 | 半自动 |
| E-2 | pr_index.md 分类一致 | 分类值属于 9 个合法分类目录名之一 | 一般 | 自动 |
| E-3 | pr_diffs.json 含 patch | 至少 80% 的 PR 条目包含非空 `patch` 字段；无 patch 的 PR 需标注原因（如"无法获取 diff，仅记录 PR 元信息"）；被截断的 PR 需含 `patch_truncated: true` 标记 | 重要 | 自动 |
| E-4 | 数据量一致性 | `all_prs.json` 的 PR 数 = `pr_diffs.json` 的 PR 数 = `pr_index.md` 的 PR 行数，允许偏差 ±1；`all_prs.json` 中无重复 PR（按 `html_url` 去重比对） | 一般 | 自动 |

**E-4 容差定义**：±1 表示三者的 PR 数量两两之差的绝对值 ≤ 1。超过 ±1 视为不一致。

---

## 三、检视流程

### 步骤 0：前置条件检查

```bash
# analyzer 未完成则检视无意义
if [ ! -f "{output_dir}/02_intermediate/.PHASE_5" ]; then
  echo "错误: analyzer 尚未完成（缺少 .PHASE_5），检视中止。"
  exit 1
fi
```

### 步骤 1：初始化并收集基础信息

```bash
# 创建检视输出目录
mkdir -p "{output_dir}/04_review"

echo "=== 目录结构 ==="
ls -la "{output_dir}/"
echo ""
echo "=== 顶层所有文件 ==="
find "{output_dir}" -maxdepth 1 -type f
echo ""
echo "=== 子目录（精确匹配三个） ==="
for d in 01_download 02_intermediate 03_knowledge; do
  [ -d "{output_dir}/$d" ] && echo "$d: ✅" || echo "$d: ❌ 缺失"
done
echo ""
echo "=== 阶段标记 ==="
ls "{output_dir}/02_intermediate/.PHASE_"* 2>/dev/null
echo ""
echo "=== 03_knowledge 分类目录 ==="
ls -d "{output_dir}/03_knowledge/"*/ 2>/dev/null
echo ""
echo "=== .DONE 标记 ==="
find "{output_dir}/03_knowledge/" -name '.DONE' 2>/dev/null
echo ""
echo "=== 错误日志 ==="
if [ -f "{output_dir}/02_intermediate/errors.log" ]; then
  lines=$(wc -l < "{output_dir}/02_intermediate/errors.log" | tr -d ' ')
  error_count=$(grep -c '\[ERROR\]' "{output_dir}/02_intermediate/errors.log" 2>/dev/null || echo 0)
  echo "errors.log 存在，共 $lines 行，其中 ERROR 级别 $error_count 条"
else
  echo "无 errors.log"
fi
```

### 步骤 2：逐维度检查

按维度 A → B → C → D → E 顺序执行检查项，每项记录通过/失败/跳过。

**自动化检查项**（bash/Python 脚本直接判定）：A-1 ~ A-8、B-3、B-8、B-9、B-11、C-2、C-3、C-6、C-7、D-1、D-4、D-5、D-6、D-7、E-2、E-3、E-4

**半自动检查项**（脚本提供线索，需进一步分析确认）：B-1、B-2、B-4、B-5、B-6、B-7、B-10、B-12、C-1、C-4、D-2、D-3、D-8、E-1

**人工检查项**（需阅读内容判断）：C-5

执行时：
- 使用 `read` 工具读取各文件内容
- 使用 `grep` 工具搜索关键模式（如 PR 链接格式、代码级特征、占位符）
- 对 `knowledge_detail.md` 和 `resume_skills.md` 做全文内容分析

### 步骤 3：生成并保存检视报告

按下方模板生成结构化报告，写入 `{output_dir}/04_review/review_report.md`。生成后向用户展示报告摘要（总览表 + 结论 + 失败项列表），并告知完整报告路径。

---

## 四、检视报告模板

```markdown
# PR 知识提炼产出检视报告

**检视目录**：{output_dir}
**检视时间**：{timestamp}

---

## 1. 总览

| 维度 | 检查项数 | 通过 | 失败 | 跳过 |
|------|---------|------|------|------|
| A: 目录结构 | 8 | ? | ? | ? |
| B: knowledge_detail.md | 12 | ? | ? | ? |
| C: resume_skills.md | 7 | ? | ? | ? |
| D: 03_knowledge/ | 8 | ? | ? | ? |
| E: 中间文件 | 4 | ? | ? | ? |
| **合计** | **39** | **?** | **?** | **?** |

**结论**：[通过 / 有条件通过 / 不通过]

---

## 2. 失败项详情

### [严重] B-5: 代码级分析深度不足

**问题**：以下 PR 条目仅含 PR 标题/commit message 概括，缺少代码级分析：

| PR | 位置 | 问题描述 |
|----|------|---------|
| PR #xxx | knowledge_detail.md L42 | 仅"修复编译问题"，无具体代码修改描述 |

**建议**：对上述 PR 补充具体函数名、代码行修改描述。

---

### [一般] C-1: resume_skills.md 简洁性

**问题**：存在连续空行或冗余表述。

**建议**：删除连续空行，确保每条技能一行。

---

## 3. 跳过项说明

（如有跳过项，说明原因）

---

## 4. 统计数据

- PR 总数：?
- 分类覆盖：?/9
- 代码级分析覆盖率：?%（?/? PR 含代码行级细节）
- errors.log 遗漏项（ERROR 级别）：? 条
```

---

## 五、严重程度判定规则

| 结论 | 条件 |
|------|------|
| **通过** | 无严重失败项，重要失败项 ≤ 2，一般失败项 ≤ 9 |
| **有条件通过** | 严重失败项 ≤ 2 且可快速修复，或一般失败项 ≥ 10 |
| **不通过** | 严重失败项 ≥ 3，或存在不可修复的严重问题 |

> 一般失败项累计 ≥ 10 时，即使严重/重要项达标，也降级为"有条件通过"，反映整体质量存疑。

---

## 六、分类目录名映射参考

以下映射来自 `code-commit-analyzer` 的 Phase 4 step 1，用于校验目录名合法性：

| 中文分类 | 目录名 | 关键词 |
|----------|--------|--------|
| 算子开发 | `operator_dev` | Flash Attention、MXFP8、QSMLA |
| 特性功能 | `feature` | anti-sparse、RoPE、mask |
| 性能优化 | `perf_opt` | tiling、UB、L1、vec |
| Bug 修复 | `bugfix` | 编译、卡死、scale |
| 测试 | `test` | pytest、UT、golden、覆盖率 |
| 代码质量 | `code_quality` | 告警、红线、注释、检视 |
| 文档 | `doc` | README、设计文档、接口文档 |
| 基础设施 | `infra` | CI、子仓、构建脚本 |
| 工具 | `tool` | 脚本、辅助工具 |

---

## 七、注意事项

1. 本技能不修改被检视目录中的已有文件，仅在 `04_review/` 子目录下新增检视产物
2. 对 `knowledge_detail.md` 和 `03_knowledge/*/analysis.md` 的代码级分析深度检查，通过扫描关键词模式（函数名引用、代码行修改描述、技术参数名）判断，非 AI 语义理解
3. 若被检视目录不存在或结构严重损坏，直接报告错误并终止检视
4. 跳过项必须说明原因（如分类无 PR 故无对应目录）
5. 检视前必须确认 analyzer 已完成（`.PHASE_5` 存在），否则检视无意义
6. 若 `04_review/` 已存在（多次检视），先清空再重新生成以保持报告一致性
7. 分类优先级规则验证（半自动）：当一个 PR 同时匹配多个分类时，按 `operator_dev > bugfix > feature > perf_opt > test > code_quality > doc > infra > tool` 取最高优先级，对 `pr_index.md` 中的分类可做关键词重判定交叉验证

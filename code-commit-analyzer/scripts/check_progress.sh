#!/bin/bash
# 断点续传进度检查脚本
# 用法: bash check_progress.sh <output_dir>

OUTPUT_DIR="${1:-.}"

echo "=== 检查进度 ==="
echo ""

echo "--- 阶段完成度 ---"
for phase in 1 2 3 4 5; do
  if [ -f "${OUTPUT_DIR}/02_intermediate/.PHASE_${phase}" ]; then
    echo "  阶段${phase}: ✅ 已完成"
  else
    echo "  阶段${phase}: ⏳ 待执行"
  fi
done

echo ""
echo "--- 分类目录完成度 ---"
if [ -d "${OUTPUT_DIR}/03_knowledge" ]; then
  for dir in "${OUTPUT_DIR}"/03_knowledge/*/; do
    [ -d "$dir" ] || continue
    category=$(basename "$dir")
    if [ -f "$dir/.DONE" ]; then
      echo "  ${category}: ✅ 已完成"
    else
      echo "  ${category}: ⏳ 待处理"
    fi
  done
else
  echo "  (03_knowledge 目录不存在)"
fi

echo ""
echo "--- 文件统计 ---"
if [ -f "${OUTPUT_DIR}/01_download/all_prs.json" ]; then
  PR_COUNT=$(python3 -c "import json; print(len(json.load(open('${OUTPUT_DIR}/01_download/all_prs.json'))))" 2>/dev/null || echo "?")
  echo "  all_prs.json: ${PR_COUNT} 个 PR"
else
  echo "  all_prs.json: 不存在"
fi

if [ -f "${OUTPUT_DIR}/02_intermediate/pr_diffs.json" ]; then
  DIFF_COUNT=$(python3 -c "import json; d=json.load(open('${OUTPUT_DIR}/02_intermediate/pr_diffs.json')); print(f'{len(d)} (成功 {sum(1 for r in d if not r.get(\"error\"))}, 失败 {sum(1 for r in d if r.get(\"error\"))})')" 2>/dev/null || echo "?")
  echo "  pr_diffs.json: ${DIFF_COUNT}"
else
  echo "  pr_diffs.json: 不存在"
fi

if [ -f "${OUTPUT_DIR}/knowledge_detail.md" ]; then
  echo "  knowledge_detail.md: ✅ ($(wc -c < "${OUTPUT_DIR}/knowledge_detail.md") bytes)"
else
  echo "  knowledge_detail.md: ❌"
fi

if [ -f "${OUTPUT_DIR}/resume_skills.md" ]; then
  echo "  resume_skills.md: ✅ ($(wc -c < "${OUTPUT_DIR}/resume_skills.md") bytes)"
else
  echo "  resume_skills.md: ❌"
fi

echo ""
echo "--- 错误统计 ---"
if [ -f "${OUTPUT_DIR}/02_intermediate/errors.log" ]; then
  ERROR_COUNT=$(grep -c '^\[ERROR\]' "${OUTPUT_DIR}/02_intermediate/errors.log" 2>/dev/null || echo "0")
  echo "  errors.log: ${ERROR_COUNT} 个 [ERROR]"
else
  echo "  errors.log: 不存在"
fi

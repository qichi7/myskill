#!/bin/bash
# 阶段5: 顶层整洁校验脚本
# 用法: bash verify_topdir.sh <output_dir>
# 检查顶层只有 knowledge_detail.md 和 resume_skills.md 两个文件，多余的移入 02_intermediate/

OUTPUT_DIR="${1:-.}"

echo "=== 顶层整洁校验 ==="

# 检查缺失
MISSING=0
for f in knowledge_detail.md resume_skills.md; do
  if [ ! -f "${OUTPUT_DIR}/$f" ]; then
    echo "  ❌ 缺失: $f"
    MISSING=$((MISSING + 1))
  else
    SIZE=$(ls -la "${OUTPUT_DIR}/$f" | awk '{print $5}')
    echo "  ✅ $f (${SIZE}B)"
  fi
done

# 检查多余
MOVED=0
for f in "${OUTPUT_DIR}"/*; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  case "$name" in
    knowledge_detail.md|resume_skills.md) ;;
    *)
      mv "$f" "${OUTPUT_DIR}/02_intermediate/" 2>/dev/null && echo "  已移走多余文件: $name" && MOVED=$((MOVED + 1))
      ;;
  esac
done

echo ""
if [ "$MISSING" -gt 0 ]; then
  echo "⚠️ 有 $MISSING 个文件缺失"
fi
if [ "$MOVED" -gt 0 ]; then
  echo "已移走 $MOVED 个多余文件到 02_intermediate/"
fi
if [ "$MISSING" -eq 0 ] && [ "$MOVED" -eq 0 ]; then
  echo "✅ 顶层整洁，两个目标文件齐全"
fi

# errors.log 统计
if [ -f "${OUTPUT_DIR}/02_intermediate/errors.log" ]; then
  ERROR_COUNT=$(grep -c '^\[ERROR\]' "${OUTPUT_DIR}/02_intermediate/errors.log" 2>/dev/null || echo "0")
  if [ "$ERROR_COUNT" -gt 0 ]; then
    echo ""
    echo "⚠️ errors.log 中有 ${ERROR_COUNT} 个 [ERROR] 条目"
    echo "   路径: ${OUTPUT_DIR}/02_intermediate/errors.log"
  fi
fi

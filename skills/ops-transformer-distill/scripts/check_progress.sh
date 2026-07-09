#!/usr/bin/env bash
# 蒸馏skill: 进度检查脚本
#
# 扫描输出目录，报告各阶段完成度、批次进度、合入记录统计、错误统计。
#
# 用法:
#   check_progress.sh {output_dir}

set -euo pipefail

OUTPUT_DIR="${1:-.}"

echo "=== 蒸馏进度报告: ${OUTPUT_DIR} ==="
echo ""

# ── 阶段完成度 ──
echo "--- 阶段进度 ---"
for i in 1 2 3 4 5; do
  marker="${OUTPUT_DIR}/02_intermediate/.PHASE_${i}"
  if [[ -f "$marker" ]]; then
    echo "  阶段${i}: ✅ 完成"
  else
    echo "  阶段${i}: ❌ 未完成"
  fi
done

# 找到第一个未完成的阶段
RESUME_STAGE=""
for i in 1 2 3 4 5; do
  marker="${OUTPUT_DIR}/02_intermediate/.PHASE_${i}"
  if [[ ! -f "$marker" ]]; then
    RESUME_STAGE=$i
    break
  fi
done

echo ""
if [[ -n "$RESUME_STAGE" ]]; then
  echo ">>> 应从阶段${RESUME_STAGE}恢复"
else
  echo ">>> 全部阶段已完成"
fi
echo ""

# ── 合入记录统计 ──
echo "--- 合入记录统计 ---"
merges_file="${OUTPUT_DIR}/01_download/merges.json"
if [[ -f "$merges_file" ]]; then
  merge_count=$(python3 -c "import json; print(len(json.load(open('$merges_file'))))" 2>/dev/null || echo "?")
  echo "  合入记录总数: ${merge_count}"
else
  echo "  合入记录: 未收集"
fi

diffs_file="${OUTPUT_DIR}/02_intermediate/diffs.json"
if [[ -f "$diffs_file" ]]; then
  diff_total=$(python3 -c "import json; d=json.load(open('$diffs_file')); print(len(d))" 2>/dev/null || echo "?")
  diff_ok=$(python3 -c "import json; d=json.load(open('$diffs_file')); print(sum(1 for r in d if not r.get('error')))" 2>/dev/null || echo "?")
  diff_fail=$(python3 -c "import json; d=json.load(open('$diffs_file')); print(sum(1 for r in d if r.get('error')))" 2>/dev/null || echo "?")
  echo "  Diff已获取: ${diff_total} (成功:${diff_ok} 失败:${diff_fail})"
else
  echo "  Diff: 未获取"
fi

echo ""

# ── 批次进度 ──
echo "--- 批次进度 ---"
progress_file="${OUTPUT_DIR}/02_intermediate/batch_progress.json"
if [[ -f "$progress_file" ]]; then
  python3 -c "
import json, os
with open('$progress_file') as f:
    p = json.load(f)
total = p.get('total_records', '?')
done = p.get('completed_count', 0)
completed_batches = p.get('completed_batches', [])
next_batch = p.get('next_batch', None)
print(f'  已完成: {done}/{total} 条记录')
print(f'  已完成批次: {completed_batches}')
if next_batch:
    print(f'  下一批次: {next_batch}')
else:
    print(f'  全部批次已完成')

# 扫描 batch 目录
batch_dir = '$OUTPUT_DIR/03_knowledge'
if os.path.isdir(batch_dir):
    batches = sorted([d for d in os.listdir(batch_dir) if d.startswith('batch_')])
    for b in batches:
        done_marker = os.path.join(batch_dir, b, '.DONE')
        marker = '✅' if os.path.exists(done_marker) else '⏳'
        print(f'  {marker} {b}')
" 2>/dev/null || echo "  进度文件解析失败"
else
  echo "  批次进度: 未开始"
fi

echo ""

# ── 错误统计 ──
errors_log="${OUTPUT_DIR}/02_intermediate/errors.log"
if [[ -f "$errors_log" ]]; then
  error_count=$(grep -c '^\[ERROR\]' "$errors_log" 2>/dev/null || echo "0")
  echo "--- 错误统计 ---"
  echo "  错误数: ${error_count}"
  echo ""
fi

# ── 产物统计 ──
summary_file="${OUTPUT_DIR}/Summary.md"
if [[ -f "$summary_file" ]]; then
  size=$(wc -c < "$summary_file" | tr -d ' ')
  echo "--- 最终产物 ---"
  echo "  Summary.md: ${size} 字节"
  echo ""
fi

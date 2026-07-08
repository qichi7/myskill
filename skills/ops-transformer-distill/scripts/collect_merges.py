#!/usr/bin/env python3
"""阶段2: 收集指定仓库所有合入记录

从 GitCode API 分页获取指定仓库的所有合入记录（state=merged 的 pulls），
按合入时间升序输出。

用法:
    GITCODE_TOKEN=xxx python3 -u collect_merges.py {output_dir} {repo}

环境变量:
    GITCODE_TOKEN — GitCode Personal Access Token（必须）

输出:
    {output_dir}/01_download/merges.json

⚠️ 关键经验（来自 code-commit-analyzer）:
- 响应可能是 JSON 数组，也可能包裹在 {"data":[]} / {"list":[]} 中，需兼容
- owner 字段可能是 dict（含 login）也可能不是，需类型检查
- 频率限制 250次/分钟，需 time.sleep(0.3)
- 合入记录的 base.sha 不可靠，阶段3必须重新获取
- daemon 线程 + join(timeout) 防卡死，不用 ThreadPoolExecutor
"""
import sys
import os
import json
import time
import threading
import requests

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
REPO = sys.argv[2] if len(sys.argv) > 2 else "cann/ops-transformer"
TOKEN = os.environ.get("GITCODE_TOKEN", "")
API_BASE = "https://gitcode.com/api/v5"

ERRORS_LOG = os.path.join(OUTPUT_DIR, "02_intermediate", "errors.log")


def log_error(msg):
    with open(ERRORS_LOG, "a") as f:
        f.write(f"[ERROR] {msg}\n")
    print(f"[ERROR] {msg}", flush=True)


def log_info(msg):
    print(msg, flush=True)


def run_with_timeout_safe(func, timeout=60):
    """daemon 线程 + join(timeout) 防卡死"""
    result_box = [None]
    error_box = [None]
    def worker():
        try:
            result_box[0] = func()
        except Exception as e:
            error_box[0] = e
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return None, TimeoutError(f"timeout>{timeout}s")
    if error_box[0] is not None:
        return None, error_box[0]
    return result_box[0], None


def api_get(url, max_retries=3):
    """GitCode API GET with retries"""
    hdrs = {"private-token": TOKEN}
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=hdrs, timeout=(10, 30))
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return None


def parse_items(data):
    """兼容响应结构: JSON 数组或 {"data":[]} / {"list":[]}"""
    return data if isinstance(data, list) else data.get("data", data.get("list", []))


def parse_state(record):
    """解析状态，兼容 state=="merged" 和 merged==True"""
    state = record.get("state", "")
    merged = record.get("merged", False)
    if isinstance(merged, str):
        merged = merged.lower() == "true"
    if state == "merged" or merged:
        return "merged"
    if state == "open":
        return "open"
    if state == "closed":
        return "closed"
    return state


def main():
    if not TOKEN:
        log_error("GITCODE_TOKEN not set")
        sys.exit(1)

    log_info(f"=== 收集 {REPO} 合入记录 ===")

    all_records = []
    page = 1

    while True:
        url = f"{API_BASE}/repos/{REPO}/pulls?state=merged&per_page=100&page={page}&sort=created&direction=asc"
        data, err = run_with_timeout_safe(lambda: api_get(url), timeout=60)
        if err:
            log_error(f"page {page}: {err}")
            break
        if not data:
            break
        items = parse_items(data)
        if not items:
            break

        for record in items:
            number = record.get("number") or record.get("id") or record.get("iid")
            if number is None:
                continue

            merged_at = record.get("merged_at") or ""
            created_at = record.get("created_at") or ""
            updated_at = record.get("updated_at") or ""

            record_data = {
                "platform": "gitcode",
                "repo": REPO,
                "repo_owner": REPO.split("/")[0],
                "repo_name": REPO.split("/")[-1],
                "number": number,
                "title": record.get("title", ""),
                "state": parse_state(record),
                "merged": record.get("merged", False),
                "html_url": record.get("html_url", ""),
                "created_at": created_at,
                "updated_at": updated_at,
                "merged_at": merged_at,
                "merge_date": merged_at or updated_at or created_at,
                "user_login": (record.get("user") or {}).get("login", "") or (record.get("author") or {}).get("login", ""),
                "head_sha": (record.get("head") or {}).get("sha", ""),
                "base_sha": (record.get("base") or {}).get("sha", ""),
            }
            all_records.append(record_data)

        log_info(f"  page {page}: {len(items)} 条合入记录 (累计 {len(all_records)})")

        if len(items) < 100:
            break
        page += 1
        time.sleep(0.3)

    # 按合入时间升序排列（从第一条开始）
    all_records.sort(key=lambda r: r.get("merged_at") or r.get("merge_date") or "")

    log_info(f"\n=== 收集完成: 共 {len(all_records)} 条合入记录 ===")
    if all_records:
        log_info(f"  最早: #{all_records[0]['number']} {all_records[0]['merged_at']} {all_records[0]['title'][:50]}")
        log_info(f"  最新: #{all_records[-1]['number']} {all_records[-1]['merged_at']} {all_records[-1]['title'][:50]}")

    output_file = os.path.join(OUTPUT_DIR, "01_download", "merges.json")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    log_info(f"已保存到 {output_file}")


if __name__ == "__main__":
    main()

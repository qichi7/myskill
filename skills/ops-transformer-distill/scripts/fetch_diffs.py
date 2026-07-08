#!/usr/bin/env python3
"""阶段3: 逐条合入记录获取 diff（三步法 + 防卡死 + 增量保存）

复用 code-commit-analyzer 的 GitCode 三步法（合入记录详情→compare→files补全），
保留 daemon 线程防卡死 + 增量保存。

用法:
    GITCODE_TOKEN=xxx python3 -u fetch_diffs.py {output_dir} {repo}

环境变量:
    GITCODE_TOKEN — GitCode Personal Access Token（必须）

输入:
    {output_dir}/01_download/merges.json
输出:
    {output_dir}/02_intermediate/diffs.json

⚠️ 关键经验:
- daemon=True + join(timeout) 防卡死，不用 ThreadPoolExecutor（单个卡死会阻塞整批）
- 每10条合入记录增量保存一次，中断不丢数据
- patch 可能是 string 也可能是 dict，需 extract_patch()
- compare API 大文件 patch 可能空，需 /pulls/{number}/files 补全
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

RECORD_TIMEOUT = 30    # 单条合入记录硬超时（秒）
SAVE_INTERVAL = 10     # 每N条合入记录保存一次

ERRORS_LOG = os.path.join(OUTPUT_DIR, "02_intermediate", "errors.log")


def log_error(msg):
    with open(ERRORS_LOG, "a") as f:
        f.write(f"[ERROR] {msg}\n")
    print(f"[ERROR] {msg}", flush=True)


# === 工具函数 ===
def extract_patch(patch_val):
    """从 patch 字段提取 diff 字符串

    ⚠️ patch 可能是 string 也可能是 dict:
    - string: "@@ -1,2 +1,3 @@\\n..."
    - dict: {"diff": "@@ ...", "new_path": "...", "old_path": "..."}
    """
    if isinstance(patch_val, str):
        return patch_val
    if isinstance(patch_val, dict):
        return patch_val.get("diff", "")
    return ""


def truncate_patch(patch_str, max_lines=500):
    """按行截断 patch，防止 JSON 过大"""
    if not patch_str:
        return "", False
    lines = patch_str.split("\n")
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]), True
    return patch_str, False


def to_int(val):
    """安全转 int（API 可能返回字符串类型如 "10"）"""
    try:
        return int(val) if val else 0
    except (ValueError, TypeError):
        return 0


# === GitCode 三步法 ===
def get_diff(repo, number, token, head_sha=None, base_sha=None):
    """GitCode 三步法: 合入记录详情 → compare → files 补全"""
    hdrs = {"private-token": token}

    # Step A: 获取 head.sha 和 base.sha（列表返回的不可靠）
    if not head_sha or not base_sha:
        try:
            r = requests.get(f"{API_BASE}/repos/{repo}/pulls/{number}",
                             headers=hdrs, timeout=(10, 20))
        except Exception:
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        head = data.get("head", {}) if isinstance(data.get("head"), dict) else {}
        base = data.get("base", {}) if isinstance(data.get("base"), dict) else {}
        head_sha = head.get("sha")
        base_sha = base.get("sha")
    if not head_sha or not base_sha:
        return None

    # Step B: compare API（主路径）
    try:
        r = requests.get(
            f"{API_BASE}/repos/{repo}/compare/{base_sha}...{head_sha}",
            headers=hdrs, timeout=(10, 30),
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    files = data.get("files", [])

    result_files = []
    need_backup = []
    for f in files:
        patch_str = extract_patch(f.get("patch", ""))
        if not patch_str:
            need_backup.append(f.get("filename", ""))
        patch, truncated = truncate_patch(patch_str)
        result_files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": to_int(f.get("additions", 0)),
            "deletions": to_int(f.get("deletions", 0)),
            "patch": patch,
            "patch_truncated": truncated,
        })

    # Step C: 对 patch 为空的文件用 /pulls/{number}/files 补全
    if need_backup:
        page = 1
        backup_files = {}
        while page <= 5:
            try:
                r = requests.get(
                    f"{API_BASE}/repos/{repo}/pulls/{number}/files",
                    headers=hdrs, params={"per_page": 100, "page": page},
                    timeout=(10, 20),
                )
                if r.status_code != 200:
                    break
                data = r.json()
                items = data if isinstance(data, list) else data.get("data", data.get("list", []))
                if not items:
                    break
                for f in items:
                    fname = f.get("filename", "") or f.get("new_path", "")
                    patch_obj = f.get("patch")
                    diff_content = extract_patch(patch_obj)
                    if fname and fname in need_backup:
                        backup_files[fname] = {
                            "additions": to_int(f.get("additions", 0)),
                            "deletions": to_int(f.get("deletions", 0)),
                            "patch": diff_content,
                        }
                if len(items) < 100:
                    break
                page += 1
                time.sleep(0.3)
            except Exception:
                break

        for rf in result_files:
            if rf["filename"] in backup_files and not rf["patch"]:
                bf = backup_files[rf["filename"]]
                patch, truncated = truncate_patch(bf["patch"])
                rf["patch"] = patch
                rf["patch_truncated"] = truncated
                if rf["additions"] == 0:
                    rf["additions"] = bf["additions"]
                if rf["deletions"] == 0:
                    rf["deletions"] = bf["deletions"]

    return result_files


# === 合入记录处理 ===
def process_record(record):
    """处理单条合入记录，返回 diff 结果 dict"""
    number = record["number"]
    result = {
        "platform": "gitcode", "repo": REPO, "number": number,
        "title": record.get("title", ""), "state": record.get("state", ""),
        "html_url": record.get("html_url", ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "merged_at": record.get("merged_at"),
        "files": [], "total_additions": 0, "total_deletions": 0, "error": None,
    }
    try:
        files = get_diff(REPO, number, TOKEN,
                         record.get("head_sha"), record.get("base_sha"))
        if files is None:
            result["error"] = "Failed to get diff"
        else:
            result["files"] = files
            result["total_additions"] = sum(f.get("additions", 0) for f in files)
            result["total_deletions"] = sum(f.get("deletions", 0) for f in files)
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


def run_with_timeout(func, timeout=30):
    """daemon 线程 + join(timeout) 防卡死

    ⚠️ 不用 ThreadPoolExecutor + as_completed（单个卡死会导致整批阻塞）
    """
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


# === 主流程 ===
def main():
    if not TOKEN:
        log_error("GITCODE_TOKEN not set")
        sys.exit(1)

    output_file = os.path.join(OUTPUT_DIR, "02_intermediate", "diffs.json")
    merges_file = os.path.join(OUTPUT_DIR, "01_download", "merges.json")

    with open(merges_file, "r") as f:
        all_records = json.load(f)

    # 读取已有结果（断点续传）
    results = []
    processed_keys = set()
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            results = json.load(f)
        processed_keys = {(r["platform"], r["repo"], r["number"]) for r in results}

    pending = [r for r in all_records
               if (r["platform"], r["repo"], r["number"]) not in processed_keys]

    print(f"=== Diff 获取: 共 {len(all_records)} 条, 已处理 {len(results)}, 待处理 {len(pending)} ===",
          flush=True)
    print(f"每条硬超时 {RECORD_TIMEOUT}s，每 {SAVE_INTERVAL} 条保存一次", flush=True)

    total = len(all_records)
    success = sum(1 for r in results if not r.get("error"))
    failed = sum(1 for r in results if r.get("error"))

    for i, record in enumerate(pending):
        idx = len(results)
        progress = f"[{idx+1}/{total}]"

        result, err = run_with_timeout(
            lambda r=record: process_record(r),
            timeout=RECORD_TIMEOUT,
        )

        if err is not None:
            err_msg = str(err)[:80]
            result = {
                "platform": "gitcode", "repo": REPO, "number": record["number"],
                "title": record.get("title", ""), "state": record.get("state", ""),
                "html_url": record.get("html_url", ""),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "merged_at": record.get("merged_at"),
                "files": [], "total_additions": 0, "total_deletions": 0,
                "error": err_msg,
            }
            failed += 1
            log_error(f"timeout_or_err {REPO}#{record['number']}: {err_msg}")
            print(f"  {progress} {REPO}#{record['number']}: {err_msg}", flush=True)
            results.append(result)
        else:
            results.append(result)
            if result.get("error"):
                failed += 1
                print(f"  {progress} {REPO}#{record['number']}: ERROR {result['error'][:50]}", flush=True)
            else:
                success += 1
                fc = len(result.get("files", []))
                print(f"  {progress} {REPO}#{record['number']}: {fc} files +{result['total_additions']}/-{result['total_deletions']}", flush=True)

        # 增量保存
        if (i + 1) % SAVE_INTERVAL == 0:
            with open(output_file, "w") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"  --- 进度: {len(results)}/{total} 成功:{success} 失败:{failed} ---", flush=True)

    # 最终保存
    with open(output_file, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    success = sum(1 for r in results if not r.get("error"))
    failed = sum(1 for r in results if r.get("error"))
    print(f"\n=== 全部完成: {len(results)} 条, 成功 {success}, 失败 {failed} ===", flush=True)

    failed_records = [r for r in results if r.get("error")]
    if failed_records:
        print(f"\n失败/超时的合入记录 ({len(failed_records)} 条):", flush=True)
        for r in failed_records:
            print(f"  {REPO}#{r['number']} [{r['state']}] {r['title'][:40]} -> {r['error'][:50]}", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""阶段3: 代码级 Diff 分析脚本

用法:
    python3 diff_analyze.py <output_dir>

从 {output_dir}/01_download/all_prs.json 读取全部 PR，
逐个获取 diff（支持 Gitee 社区版 REST / Gitee 企业版 MCP / GitCode 三步法），
每个 PR 有 30 秒硬超时（daemon 线程 + join），超时跳过不阻塞。
结果保存到 {output_dir}/02_intermediate/pr_diffs.json。

⚠️ 关键设计（防卡死）:
- 用 daemon 线程 + join(timeout=30) 逐 PR 处理，而非 ThreadPoolExecutor + as_completed
  （后者单个 PR 卡死会导致整个批次永不完成）
- requests timeout 用元组 (connect_timeout, read_timeout)
- patch 字段可能是 dict 而非 string，必须用 extract_patch() 统一处理
- 每 10 个 PR 增量保存一次

后台运行方式:
    nohup python3 -u diff_analyze.py <output_dir> > {output_dir}/02_intermediate/diff.log 2>&1 &
    disown
    tail -f {output_dir}/02_intermediate/diff.log
"""
import requests
import json
import time
import sys
import os
import threading
import warnings

warnings.filterwarnings("ignore")

# === 配置 ===
GITEE_PAT = os.environ.get("GITEE_PAT", "")
GITCODE_TOKEN = os.environ.get("GITCODE_TOKEN", "")
MCP_URL = "https://api.gitee.com/mcp"
GITEE_REST = "https://gitee.com/api/v5"
GITCODE_API = "https://gitcode.com/api/v5"
PR_TIMEOUT = 30  # 每个 PR 的硬超时秒数
SAVE_INTERVAL = 10  # 每 N 个 PR 保存一次

_mcp_id = 200
_mcp_initialized = False


def log_error(error_log, msg):
    with open(error_log, "a") as f:
        f.write(f"[ERROR] {msg}\n")


# === MCP 客户端 ===
def mcp_init(hdrs):
    global _mcp_initialized
    if _mcp_initialized:
        return
    requests.post(MCP_URL, headers=hdrs, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "opencode", "version": "1.0"}}
    }, timeout=(10, 20))
    requests.post(MCP_URL, headers=hdrs, json={
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
    }, timeout=(10, 15))
    _mcp_initialized = True


def mcp_call(hdrs, name, args):
    global _mcp_id
    _mcp_id += 1
    for attempt in range(2):
        try:
            r = requests.post(MCP_URL, headers=hdrs, json={
                "jsonrpc": "2.0", "id": _mcp_id, "method": "tools/call",
                "params": {"name": name, "arguments": args}
            }, timeout=(10, 30))
            d = r.json()
            if "error" in d:
                raise RuntimeError(d["error"])
            txt = "".join(c.get("text", "") for c in d.get("result", {}).get("content", []))
            return json.loads(txt) if txt else {}
        except Exception:
            if attempt < 1:
                time.sleep(2)
            else:
                raise
    return {}


# === 工具函数 ===
def extract_patch(patch_val):
    """从 patch 字段提取 diff 字符串

    ⚠️ patch 可能是 string 也可能是 dict:
    - string: "@@ -1,2 +1,3 @@\n..."
    - dict: {"diff": "@@ ...", "new_path": "...", "old_path": "...", "too_large": false}
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
    """安全转 int（Gitee/MCP 可能返回字符串类型如 "10"）"""
    try:
        return int(val) if val else 0
    except (ValueError, TypeError):
        return 0


# === Diff 获取 ===
def get_gitee_diff_rest(repo, number, token):
    """Gitee 社区版 REST API: /pulls/{number}/files 端点

    ⚠️ /pulls/{number}（PR 详情）不返回 files[]，必须用 /pulls/{number}/files
    ⚠️ patch 字段是 dict 而非 string，需 extract_patch() 处理
    """
    result_files = []
    page = 1
    while True:
        r = requests.get(
            f"{GITEE_REST}/repos/{repo}/pulls/{number}/files",
            params={"access_token": token, "per_page": 100, "page": page},
            timeout=(10, 30),
        )
        if r.status_code == 404:
            data = r.json() if r.text else {}
            if "Not Found" in str(data.get("message", "")):
                return None  # 企业版仓库
            return result_files if result_files else None
        if r.status_code != 200:
            return result_files if result_files else None
        data = r.json()
        if not data:
            break
        for f in data:
            patch_str = extract_patch(f.get("patch", ""))
            patch, truncated = truncate_patch(patch_str)
            result_files.append({
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": to_int(f.get("additions", 0)),
                "deletions": to_int(f.get("deletions", 0)),
                "patch": patch,
                "patch_truncated": truncated,
            })
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.3)
    return result_files


def get_gitee_diff_mcp(owner, repo, number, hdrs):
    """Gitee 企业版 MCP get_diff_files"""
    mcp_init(hdrs)
    result = mcp_call(hdrs, "get_diff_files", {
        "owner": owner, "repo": repo, "number": number
    })
    if isinstance(result, dict):
        files = result.get("files", result.get("data", result.get("list", [])))
    elif isinstance(result, list):
        files = result
    else:
        files = []

    result_files = []
    for f in files:
        patch_str = extract_patch(f.get("patch", ""))
        patch, truncated = truncate_patch(patch_str)
        result_files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": to_int(f.get("additions", 0)),
            "deletions": to_int(f.get("deletions", 0)),
            "patch": patch,
            "patch_truncated": truncated,
        })
    return result_files


def get_gitcode_diff(repo, number, token, pr_head_sha=None, pr_base_sha=None):
    """GitCode 三步法: PR详情 → compare → files 补全"""
    head_sha = pr_head_sha
    base_sha = pr_base_sha
    hdrs = {"private-token": token}

    # Step A: 获取 head.sha 和 base.sha
    if not head_sha or not base_sha:
        r = requests.get(f"{GITCODE_API}/repos/{repo}/pulls/{number}",
                         headers=hdrs, timeout=(10, 20))
        if r.status_code != 200:
            return None
        data = r.json()
        head = data.get("head", {}) if isinstance(data.get("head"), dict) else {}
        base = data.get("base", {}) if isinstance(data.get("base"), dict) else {}
        head_sha = head.get("sha")
        base_sha = base.get("sha")
    if not head_sha or not base_sha:
        return None

    # Step B: compare API (主路径)
    r = requests.get(
        f"{GITCODE_API}/repos/{repo}/compare/{base_sha}...{head_sha}",
        headers=hdrs, timeout=(10, 30),
    )
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
            "additions": f.get("additions", 0) or 0,
            "deletions": f.get("deletions", 0) or 0,
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
                    f"{GITCODE_API}/repos/{repo}/pulls/{number}/files",
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
                            "additions": f.get("additions", 0) or 0,
                            "deletions": f.get("deletions", 0) or 0,
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


# === PR 处理 ===
def process_pr(pr, gitee_pat, gitcode_token):
    """处理单个 PR，返回 diff 结果 dict"""
    platform = pr["platform"]
    repo = pr["repo"]
    number = pr["number"]
    result = {
        "platform": platform, "repo": repo, "number": number,
        "title": pr.get("title", ""), "state": pr.get("state", ""),
        "html_url": pr.get("html_url", ""),
        "created_at": pr.get("created_at"), "updated_at": pr.get("updated_at"),
        "merged_at": pr.get("merged_at"),
        "files": [], "total_additions": 0, "total_deletions": 0, "error": None,
    }
    try:
        if platform == "gitee":
            owner = pr.get("owner", repo.split("/")[0] if "/" in repo else "")
            repo_name = pr.get("repo_name", repo.split("/")[-1] if "/" in repo else repo)
            # 先试 REST API
            files = get_gitee_diff_rest(repo, number, gitee_pat)
            if files is None:
                # 企业版仓库，转 MCP
                hdrs = {
                    "Authorization": f"Bearer {gitee_pat}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                }
                files = get_gitee_diff_mcp(owner, repo_name, number, hdrs)
            if files is None:
                result["error"] = "Failed to get diff (REST+MCP)"
            else:
                result["files"] = files
                result["total_additions"] = sum(f.get("additions", 0) for f in files)
                result["total_deletions"] = sum(f.get("deletions", 0) for f in files)

        elif platform == "gitcode":
            files = get_gitcode_diff(repo, number, gitcode_token,
                                     pr.get("head_sha"), pr.get("base_sha"))
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
    """在 daemon 子线程执行 func，主线程等待 timeout 秒，超时返回 None

    ⚠️ 核心防卡死方案:
    - daemon=True: 卡住的线程无法 kill，但 daemon 线程在进程退出时自动清理
    - join(timeout): 主线程最多等待 timeout 秒，超时返回 (None, TimeoutError)
    - 不用 ThreadPoolExecutor + as_completed（单个卡死 PR 会导致整批阻塞）
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
    if len(sys.argv) < 2:
        print("用法: python3 diff_analyze.py <output_dir>")
        print("环境变量: GITEE_PAT, GITCODE_TOKEN")
        sys.exit(1)

    output_dir = sys.argv[1]
    error_log = os.path.join(output_dir, "02_intermediate", "errors.log")
    output_file = os.path.join(output_dir, "02_intermediate", "pr_diffs.json")
    all_prs_file = os.path.join(output_dir, "01_download", "all_prs.json")

    if not GITEE_PAT or not GITCODE_TOKEN:
        print("⚠️ 请设置环境变量 GITEE_PAT 和 GITCODE_TOKEN")
        sys.exit(1)

    with open(all_prs_file, "r") as f:
        all_prs = json.load(f)

    # 读取已有结果（断点续传）
    results = []
    processed_keys = set()
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            results = json.load(f)
        processed_keys = {(r["platform"], r["repo"], r["number"]) for r in results}

    pending = [pr for pr in all_prs
               if (pr["platform"], pr["repo"], pr["number"]) not in processed_keys]

    print(f"=== Diff 分析: 共 {len(all_prs)} PR, 已处理 {len(results)}, 待处理 {len(pending)} ===",
          flush=True)
    print(f"每个 PR 硬超时 {PR_TIMEOUT}s，每 {SAVE_INTERVAL} 个保存一次", flush=True)

    total = len(all_prs)
    success = sum(1 for r in results if not r.get("error"))
    failed = sum(1 for r in results if r.get("error"))

    for i, pr in enumerate(pending):
        idx = len(results)
        progress = f"[{idx+1}/{total}]"

        # daemon 线程 + 30s 硬超时
        result, err = run_with_timeout(
            lambda p=pr: process_pr(p, GITEE_PAT, GITCODE_TOKEN),
            timeout=PR_TIMEOUT,
        )

        if err is not None:
            err_msg = str(err)[:80]
            result = {
                "platform": pr["platform"], "repo": pr["repo"], "number": pr["number"],
                "title": pr.get("title", ""), "state": pr.get("state", ""),
                "html_url": pr.get("html_url", ""),
                "created_at": pr.get("created_at"), "updated_at": pr.get("updated_at"),
                "merged_at": pr.get("merged_at"),
                "files": [], "total_additions": 0, "total_deletions": 0,
                "error": err_msg,
            }
            failed += 1
            log_error(error_log, f"pr_timeout_or_err {pr['platform']}:{pr['repo']}#{pr['number']}: {err_msg}")
            print(f"  {progress} {pr['platform']}:{pr['repo']}#{pr['number']}: {err_msg}", flush=True)
        else:
            results.append(result)
            if result.get("error"):
                failed += 1
                print(f"  {progress} {pr['platform']}:{pr['repo']}#{pr['number']}: ERROR {result['error'][:50]}", flush=True)
            else:
                success += 1
                fc = len(result.get("files", []))
                print(f"  {progress} {pr['platform']}:{pr['repo']}#{pr['number']}: {fc} files +{result['total_additions']}/-{result['total_deletions']}", flush=True)

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
    print(f"\n=== 全部完成: {len(results)} PRs, 成功 {success}, 失败 {failed} ===", flush=True)

    failed_prs = [r for r in results if r.get("error")]
    if failed_prs:
        print(f"\n失败/超时的 PR ({len(failed_prs)} 个):", flush=True)
        for r in failed_prs:
            print(f"  {r['platform']}:{r['repo']}#{r['number']} [{r['state']}] {r['title'][:40]} -> {r['error'][:50]}", flush=True)


if __name__ == "__main__":
    main()

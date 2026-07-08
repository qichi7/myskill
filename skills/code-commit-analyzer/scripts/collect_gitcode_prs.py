#!/usr/bin/env python3
"""阶段2: GitCode PR 收集脚本（REST API, author 过滤）

策略：
1. /user/repos + /user/starred 获取仓库列表，排除 owner==username 的自建仓
2. 逐仓用 /repos/{repo}/pulls?state=all&author={username} 获取 PR（服务端过滤）
3. 兼容响应结构（JSON 数组或 {"data":[]}/{"list":[]}）和字段名（full_name/path_with_namespace）

用法:
    GITCODE_TOKEN=xxx GITCODE_USERNAME=xxx \
    python3 -u collect_gitcode_prs.py {output_dir}

环境变量:
    GITCODE_TOKEN     — GitCode Personal Access Token（必须）
    GITCODE_USERNAME  — GitCode 用户名（必须）

输出:
    {output_dir}/01_download/gitcode_prs.json

⚠️ 关键经验:
- 必须带 author 参数服务端过滤，否则大仓库拉取全部 PR 会超时
- 响应可能是 JSON 数组，也可能包裹在 {"data":[]} / {"list":[]} 中，需兼容
- owner 字段可能是 dict（含 login）也可能不是，需类型检查
- 频率限制 250次/分钟，需 time.sleep(0.3)
- PR 列表返回的 base.sha 不可靠（是默认分支当前 HEAD），阶段3必须重新获取
"""
import sys
import os
import json
import time
import threading
import requests

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
TOKEN = os.environ.get("GITCODE_TOKEN", "")
USERNAME = os.environ.get("GITCODE_USERNAME", "")
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


def get_owner_login(repo):
    """兼容 owner 字段: dict（含 login）或 string 或缺失"""
    owner = repo.get("owner")
    if isinstance(owner, dict):
        return owner.get("login", "")
    if isinstance(owner, str):
        return owner
    return ""


def parse_pr_state(pr):
    """解析 PR 状态，兼容 state=="merged" 和 merged==True 两种"""
    state = pr.get("state", "")
    merged = pr.get("merged", False)
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
    if not USERNAME:
        log_error("GITCODE_USERNAME not set")
        sys.exit(1)

    # Step 1: 获取仓库列表
    log_info("=== Step 1: 获取 GitCode 仓库列表 ===")
    all_repos = []

    for endpoint in ["/user/repos", "/user/starred"]:
        page = 1
        while True:
            url = f"{API_BASE}{endpoint}?per_page=100&page={page}"
            data, err = run_with_timeout_safe(lambda: api_get(url), timeout=60)
            if err:
                log_error(f"{endpoint} page {page}: {err}")
                break
            if not data:
                break
            items = parse_items(data)
            if not items:
                break
            all_repos.extend(items)
            log_info(f"  {endpoint} page {page}: {len(items)} 个仓库")
            if len(items) < 100:
                break
            page += 1
            time.sleep(0.3)

    log_info(f"总计获取 {len(all_repos)} 个仓库（含 starred）")

    # Step 2: 筛选候选仓库（排除 owner==username 的自建仓）
    candidate_repos = []
    seen = set()
    for repo in all_repos:
        full_name = repo.get("full_name") or repo.get("path_with_namespace") or ""
        if not full_name or get_owner_login(repo) == USERNAME or full_name in seen:
            continue
        seen.add(full_name)
        candidate_repos.append(full_name)

    log_info(f"候选仓库 {len(candidate_repos)} 个（排除自建仓）")

    # Step 3: 逐仓获取 PR（author 过滤，state=all）
    log_info(f"=== Step 2: 逐仓获取 PR (author={USERNAME}, state=all) ===")
    all_prs = []

    for i, repo_full in enumerate(candidate_repos):
        page = 1
        repo_pr_count = 0
        while True:
            url = f"{API_BASE}/repos/{repo_full}/pulls?state=all&author={USERNAME}&per_page=100&page={page}"
            data, err = run_with_timeout_safe(lambda: api_get(url), timeout=60)
            if err:
                log_error(f"get_prs {repo_full} page {page}: {err}")
                break
            if not data:
                break
            items = parse_items(data)
            if not items:
                break

            for pr in items:
                pr_number = pr.get("number") or pr.get("id") or pr.get("iid")
                if pr_number is None:
                    continue

                merged_at = pr.get("merged_at") or ""
                created_at = pr.get("created_at") or ""
                updated_at = pr.get("updated_at") or ""

                pr_record = {
                    "platform": "gitcode",
                    "repo": repo_full,
                    "repo_owner": repo_full.split("/")[0] if "/" in repo_full else "",
                    "repo_name": repo_full.split("/")[-1] if "/" in repo_full else repo_full,
                    "number": pr_number,
                    "title": pr.get("title", ""),
                    "state": parse_pr_state(pr),
                    "merged": pr.get("merged", False),
                    "html_url": pr.get("html_url", ""),
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "merged_at": merged_at,
                    "pr_date": merged_at or updated_at or created_at,
                    "user_login": (pr.get("user") or {}).get("login", "") or (pr.get("author") or {}).get("login", ""),
                    "head_sha": (pr.get("head") or {}).get("sha", ""),
                    "base_sha": (pr.get("base") or {}).get("sha", ""),
                }
                all_prs.append(pr_record)
                repo_pr_count += 1

            if len(items) < 100:
                break
            page += 1
            time.sleep(0.3)

        if repo_pr_count > 0:
            log_info(f"  [{i+1}/{len(candidate_repos)}] {repo_full}: {repo_pr_count} 个 PR")

    log_info(f"\n=== GitCode PR 收集完成: 共 {len(all_prs)} 个 PR ===")
    state_counts = {}
    for pr in all_prs:
        state_counts[pr["state"]] = state_counts.get(pr["state"], 0) + 1
    log_info(f"状态分布: {state_counts}")

    output_file = os.path.join(OUTPUT_DIR, "01_download", "gitcode_prs.json")
    with open(output_file, "w") as f:
        json.dump(all_prs, f, ensure_ascii=False, indent=2)
    log_info(f"已保存到 {output_file}")


if __name__ == "__main__":
    main()

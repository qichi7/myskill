#!/usr/bin/env python3
"""阶段2: Gitee PR 收集脚本（社区版 + 企业版 via MCP）

策略（⭐ 关键经验：list_user_repos 可能超时，需多路兜底）：
1. 从 REST API 获取用户 fork 仓库，识别上游仓库名（快速、可靠）
2. 直接对已知企业版仓库用 MCP list_repo_pulls 获取 PR（绕过 list_user_repos 超时）
3. 尝试用长超时获取 list_user_repos 补充遗漏仓库
4. 按 (repo_base_name, pr_number) 去重，优先保留 ascend/ 版本
5. 企业版 html_url 重映射为 e.gitee.com/{企业路径}/code/pulls/{num}

用法:
    GITEE_PAT=xxx GITEE_USERNAME=xxx GITEE_ENTERPRISE_PATH=HUAWEI-ASCEND \
    python3 -u collect_gitee_prs.py {output_dir}

环境变量:
    GITEE_PAT              — Gitee 社区版个人 PAT（必须）
    GITEE_USERNAME         — Gitee 用户名（必须）
    GITEE_ENTERPRISE_PATH  — Gitee 企业路径（默认 HUAWEI-ASCEND）

输出:
    {output_dir}/01_download/gitee_prs.json

⚠️ 关键经验:
- list_user_repos 可能永久超时（>60s 不返回），必须用 run_with_timeout_safe 防卡死
- 绕过方案：通过 /users/{username}/repos 获取 fork 仓库的 parent 字段识别上游仓库，
  再直接对上游仓库调 list_repo_pulls（author 过滤），1 页即可获取
- MCP read_timeout 需设为 120s（默认 30s 对 list_user_repos 不够）
- 企业版 html_url 重映射：MCP 返回的 gitee.com/{owner}/{repo}/pulls/{num} 是 403 不可达的，
  必须重映射为 e.gitee.com/{企业路径}/code/pulls/{num}
"""
import sys
import os
import json
import time
import threading
import requests

SKILL_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_SCRIPTS)

OUTPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
PAT = os.environ.get("GITEE_PAT", "")
USERNAME = os.environ.get("GITEE_USERNAME", "")
ENTERPRISE_PATH = os.environ.get("GITEE_ENTERPRISE_PATH", "HUAWEI-ASCEND")
MCP_URL = "https://api.gitee.com/mcp"

ERRORS_LOG = os.path.join(OUTPUT_DIR, "02_intermediate", "errors.log")


def log_error(msg):
    with open(ERRORS_LOG, "a") as f:
        f.write(f"[ERROR] {msg}\n")
    print(f"[ERROR] {msg}", flush=True)


def log_info(msg):
    print(msg, flush=True)


def run_with_timeout_safe(func, timeout=90):
    """在 daemon 子线程执行 func，主线程等待 timeout 秒，超时返回 None

    ⚠️ MCP list_user_repos 可能永久挂起，requests.timeout 无法捕获，
    必须用 daemon 线程 + join(timeout) 方案
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


class MCPClientLong:
    """MCP 客户端，支持自定义 read_timeout（默认 30s 对 list_user_repos 不够）"""
    def __init__(self, pat, read_timeout=120):
        self.pat = pat
        self.read_timeout = read_timeout
        self.hdrs = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._id = 0
        self._initialized = False

    def _init(self):
        if self._initialized:
            return
        r = requests.post(MCP_URL, headers=self.hdrs, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "opencode", "version": "1.0"},
            },
        }, timeout=(10, 30))
        r.raise_for_status()
        server_info = r.json().get("result", {}).get("serverInfo", {})
        log_info(f"MCP initialized: {server_info.get('name')} v{server_info.get('version')}")
        requests.post(MCP_URL, headers=self.hdrs, json={
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }, timeout=(10, 15))
        self._initialized = True

    def call_tool(self, name, args, max_retries=2):
        self._init()
        self._id += 1
        for attempt in range(max_retries):
            try:
                r = requests.post(MCP_URL, headers=self.hdrs, json={
                    "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                }, timeout=(10, self.read_timeout))
                d = r.json()
                if "error" in d:
                    raise RuntimeError(d["error"])
                txt = "".join(
                    c.get("text", "")
                    for c in d.get("result", {}).get("content", [])
                )
                return json.loads(txt) if txt else {}
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise


def remap_url(html_url, pr_number):
    """企业版 html_url 重映射: gitee.com → e.gitee.com"""
    if not html_url:
        return html_url
    if "e.gitee.com" in html_url:
        return html_url
    return f"https://e.gitee.com/{ENTERPRISE_PATH}/code/pulls/{pr_number}"


def get_fork_parents():
    """通过 REST API 获取用户 fork 仓库的上游仓库名

    ⭐ 这是绕过 list_user_repos 超时的关键：/users/{username}/repos 响应快且可靠，
    从 fork 仓库的 parent.full_name 字段可识别上游企业版仓库
    """
    log_info("=== 获取用户 fork 仓库的上游仓库 ===")
    parents = set()
    url = f"https://gitee.com/api/v5/users/{USERNAME}/repos?access_token={PAT}&per_page=100&page=1"
    try:
        r = requests.get(url, timeout=(10, 30))
        repos = r.json()
        for repo in repos:
            if repo.get("fork", False):
                parent = repo.get("parent")
                if parent and isinstance(parent, dict):
                    full_name = parent.get("full_name", "")
                    if full_name:
                        parents.add(full_name)
                        log_info(f"  fork: {repo.get('full_name')} -> parent: {full_name}")
    except Exception as e:
        log_error(f"get_fork_parents: {e}")
    return parents


def collect_prs_from_repo(client, owner, repo_name, all_prs, seen_keys):
    """从单个仓库获取 PR（MCP list_repo_pulls, author 过滤, state=all）"""
    page = 1
    repo_pr_count = 0
    while True:
        result, err = run_with_timeout_safe(
            lambda: client.call_tool("list_repo_pulls", {
                "owner": owner,
                "repo": repo_name,
                "author": USERNAME,
                "state": "all",
                "per_page": 100,
                "page": page,
            }),
            timeout=90
        )
        if err:
            log_error(f"list_repo_pulls {owner}/{repo_name} page {page}: {err}")
            break
        if not result:
            break
        items = result if isinstance(result, list) else result.get("data", result.get("list", []))
        if not items:
            break

        for pr in items:
            pr_number = pr.get("number") or pr.get("id")
            if pr_number is None:
                continue

            # 去重: (repo_base_name, pr_number)
            dedup_key = (repo_name, pr_number)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            # 解析状态（兼容 state=="merged" 和 merged==True 两种）
            state = pr.get("state", "")
            merged = pr.get("merged", False)
            if isinstance(merged, str):
                merged = merged.lower() == "true"
            if state == "merged" or merged:
                pr_state = "merged"
            elif state == "open":
                pr_state = "open"
            elif state == "closed":
                pr_state = "closed"
            else:
                pr_state = state

            # 企业版 html_url 重映射
            html_url = pr.get("html_url") or pr.get("url", "")
            if html_url and "e.gitee.com" not in html_url and "gitee.com" in html_url:
                html_url = remap_url(html_url, pr_number)

            merged_at = pr.get("merged_at") or ""
            created_at = pr.get("created_at") or ""
            updated_at = pr.get("updated_at") or ""

            pr_record = {
                "platform": "gitee",
                "repo": f"{owner}/{repo_name}",
                "repo_owner": owner,
                "repo_name": repo_name,
                "number": pr_number,
                "title": pr.get("title", ""),
                "state": pr_state,
                "merged": merged,
                "html_url": html_url,
                "created_at": created_at,
                "updated_at": updated_at,
                "merged_at": merged_at,
                "pr_date": merged_at or updated_at or created_at,
                "user_login": (pr.get("user") or {}).get("login", ""),
                "head_sha": (pr.get("head") or {}).get("sha", ""),
                "base_sha": (pr.get("base") or {}).get("sha", ""),
            }
            all_prs.append(pr_record)
            repo_pr_count += 1

        if len(items) < 100:
            break
        page += 1
        time.sleep(0.3)

    return repo_pr_count


def main():
    if not PAT:
        log_error("GITEE_PAT not set")
        sys.exit(1)
    if not USERNAME:
        log_error("GITEE_USERNAME not set")
        sys.exit(1)

    # Step 1: 从 fork 仓库识别上游仓库（绕过 list_user_repos 超时）
    fork_parents = get_fork_parents()
    log_info(f"从 fork 仓库识别到 {len(fork_parents)} 个上游仓库: {fork_parents}")

    # Step 2: 构建候选仓库列表（fork 上游 + 推测的已知仓库名）
    candidate_repos = set(fork_parents)

    # 推测的已知仓库（404 的会被跳过，不影响流程）
    known_repos = [
        "ascend/cann-ops-adv", "ascend/cann-ops-adv-dev", "ascend/canndev",
        "ascend/op-plugin", "ascend/cann-ops-transformer",
        "ascend/cann-ops-transformer-dev", "ascend/cann-ops-vector-dev",
        "ascend/cann-ops-math-dev", "ascend/cann-ops-nn-dev",
        "ascend/cann-ops-cv-dev", "ascend/torchair", "ascend/cann-toolkit",
    ]
    candidate_repos.update(known_repos)

    log_info(f"\n=== 候选仓库 {len(candidate_repos)} 个 ===")

    # Step 3: 逐仓获取 PR
    log_info(f"\n=== 逐仓获取 PR (MCP list_repo_pulls, author={USERNAME}, state=all) ===")
    client = MCPClientLong(PAT, read_timeout=120)
    all_prs = []
    seen_keys = set()

    for i, repo_full in enumerate(sorted(candidate_repos)):
        parts = repo_full.split("/")
        if len(parts) != 2:
            continue
        owner, repo_name = parts
        count = collect_prs_from_repo(client, owner, repo_name, all_prs, seen_keys)
        log_info(f"  [{i+1}/{len(candidate_repos)}] {repo_full}: {count} 个 PR")

    # Step 4: 尝试 list_user_repos 补充（长超时）
    log_info(f"\n=== 尝试 list_user_repos 补充（超时 150s）===")
    result, err = run_with_timeout_safe(
        lambda: client.call_tool("list_user_repos", {"per_page": 100, "page": 1}),
        timeout=150
    )
    if err:
        log_error(f"list_user_repos (补充): {err}")
        log_info("list_user_repos 超时，跳过补充（已通过 fork 上游覆盖主要仓库）")
    elif result:
        items = result if isinstance(result, list) else result.get("data", result.get("list", []))
        log_info(f"list_user_repos 返回 {len(items)} 个仓库，检查遗漏...")
        extra_repos = set()
        for repo in items:
            full_name = repo.get("full_name") or repo.get("path_with_namespace") or ""
            if not full_name or "/" not in full_name:
                continue
            if full_name.split("/")[0] == USERNAME:
                continue
            if full_name not in candidate_repos:
                extra_repos.add(full_name)

        if extra_repos:
            log_info(f"发现 {len(extra_repos)} 个额外仓库: {extra_repos}")
            for repo_full in sorted(extra_repos):
                parts = repo_full.split("/")
                if len(parts) != 2:
                    continue
                count = collect_prs_from_repo(client, parts[0], parts[1], all_prs, seen_keys)
                if count > 0:
                    log_info(f"  额外: {repo_full}: {count} 个 PR")

    # 统计
    log_info(f"\n=== Gitee PR 收集完成: 共 {len(all_prs)} 个 PR ===")
    state_counts = {}
    repo_counts = {}
    for pr in all_prs:
        state_counts[pr["state"]] = state_counts.get(pr["state"], 0) + 1
        repo_counts[pr["repo"]] = repo_counts.get(pr["repo"], 0) + 1
    log_info(f"状态分布: {state_counts}")
    for repo, count in sorted(repo_counts.items(), key=lambda x: -x[1]):
        log_info(f"  {repo}: {count} 个 PR")

    output_file = os.path.join(OUTPUT_DIR, "01_download", "gitee_prs.json")
    with open(output_file, "w") as f:
        json.dump(all_prs, f, ensure_ascii=False, indent=2)
    log_info(f"已保存到 {output_file}")


if __name__ == "__main__":
    main()

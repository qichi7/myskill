#!/usr/bin/env python3
"""Gitee MCP 客户端封装（三步握手 + 工具调用）

用法:
    from mcp_client import GiteeMCPClient
    client = GiteeMCPClient(gitee_pat)
    user_info = client.call_tool("get_user_info", {})
    repos = client.call_tool("list_user_repos", {"per_page": 100, "page": 1})

⚠️ 关键经验:
- 用社区版个人 PAT，企业 token 反而报 "Access token is wrong type"
- timeout 用元组 (connect_timeout, read_timeout)，单一值无法精确控制
- 所有 tools/call 前必须完成三步握手 (initialize → notifications/initialized → 就绪)
"""
import requests
import json
import time

MCP_URL = "https://api.gitee.com/mcp"


class GiteeMCPClient:
    def __init__(self, gitee_pat):
        self.pat = gitee_pat
        self.hdrs = {
            "Authorization": f"Bearer {gitee_pat}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._id = 0
        self._initialized = False

    def _init(self):
        """三步握手: initialize → notifications/initialized"""
        if self._initialized:
            return

        # Step 1: initialize
        r = requests.post(MCP_URL, headers=self.hdrs, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "opencode", "version": "1.0"},
            },
        }, timeout=(10, 20))
        r.raise_for_status()
        server_info = r.json().get("result", {}).get("serverInfo", {})
        print(f"MCP initialized: {server_info.get('name')} v{server_info.get('version')}")

        # Step 2: notifications/initialized (returns 202, no body)
        r = requests.post(MCP_URL, headers=self.hdrs, json={
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }, timeout=(10, 15))
        self._initialized = True

    def call_tool(self, name, args, max_retries=2):
        """调用 MCP 工具，返回解析后的 JSON 结果

        ⚠️ 返回的 additions/deletions 可能是字符串类型如 "10"，调用方需 int() 转换
        ⚠️ patch 字段可能是 dict（含 diff 子字段）而非字符串，调用方需 isinstance 检查
        """
        self._init()
        self._id += 1

        for attempt in range(max_retries):
            try:
                r = requests.post(MCP_URL, headers=self.hdrs, json={
                    "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                }, timeout=(10, 30))
                d = r.json()
                if "error" in d:
                    raise RuntimeError(d["error"])
                txt = "".join(
                    c.get("text", "")
                    for c in d.get("result", {}).get("content", [])
                )
                return json.loads(txt) if txt else {}
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise


def remap_enterprise_url(html_url, enterprise_path="HUAWEI-ASCEND", pr_number=None):
    """将 MCP 返回的社区版 html_url 重映射为企业版可达 URL

    MCP 返回:  https://gitee.com/{owner}/{repo}/pulls/{num}     ← 403 不可达
    重映射为:  https://e.gitee.com/{企业路径}/code/pulls/{num}  ← 200 OK
    """
    if "e.gitee.com" in html_url:
        return html_url
    if pr_number is None:
        # 从 URL 提取 PR number
        import re
        m = re.search(r"/pulls/(\d+)", html_url)
        pr_number = m.group(1) if m else ""
    return f"https://e.gitee.com/{enterprise_path}/code/pulls/{pr_number}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 mcp_client.py <gitee_pat>")
        print("  验证 MCP 连接并打印用户信息")
        sys.exit(1)

    pat = sys.argv[1]
    client = GiteeMCPClient(pat)
    user = client.call_tool("get_user_info", {})
    print(json.dumps(user, ensure_ascii=False, indent=2))

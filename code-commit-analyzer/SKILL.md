---
name: code-commit-analyzer
description: PR 知识提炼技能。从 Gitee（含企业版，通过 MCP server）/GitCode 等平台收集用户全部 PR（含已合入和未合入），代码级深度分析后提炼技术知识点，最终输出两份文件：详细知识清单（knowledge_detail.md）和简历技能点（resume_skills.md）。全程使用指定目录的三个子目录：01_download（下载）、02_intermediate（中间文件）、03_knowledge（详细知识分章），指定目录顶层只保留两个最终文件。支持断点续传。触发：需要分析某人在代码托管平台上的 PR、提炼技术知识用于写简历时。
---

# PR 知识提炼技能

## 一、概述

本技能用于从代码托管平台（Gitee/GitCode等）收集用户**全部 PR（含已合入和未合入）**，进行代码级深度分析后提炼技术知识点，最终输出两份文件用于写简历：
1. `knowledge_detail.md` — 详细知识清单
2. `resume_skills.md` — 浓缩到简历里的技能点

**核心特性**：
- 支持多平台（Gitee、GitCode，可扩展到其他平台）
- 断点续传：通过 `.DONE` / `.PHASE_*` 标记文件跟踪进度
- 并发处理：5个并发请求 PR 数据
- **全状态 PR 收集**：同时处理已合入（merged）和未合入（open/closed）的 PR，分析时标注每个 PR 的合入状态，供用户判断其简历价值
- **代码级深度分析**：不停留在 PR 标题表面，必须分析每个 PR 的实际文件变更和代码修改点
- **严格的目录隔离**：下载/中间文件/详细知识分处三个子目录，顶层只留两份最终文件

**目录结构约定**（{output_dir} 为用户指定目录）：
- `01_download/` — 子目录1：所有网络下载的原始数据（PR 列表、PR 详情 JSON）
- `02_intermediate/` — 子目录2：所有中间文件（diff 分析结果、PR 分类表、进度标记）
- `03_knowledge/` — 子目录3：详细知识分章源文件（按主题拆分的知识点草稿）
- 顶层只有两个文件：`knowledge_detail.md` 和 `resume_skills.md`

---

## 二、完整工作流程

### 阶段1：平台认证与身份获取

**触发条件**：用户提供平台URL（如 `https://gitee.com/xxx` 或 `https://gitcode.com/xxx`）

> **前置原则（令牌/手段优先索要）**：获取用户的全部 PR 必须有认证手段（令牌）支撑。若用户未提供令牌、令牌无效/过期、或某仓库/平台因权限不足无法获取 PR（如 Gitee 企业版仓库 404、API 返回 401/403），**必须优先主动向用户索要获取手段**（重新提供令牌、补充企业授权、提供其他访问方式如导出 PR 列表等），**不可默默跳过、不可仅记 errors.log 后继续**。只有在用户明确表示"无法提供"或"放弃该部分"后，才可跳过并记录。询问时应告知用户：缺少令牌会导致该平台/仓库的 PR 全部遗漏。

**步骤**：
1. **初始化目录**：
   ```bash
   mkdir -p {output_dir}/01_download {output_dir}/02_intermediate {output_dir}/03_knowledge
   ```
2. **询问令牌**：向用户请求该平台的 Personal Access Token
   - 若用户未提供令牌，**必须主动询问**，并说明："无令牌将无法获取任何 PR，分析无法进行"
   - Gitee：设置 → 私人令牌（**社区版个人 PAT 即可**，通过 MCP server 可跨界访问企业版仓库，无需企业 token；详见 §4.1.2）
   - GitCode：设置 → 私人令牌
3. **获取身份信息**：
   ```bash
   # Gitee 社区版
   curl -s "https://gitee.com/api/v5/user?access_token=${TOKEN}"
   
   # Gitee 企业版（MCP get_user_info，三步握手见 §4.1.2）
   
   # GitCode（需要 header）
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/user"
   ```
4. **获取邮箱**（用于匹配 PR）：
   ```bash
   # Gitee
   curl -s "https://gitee.com/api/v5/emails?access_token=${TOKEN}"
   ```

**输出**：用户名、邮箱、用户ID。创建 `{output_dir}/02_intermediate/.PHASE_1` 标记完成。

### 阶段2：PR 收集（含已合入和未合入，下载到子目录1）

**步骤**：
1. **分页获取所有相关仓库**（关注/starred/用户自建公开仓库，用于识别用户活跃的仓库范围），过滤掉 owner 等于用户本人的自建仓库，遍历剩余仓库获取全部状态的 PR：
   ```bash
   # Gitee - 关注 + 自建公开仓库（合并去重，标记 owner==username 的为自建并排除）
   # 注意：subscriptions 端点响应缓慢（实测单页 40-50s），timeout 必须 ≥90s
   curl -s "https://gitee.com/api/v5/user/subscriptions?access_token=${TOKEN}&per_page=100&page={page}"
   curl -s "https://gitee.com/api/v5/users/{username}/repos?access_token=${TOKEN}&per_page=100&page={page}"
   # Gitee 社区版 - 全部 PR（仅在 owner != username 的仓库查询，state=all 含 merged/open/closed）
   curl -s "https://gitee.com/api/v5/repos/{repo}/pulls?access_token=${TOKEN}&state=all&per_page=100&page={page}"
   #   ↑ 若返回 404 {"message":"Not Found Project"}，说明是企业版仓库，转 MCP 流程（见步骤1b）
   
   # GitCode - 自有仓库 + Starred（标记 owner==username 的为自建并排除）
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/user/repos?per_page=100&page={page}"
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/user/starred?per_page=100&page={page}"
   # GitCode - 全部 PR（仅在 owner != username 的仓库查询，state=all 含 merged/open/closed）
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/repos/{repo}/pulls?state=all&per_page=100&page={page}"
   ```

   **1b. Gitee 企业版仓库 PR 收集（MCP 流程，当社区版 REST API 返回 404 时触发）**：

   当社区版 REST API 对某仓库返回 `404 {"message":"Not Found Project"}` 时，该仓库属于 Gitee 企业版，**必须**转 MCP server 流程（§4.1.2）：用 `list_repo_pulls`（author + state=all）获取 PR，并对每个 PR 的 html_url 重映射为 `e.gitee.com/{企业路径}/code/pulls/{num}`。完整 MCP 调用代码和工具参数见 §4.1.2。
2. **按作者筛选**：根据 `user.login` / `user.name` / `user.email` 匹配目标用户提交的 PR
3. **记录 PR 合入状态**：对每个 PR 解析 `state` 字段（`merged`/`open`/`closed`），并在后续 diff 分析与知识提炼中始终携带该状态。未合入（open/closed）的 PR 同样纳入分析，但在 `analysis.md` 与 `resume_skills.md` 中需显式标注其状态，便于用户评估简历可用性。注意：closed 状态需结合 `merged` 标志区分"已合入"与"关闭未合入"，Gitee/GitCode 的字段位置可能不同，需兼容 `pr.get("merged")` / `pr.get("state") == "merged"` 两种判断方式。
4. **Gitee 特殊处理**：
   - `/user/subscriptions` 端点响应缓慢（实测单页 40-50s），**必须**设置 timeout≥90s，重试≥5次，指数退避（2^attempt 秒）
   - 若重试全部失败，记录到 errors.log 并继续后续流程（不阻塞），在阶段5汇总时提示用户可能遗漏 Gitee PR
   - **企业版仓库路由**：subscriptions 返回的上游仓库中，部分属于 Gitee 企业版（如 `ascend/cann-ops-adv-dev`），社区版 REST API `/repos/{repo}/pulls` 对其返回 `404 {"message":"Not Found Project"}`。遇到此响应时，**必须**将该仓库标记为企业版，转 MCP 流程（§4.1.2）用 `list_repo_pulls` 获取 PR，不可跳过。若用户直接提供企业版 URL（`e.gitee.com/{企业路径}/...`），也走 MCP 流程
5. **GitCode 特殊处理**：
   - 频率限制：250次/分钟，需要加延迟（`time.sleep(0.3)`）和重试
   - **响应结构兼容**：GitCode PR 列表 API 的响应可能是直接的 JSON 数组 `[...]`，也可能包裹在 `{"data": [...]}` / `{"list": [...]}` 中，需兼容处理：
     ```python
     items = data if isinstance(data, list) else data.get("data", data.get("list", []))
     ```
   - **仓库字段兼容**：仓库对象的 full_name 字段可能为 `full_name` 或 `path_with_namespace`，需兼容：`repo.get("full_name") or repo.get("path_with_namespace")`
   - **owner 字段兼容**：owner 可能是 dict（含 login）也可能不是，需类型检查：
     ```python
     owner_login = repo.get("owner", {}).get("login", "") if isinstance(repo.get("owner"), dict) else ""
     ```
   - **base_sha 不可靠**：PR 列表 API 返回的 `base.sha` 是默认分支当前 HEAD 而非 PR 合入时的实际 base，**不可直接使用**，阶段3必须通过 PR 详情 API 重新获取
6. **并发优化**：建议用 Python 脚本（`concurrent.futures.ThreadPoolExecutor(max_workers=5)`）并发请求；本文 `curl` 仅用于说明 API 调用，实际执行时可在 Python 中用 `requests` 库替代，统一运行时上下文
7. **保存全部 PR 到子目录1**：
   ```bash
   # PR 列表（含链接、合入状态）
   {output_dir}/01_download/all_prs.json
   ```
   - PR 的 `html_url` 字段直接使用 API 返回值，**不要手动拼接**（GitCode 实际返回 `merge_requests/{num}` 路径而非 `pulls/{num}`）
    - **Gitee 企业版例外**：MCP 返回的 html_url 不可达（403），必须重映射为 `e.gitee.com/{企业路径}/code/pulls/{num}`，详见 §4.1.2
   - 每个 PR 记录中必须保留 `state` 字段，用于阶段3/4标注合入状态

**注意事项**：
- 收集全部状态的 PR（merged/open/closed），未合入的 PR 不忽略，与已合入的一并分析
- 匹配策略：用户名 OR 邮箱 OR login 包含关键词
- 所有下载文件只写入 `01_download/`，不污染其他目录
- **覆盖范围**：排除 owner 等于用户本人的自建仓库，只收集用户向其他仓库提交的 PR（含已合入和未合入，即作为贡献者向上游/他人仓库的全部贡献）
- **获取手段缺失时优先索要**：见阶段1前置原则。禁止在未询问用户的情况下静默跳过整个平台或整类仓库

创建 `{output_dir}/02_intermediate/.PHASE_2` 标记完成。

### 阶段3：代码级 Diff 分析（结果存子目录2）

**重要**：这是深度分析的核心阶段，不能跳过。分析不能停留在 PR 标题/commit message 表面，必须获取每个 PR 的实际文件变更，深入到代码行级修改。

**步骤**：
1. **GitCode diff 获取流程**（注意：PR 列表和详情 API 都不返回 `merge_commit_sha`、`additions`/`deletions`/`changed_files`、`files[]`，必须通过以下三步获取）：

   **Step A — 调 PR 详情 API 获取 head.sha 和 base.sha**：
   ```bash
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/repos/{repo}/pulls/{number}"
   # 从返回的 head.sha 和 base.sha 字段提取（不是 merge_commit_sha，该字段为 None）
   # 注意：PR 列表 API 返回的 base.sha 不可靠（是默认分支当前 HEAD），必须从此详情 API 重新获取
   ```

   **Step B — 用 compare API 获取文件列表和 patch（主路径）**：
   ```bash
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/repos/{repo}/compare/{base_sha}...{head_sha}"
   # 返回 files[]，每个文件含 filename/status/additions/deletions/patch（patch 为字符串）
   # 注意：top-level additions/deletions 为 None，需遍历 files[] 累加各文件的 additions/deletions 得到总数
   # 注意：compare API 对大文件（变更行数约 1000+ 行）可能返回空 patch（patch=""），需 Step C 补全
   ```

   **Step C — 对 patch 为空的文件，用 /pulls/{number}/files 端点补全（备选路径）**：
   ```bash
   curl -s -H "private-token: ${TOKEN}" "https://gitcode.com/api/v5/repos/{repo}/pulls/{number}/files?per_page=100&page={page}"
   # 返回文件列表（支持分页），每个文件含 filename/status/additions/deletions/patch
   # 注意：此端点的 patch 是字典（对象）而非字符串，需从 patch.diff 字段提取实际 diff 内容：
   #   patch_obj = {"diff": "@@ ...实际diff...", "old_path": "...", "new_path": "...",
   #                "too_large": false, "added_lines": 0, "removed_lines": 13, ...}
   #   diff_content = patch_obj.get("diff", "")  # 若为 "The file is empty" 则仍无法获取
   # 注意：即使 too_large=false，某些大文件的 diff 仍可能为 "The file is empty"，此时标注"无 patch，仅文件级统计"
   ```

   **Gitee 社区版 diff 获取流程**（较简单，PR 详情直接返回 files[]）：
   ```bash
   curl -s "https://gitee.com/api/v5/repos/{repo}/pulls/{number}?access_token=${TOKEN}"
   # 返回 files[] 数组（每个文件的 filename/status/additions/deletions/patch，patch 为字符串）
   ```

   **Gitee 企业版 diff 获取流程**（通过 MCP server，当仓库为企业版时使用，详见 §4.1.2）：

   用 MCP 工具 `get_diff_files`（owner, repo, number）直接获取文件级 patch（返回 files[]，patch 为字符串，含 `@@ ... @@` 实际 diff）。MCP 已封装好，无需像 GitCode 那样走 compare + files 两步。完整调用代码见 §4.1.2。

2. **patch 截断策略**：单个文件的 patch 可能非常大（如 20000+ 字符），建议按行截断为前 500 行存入 JSON，并标记 `patch_truncated: true`，避免 JSON 文件过大影响后续读取和分析。

3. **必须记录的信息**（每个 PR）：
   - PR 链接、标题、合入状态（merged/open/closed，未合入的标注"未合入"及原因若可知）
   - PR 日期（已合入的取合入日期，未合入的取创建/更新日期）
   - 修改了哪些文件（文件名列表）
   - 每个文件的增/删行数
   - **代码行级修改点**：读取 patch 内容，定位具体修改的函数/类/代码块，记录：
     - 修改前后的代码逻辑差异
     - 新增了什么逻辑（如新增同步屏障、调整 tiling 参数、修改偏移计算）
     - 删除/替换了什么逻辑（如废弃旧 API、修复错误算法）
     - 修改的技术原因（为什么这么改）
   - 关键修改点：根据文件名和 patch 推断具体修改了什么功能
   - **未合入 PR 的 diff 说明**：open/closed 状态的 PR 仍可通过 head.sha 与 base.sha 获取 compare diff（流程同已合入）；若 PR 已被关闭且源分支删除导致无法获取 diff，则标注"无法获取 diff，仅记录 PR 元信息"
4. **禁止只看 commit message**：commit message 仅作参考，必须以 patch 中的实际代码变更为准进行判断
5. 注意频率限制，分批处理，加延迟和重试（Python 中用 `time.sleep()`）
6. **保存分析结果到子目录2**：
   ```bash
   {output_dir}/02_intermediate/pr_diffs.json
   ```

创建 `{output_dir}/02_intermediate/.PHASE_3` 标记完成。

### 阶段4：PR 分类与深度知识提炼（子目录3按分类建子目录）

**步骤**：
1. **分析 PR 标题与 diff 内容**，按关键词分类。**分类依据以 PR 标题为主、patch 内容为辅**（仅看标题无法判断时再参考 patch）。分类名与目录名映射如下：
   | 分类名（中文） | 目录名（英文） | 关键词示例 |
   |---------------|--------------|-----------|
   | 算子开发 | `operator_dev` | Flash Attention、MXFP8、QSMLA、新增算子 |
   | 特性功能 | `feature` | anti-sparse、RoPE、mask、规格调整 |
   | 性能优化 | `perf_opt` | tiling、UB、L1、vec |
   | Bug 修复 | `bugfix` | 编译、卡死、scale、修复、fix、异常 |
   | 测试 | `test` | pytest、UT、golden、覆盖率 |
   | 代码质量 | `code_quality` | 告警、红线、注释、检视、整改、删除、开源 |
   | 文档 | `doc` | README、设计文档、接口文档 |
   | 基础设施 | `infra` | CI、子仓、构建脚本 |
   | 工具 | `tool` | 脚本、辅助工具 |

   **分类优先级规则**（当一个 PR 同时匹配多个分类时，按以下优先级取最高的）：
   ```
   operator_dev > bugfix > feature > perf_opt > test > code_quality > doc > infra > tool
   ```
   注意：分类关键词不要从 patch 内容中全文匹配（patch 中可能包含各种无关关键词），应优先根据 PR 标题中的关键词判断，仅在标题不含明确关键词时参考修改的文件路径和主要变更内容。

2. **生成 PR 分类索引表**（存子目录2，作为中间产物）：
   ```bash
   {output_dir}/02_intermediate/pr_index.md
   ```
   包含每个 PR 的：编号、标题、日期、合入状态、分类、链接

3. **在子目录3中按分类创建子目录，每个分类一个目录**（目录名见 step 1 映射表）：
   ```bash
   mkdir -p {output_dir}/03_knowledge/{category_dir}/
   # 示例：
   # 03_knowledge/operator_dev/
   # 03_knowledge/bugfix/
   # 03_knowledge/code_quality/
   ```

4. **每个分类目录内进行深度代码分析，生成知识文件**：
   每个分类目录下生成 `analysis.md`，内容必须基于阶段3的代码行级 diff 分析，包含：
   - **技术能力点**：从该分类 PR 的实际代码修改中体现的技术能力
   - **逐 PR 代码级分析**（每个 PR 一节）：
     - PR 链接、标题、日期、**合入状态**（已合入 / 未合入-open / 未合入-closed）
     - **修改文件清单**：按功能分组（kernel/metadata/test/doc 等）
     - **代码修改详情**：具体到函数/代码块，说明改了什么、为什么改、技术手段
       - 例：「`FlashAttentionScoreTiling` 类的 `ComputeTblock` 方法，将 block_cnt 计算从向上取整改为按 (totalLength - 1) / BLOCK + 1，修复尾块越界」
       - 例：「kernel 侧 `Compute` 函数新增 `SetFlag<HardEvent::MTE2_V>(eventID)` 同步屏障，解决 vec 读取 L1 时的读写竞争」
     - **关键技术点**：技术难点、设计决策、可复用经验
   - **分类总结**：该类 PR 体现的核心能力与经验沉淀
   - 禁止用 commit message 概括替代代码分析

5. **并发提炼（优先方案）与串行 fallback（备选方案）**：

   **优先方案 — subagent 并发**：启动子 agent 并发处理（`subagent_type: explore`），每个 agent 负责若干分类目录。分配策略：按各分类的 PR 数量做负载均衡分配（PR 数多的分类独占一个 agent，PR 数少的分类合并给同一 agent），使各 agent 负载尽量均匀。每个 agent 启动时传入其负责的目录名列表，agent 仅处理列表中的分类。每个 agent 必须读取 `02_intermediate/pr_diffs.json` 中对应 PR 的 patch 字段进行代码级分析。

   **备选方案 — 主 agent 串行处理**：若 subagent 调用失败（如返回基础设施错误），**立即降级为主 agent 串行处理**，逐个分类目录生成 `analysis.md`。降级时无需通知用户，直接继续执行。判断 subagent 不可用的条件：task 工具返回错误（如 `no such column: replacement_seq` 等非业务错误）。

   **实现要点**：
   - 先尝试 subagent 并发，若全部失败则降级为串行
   - 串行模式下逐个分类处理，每完成一个立即创建 `.DONE` 标记
   - 无论并发还是串行，分析质量要求一致

6. **完成标记（目录级标签）**：每个分类目录的分析完成后，在该目录下创建 `.DONE` 标签文件：
   ```bash
   touch {output_dir}/03_knowledge/{category}/.DONE
   ```
   断点续传时通过检查 `.DONE` 判断该分类是否已完成，跳过已完成的目录

7. **链接格式**（用于知识文件中引用 PR）：
   - **优先使用 API 返回的 `html_url` 字段**，不要手动拼接 URL
   - 若需手动拼接，各平台格式如下：
   ```
   # Gitee
   PR: https://gitee.com/{owner}/{repo}/pulls/{num}
   
   # GitCode（注意：GitCode 的 PR URL 使用 merge_requests 路径，非 pulls）
   PR: https://gitcode.com/{owner}/{repo}/merge_requests/{num}
   ```

创建 `{output_dir}/02_intermediate/.PHASE_4` 标记完成。

### 阶段5：生成最终两份文件（顶层）

**步骤**：
1. **生成详细知识清单**：`{output_dir}/knowledge_detail.md`
   - 汇总 `03_knowledge/*/` 下各分类子目录中的 `analysis.md`
   - 结构：按分类组织，每类含技术能力点、逐 PR 代码级分析（带 PR 链接）、分类总结
   - 末尾附「核心技术能力总结」和「可复用经验」

2. **生成简历技能点**：`{output_dir}/resume_skills.md`
   - 基于 `knowledge_detail.md` 浓缩
   - 简短精炼，可直接粘贴到简历
   - 格式建议：技能条目 + 一句话佐证（指向代表性 PR）
   - **PR 状态标注**：引用未合入 PR 作为佐证时，应在括号内标注"未合入"（如 `（未合入，见 <链接>）`），如实反映其状态；已合入 PR 无需额外标注
   - 示例：
     ```
      - Ascend C 算子开发：独立完成 Flash Attention / MXFP8 等核心算子内核实现与 Tiling 优化（见 https://gitcode.com/{owner}/{repo}/merge_requests/{num}）
      - 性能调优：掌握 UB/L1 内存规划与 Cube/Vector 流水线优化（见 https://gitee.com/{owner}/{repo}/pulls/{num}）
      - 工程质量：主导代码红线治理与 UT 覆盖率提升（未合入，见 https://gitcode.com/{owner}/{repo}/merge_requests/{num}）
     ```

3. **校验顶层整洁**：精确检查顶层文件名集合，确保只有两个目标文件：
   ```bash
   # 检查缺失
   for f in knowledge_detail.md resume_skills.md; do
     [ -f "{output_dir}/$f" ] || echo "缺失: $f"
   done
    # 检查多余：顶层除两个目标文件外的其他文件（不限扩展名）移入 02_intermediate/
    for f in {output_dir}/*; do
      [ -f "$f" ] || continue
      name=$(basename "$f")
      case "$name" in
        knowledge_detail.md|resume_skills.md) ;;
        *) mv "$f" {output_dir}/02_intermediate/ && echo "已移走多余文件: $name" ;;
      esac
    done
   ```

创建 `{output_dir}/02_intermediate/.PHASE_5` 标记完成。若 `02_intermediate/errors.log` 存在且非空，在最终输出中提示用户存在遗漏的仓库/PR，并附 errors.log 路径。

---

## 三、断点续传机制

### 3.1 标记文件规范

| 标记文件 | 位置 | 含义 |
|----------|------|------|
| `.DONE` | `{output_dir}/03_knowledge/{category}/.DONE` | 该分类目录的代码分析与知识提炼已完成 |
| `.PHASE_{N}` | `{output_dir}/02_intermediate/.PHASE_{N}` | 阶段N已完成（N=1~5） |

### 3.2 启动时扫描逻辑

```bash
echo "=== 检查进度 ==="

# 阶段检查
for phase in 1 2 3 4 5; do
  if [ -f "{output_dir}/02_intermediate/.PHASE_${phase}" ]; then
    echo "阶段${phase}: ✅ 已完成"
  else
    echo "阶段${phase}: ⏳ 待执行"
    break
  fi
done

# 分类目录完成度检查（按目录扫描 .DONE 标签）
echo "=== 分类目录完成度 ==="
for dir in "${output_dir}"/03_knowledge/*/; do
  category=$(basename "$dir")
  if [ -f "$dir/.DONE" ]; then
    echo "  ${category}: ✅ 已完成"
  else
    echo "  ${category}: ⏳ 待处理"
  fi
done
```

### 3.3 恢复执行逻辑

```
如果 .PHASE_1 不存在:
  → 从阶段1开始（平台认证与身份获取）
  
如果 .PHASE_2 不存在:
  → 从阶段2开始（PR 收集）
  
如果 .PHASE_3 不存在:
  → 从阶段3开始（diff 分析）
  
如果 .PHASE_4 不存在:
  → 从阶段4开始（知识提炼）
  → 扫描 03_knowledge/*/ 下无 .DONE 标签的分类目录
  → 仅对未完成的分类目录进行分析，跳过已完成的

如果 .PHASE_4 存在但 .PHASE_5 不存在:
  → 直接进入阶段5（生成最终两文件）

增量追加场景（新增平台或新增 PR，含新合入/新建的）:
  → 不删除已有数据，重新执行阶段2-3，与 01_download/all_prs.json 合并去重
  → 仅对新增 PR 跑阶段3（diff 分析），追加到 02_intermediate/pr_diffs.json
  → 阶段4：扫描 03_knowledge/*/ 下无 .DONE 的新分类目录，仅分析新增部分
  → 阶段5：重新汇总生成两份最终文件
```

---

## 四、平台适配

### 4.1 Gitee 适配

Gitee 分为**社区版**（`gitee.com`）和**企业版**（`e.gitee.com/{企业路径}`）两套隔离系统。社区版 REST API 看不到企业版仓库（返回 `404 "Not Found Project"`），企业版 PR 必须通过 Gitee 官方 MCP server 获取。**关键发现：用社区版个人 PAT 通过 MCP server 可跨界访问企业版仓库，无需企业 token。**

#### 4.1.1 Gitee 社区版（REST API）

| 项目 | 值 |
|------|-----|
| API Base | `https://gitee.com/api/v5` |
| 认证方式 | URL 参数 `?access_token={TOKEN}` |
| 仓库列表 | `/user/subscriptions` (关注) + `/users/{user}/repos` (自建，用于识别并排除) |
| PR 列表 | `/repos/{repo}/pulls?state=all`（仅在 owner != username 的仓库查询，含 merged/open/closed） |
| PR 详情 | `/repos/{repo}/pulls/{number}` — 直接返回 `files[]`（含 patch 字符串）和 `merge_commit_sha`；已合入的 `merge_commit_sha` 非空，未合入的为空 |
| 频率限制 | 较宽松 |
| 超时处理 | `/user/subscriptions` 端点响应缓慢（实测单页 40-50s），**必须** timeout≥90s + 重试≥5次 + 指数退避（2^attempt 秒）；失败后记录 errors.log 不阻塞 |
| PR URL 格式 | `https://gitee.com/{owner}/{repo}/pulls/{num}` |
| 企业版仓库识别 | 社区版 REST API 对企业版仓库返回 `404 {"message":"Not Found Project"}`，遇到此响应应将该仓库标记为企业版，转 §4.1.2 MCP 流程 |

#### 4.1.2 Gitee 企业版（MCP server，✅ 已验证可行）

当 PR 在 `e.gitee.com/{企业路径}/code/pulls` 时（如 `e.gitee.com/HUAWEI-ASCEND/code/pulls?pr[author_id]={uid}`），社区版 REST API 全部 404，必须改走 Gitee 官方 MCP server。

| 项目 | 值 |
|------|-----|
| MCP 端点 | `https://api.gitee.com/mcp`（HTTP+SSE，JSON-RPC 2.0） |
| Server 信息 | `gitee-mcp-remote` v1.0.0，支持 tools（listChanged） |
| 认证方式 | 社区版个人 PAT，Header `Authorization: Bearer {GITEE_PAT}`（**无需企业 token**，企业 token 反而报 "Access token is wrong type"） |
| 协议版本 | MCP `2024-11-05` |
| 仓库发现 | 复用社区版 `/user/subscriptions`（timeout=90s）结果中 404 的仓库；或用户直接提供企业路径（如 `HUAWEI-ASCEND`） |
| PR 列表 | MCP 工具 `list_repo_pulls`（支持 `author`/`state=all`/分页） |
| PR 详情 | MCP 工具 `get_pull_detail`（返回 head.sha/base.sha，**不返回** merge_commit_sha/additions/deletions/changed_files/files[]） |
| Diff 获取 | MCP 工具 `get_diff_files`（返回 files[]，patch 为字符串，类似社区版） |
| 频率限制 | 较宽松，建议 `time.sleep(0.3)` |
| html_url 重映射 | MCP 返回 `https://gitee.com/{owner}/{repo}/pulls/{num}`（403 不可达），**必须重映射**为 `https://e.gitee.com/{企业路径}/code/pulls/{num}`（200 OK） |

**MCP 三步握手流程**（所有 tools/call 前必须完成）：
```python
import requests, json
MCP_URL = "https://api.gitee.com/mcp"
hdrs = {"Authorization": f"Bearer {GITEE_PAT}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}

# Step 1: initialize（返回 serverInfo，确认协议版本）
requests.post(MCP_URL, headers=hdrs, json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "opencode", "version": "1.0"}}
}, timeout=30)

# Step 2: notifications/initialized（通知，返回 202，无响应体）
requests.post(MCP_URL, headers=hdrs, json={
    "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
}, timeout=15)

# Step 3: tools/call（所有工具调用走此方法）
def mcp_call_tool(name, args):
    r = requests.post(MCP_URL, headers=hdrs, json={
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }, timeout=90)
    d = r.json()
    if "error" in d:
        raise RuntimeError(d["error"])
    txt = "".join(c.get("text", "") for c in d.get("result", {}).get("content", []))
    return json.loads(txt)
```

**核心工具及参数**（MCP server 共 25 个工具，以下 4 个为本技能必需）：

| 工具 | 参数 | 用途 | 阶段 |
|------|------|------|------|
| `get_user_info` | （无） | 身份验证，返回 id/login/name/email | 阶段1 |
| `list_repo_pulls` | owner, repo, author, state=`all`, per_page=100, page | PR 列表（支持 author 过滤，state 含 open/closed/merged/all） | 阶段2 |
| `get_pull_detail` | owner, repo, number | PR 详情，返回 head.sha/base.sha/html_url（不返回 files[]） | 阶段3 |
| `get_diff_files` | owner, repo, number | 文件级 diff，返回 files[]（含 filename/additions/deletions/patch 字符串） | 阶段3 |

**html_url 重映射规则**（企业版仓库统一规则）：
```
MCP 返回:  https://gitee.com/{owner}/{repo}/pulls/{num}     ← 403 不可达
重映射为:  https://e.gitee.com/{企业路径}/code/pulls/{num}  ← 200 OK
```
企业路径由用户提供或从 web URL 提取（如 `e.gitee.com/HUAWEI-ASCEND/...` → 企业路径 `HUAWEI-ASCEND`）。阶段2/4/5 输出 PR 链接时，对 Gitee 企业版仓库必须做此重映射，否则用户点链接 403。

**企业 token 不可用说明**：Gitee 企业版 REST API（`api.gitee.com/enterprises/...`）返回 `401 "应用类型不符合此次授权"`（个人 PAT）或 `401 "Only For MCP Gitee Enterprise Application"`（企业 MCP token）；企业 MCP token 在 `api.gitee.com/mcp` 报 `401 "Access token is wrong type"`。**结论：当前 MCP server 是社区版导向，社区 PAT 即可跨界访问企业版仓库，无需企业 token。**

### 4.2 GitCode 适配

| 项目 | 值 |
|------|-----|
| API Base | `https://gitcode.com/api/v5` |
| 认证方式 | Header `private-token: {TOKEN}` |
| 仓库列表 | `/user/repos` + `/user/starred`（自建用于识别并排除，owner==username 的跳过） |
| 仓库字段兼容 | full_name 可能缺失，需兼容 `repo.get("full_name") or repo.get("path_with_namespace")`；owner 字段需类型检查 |
| PR 列表 | `/repos/{repo}/pulls?state=all`（仅在 owner != username 的仓库查询，含 merged/open/closed） |
| PR 列表响应兼容 | 可能是 JSON 数组或 `{"data": [...]}` / `{"list": [...]}`，需 `items = data if isinstance(data, list) else data.get("data", data.get("list", []))` |
| PR 详情 | `/repos/{repo}/pulls/{number}` — **不返回** `merge_commit_sha`、`additions`/`deletions`/`changed_files`、`files[]`，仅返回 `head.sha` 和 `base.sha` |
| base_sha 不可靠 | PR 列表返回的 `base.sha` 是默认分支当前 HEAD，**必须**从 PR 详情 API 重新获取 |
| Diff 获取（主路径） | `/repos/{repo}/compare/{base_sha}...{head_sha}` — 返回 `files[]`（patch 为字符串），但大文件 patch 可能为空字符串；top-level additions/deletions 为 None，需从 files 累加 |
| Diff 获取（备选路径） | `/repos/{repo}/pulls/{number}/files?per_page=100&page={page}` — 分页获取，patch 为字典对象，需从 `patch.diff` 提取；支持 `too_large` 标志 |
| 频率限制 | 250次/分钟，需要 `time.sleep(0.3)` 和重试 |
| PR 匹配 | 按 user.name / user.email / user.login 多字段匹配 |
| PR URL 格式 | 优先用 API 返回的 `html_url`（实际为 `merge_requests/{num}` 路径）；手动拼接为 `https://gitcode.com/{owner}/{repo}/merge_requests/{num}` |

### 4.3 扩展新平台

新增平台需要实现：
1. `get_user_info(token)` → username, email, user_id
2. `get_all_repos(token)` → repo list（排除 owner==username 的自建仓库）
3. `get_all_prs(token, repo, author)` → pr list（含全部状态 merged/open/closed，每条携带 state 字段）
4. `get_pr_detail(token, repo, number)` → head.sha, base.sha（用于 compare API）
5. `get_pr_diff(token, repo, number, base_sha, head_sha)` → diff stats + patch（主路径用 compare API，备选用 files 端点；必须返回 patch，否则代码级分析无法进行）
6. `build_links(repo, pr_num, html_url)` → URL 生成规则（优先用 API 返回的 html_url）

**MCP 协议平台实现要点**（如 Gitee 企业版，详见 §4.1.2）：
- 当平台提供 MCP server 而非 REST API 时（或 REST API 对部分仓库 404），需实现 MCP 客户端封装：
  - `mcp_initialize()` — 三步握手（initialize → notifications/initialized → 就绪）
  - `mcp_call_tool(name, args)` — 统一工具调用入口，解析 JSON-RPC 响应的 `result.content[].text`
- 上述 6 个接口用 MCP 工具映射实现（如 `get_user_info` → MCP `get_user_info`，`get_all_prs` → MCP `list_repo_pulls`，`get_pr_diff` → MCP `get_diff_files`）
- 认证：MCP server 通常用 `Authorization: Bearer {token}` header，token 类型需验证（Gitee MCP 用社区 PAT，企业 token 反而不可用）
- `build_links` 需注意 MCP 返回的 html_url 可能不可达（如 Gitee 企业版需重映射到 `e.gitee.com`），必须做 URL 重映射

---

## 五、输出文件结构

```
{output_dir}/
├── knowledge_detail.md          # ⭐ 最终产出1：详细知识清单
├── resume_skills.md             # ⭐ 最终产出2：简历技能点
│
├── 01_download/                 # 子目录1：下载
│   └── all_prs.json             # 全部 PR 原始数据（含已合入和未合入）
│
├── 02_intermediate/             # 子目录2：中间文件
│   ├── pr_diffs.json            # diff 分析结果（含 patch）
│   ├── pr_index.md              # PR 分类索引表
│   └── .PHASE_{1-5}             # 阶段进度标记
│
└── 03_knowledge/                # 子目录3：按分类建子目录
    ├── operator_dev/         # 每个分类一个目录
    │   ├── analysis.md          # 代码级深度分析（逐 PR）
    │   └── .DONE                # ✅ 该分类已完成（断点续传标签）
    ├── bugfix/
    │   ├── analysis.md
    │   └── .DONE
    ├── code_quality/
    │   ├── analysis.md
    │   └── .DONE
    └── .../                     # 其他分类
```

**硬性约束**：`{output_dir}/` 顶层只能有两个文件（`knowledge_detail.md`、`resume_skills.md`），其余一切文件必须落在三个子目录内。

---

## 六、使用示例

### 6.1 首次分析

```
用户: 分析 https://gitee.com/huipengcheng71 的 PR，提炼简历技能点
Bot: 请提供 Gitee 的 Personal Access Token
用户: xxxxxx
Bot: [执行阶段1-5，最终输出 knowledge_detail.md 和 resume_skills.md]
```

### 6.2 断点续传

```
用户: 继续之前的分析
Bot: [扫描 .DONE 和 .PHASE 标记]
     阶段1-3: ✅ 已完成
     阶段4: 2/6 知识章节已完成，继续生成剩余4个
     [从断点继续]
```

### 6.3 多平台

```
用户: 也分析一下 GitCode https://gitcode.com/huipengcheng
Bot: 请提供 GitCode 的 Personal Access Token
用户: yyyyyy
Bot: [对新平台执行阶段1-5，输出到独立的 {output_dir}/]
```

### 6.4 Gitee 企业版

```
用户: 分析 https://e.gitee.com/HUAWEI-ASCEND/code/pulls?pr[author_id]=14766376 的 PR
Bot: 请提供 Gitee 的 Personal Access Token（社区版个人 PAT 即可，通过 MCP server 可访问企业版仓库）
用户: xxxxxx
Bot: [识别到企业版 URL，走 MCP server 流程（§4.1.2）：
      1. MCP get_user_info 验证身份
      2. subscriptions 获取仓库列表（社区版 REST API），404 的仓库转 MCP
      3. MCP list_repo_pulls 获取企业版仓库全部 PR（state=all）
      4. MCP get_diff_files 获取文件级 patch
      5. html_url 重映射为 e.gitee.com/HUAWEI-ASCEND/code/pulls/{num}
      最终输出 knowledge_detail.md 和 resume_skills.md]
```

---

## 七、注意事项

> 本节仅列全局性注意事项。平台特定规则（如 Gitee 企业版 MCP 流程、令牌索要原则、html_url 重映射）详见 §4.1.2 和阶段1前置原则。

1. **令牌安全**：令牌仅在运行时使用，不写入任何文件（除非用户明确要求）
2. **频率限制**：GitCode 250次/分钟，需要加 `time.sleep()` 和重试机制
3. **大仓库处理**：PR 数多的仓库需要分页获取，注意超时
4. **并发控制**：建议5个并发，避免API频率限制
5. **编码问题**：PR 标题可能包含中文、emoji，确保 UTF-8 处理
6. **代码级深度分析**：分析不能停留在 PR 标题表面。必须获取每个 PR 的文件级 diff 数据，记录修改了哪些文件、每个文件的具体修改点（如修改了什么参数、增加了什么逻辑、修复了什么 bug）、以及修改的技术原因。这是区分"表面分析"和"深度分析"的关键
7. **简历导向**：`resume_skills.md` 必须简短精炼、可直接用于简历，每条技能配一句佐证并指向代表性 PR；`knowledge_detail.md` 提供完整细节支撑
8. **errors.log 日志规范**：errors.log 统一使用追加模式（`mode="a"`），**只记录 ERROR/WARNING 级别**日志（如 API 失败、超时、跳过的仓库/PR），INFO 级别日志输出到控制台即可。每次新执行时在 errors.log 末尾追加，不覆盖已有日志。阶段5校验时只检查 errors.log 中是否存在 ERROR 级别记录来判断是否有遗漏项。
9. **subagent 可用性**：阶段4的并发提炼依赖 subagent，但 subagent 可能因基础设施问题不可用。若调用失败，自动降级为主 agent 串行处理，不影响最终产出质量。

---

## 八、错误处理指引

| 异常情况 | 处理方式 |
|----------|----------|
| 未提供令牌 | 立即主动询问用户（见阶段1前置原则） |
| Token 无效或过期 | 立即请用户重新提供令牌，不继续后续阶段 |
| 用户无仓库 | 提示"未找到仓库"，结束流程 |
| 用户无 PR | 提示"未找到任何 PR（含已合入和未合入）"，结束流程 |
| 某平台/仓库 401/403 权限不足 | 暂停并主动索要获取手段（见阶段1前置原则），用户明确放弃后才跳过并记录 |
| API 返回 4xx（非 401/403/404 Not Found Project） | 记录错误，跳过该仓库/PR，继续处理其他 |
| API 返回 5xx | 指数退避重试（最多3次），仍失败则跳过并记录 |
| 网络超时 | timeout≥60s + 重试2次 + 指数退避，仍失败则跳过该请求并记录到 `02_intermediate/errors.log` |
| Gitee subscriptions 超时 | 重试5次+指数退避（timeout≥90s，实测单页 40-50s），全部失败后记录 errors.log 并继续（不阻塞流程），阶段5提示用户可能遗漏 |
| Gitee 社区版 REST API 404 "Not Found Project" | 该仓库属于 Gitee 企业版，**不可跳过**，必须转 MCP server 流程（§4.1.2）用 `list_repo_pulls` 获取 PR，否则会遗漏全部企业版 PR |
| MCP tools/call 401 "Access token is wrong type" | 当前使用的 token 类型不对（如企业 MCP token），换社区版个人 PAT 重试；社区 PAT 通过 MCP 可访问企业版仓库 |
| 企业版 REST API 401 "应用类型不符合此次授权" / "Only For MCP Gitee Enterprise Application" | 个人 PAT 无权访问企业版 REST API，企业 MCP token 也仅限特定场景；**不要走 REST API**，改走 MCP server（`api.gitee.com/mcp`）+ 社区 PAT |
| subagent 并发调用失败 | 自动降级为主 agent 串行处理，不记录为错误 |
| compare API patch 为空 | 用 `/pulls/{number}/files` 端点补全；仍为空则标注"无 patch，仅文件级统计" |

所有跳过的仓库/PR 均以 ERROR 级别记录到 `02_intermediate/errors.log`（追加模式，不覆盖），阶段5汇总时检查 ERROR 记录并提示用户存在遗漏项。

> **重要原则**：上表中"跳过并记录"仅适用于**单个仓库/PR 级别**的失败（如某仓库 API 5xx、某 PR diff 获取失败）。对于**平台级或整类仓库级**的获取失败（如整个平台令牌缺失/无效、Gitee 企业版全部仓库无法访问），**必须先向用户索要获取手段**，不可直接跳过——否则会导致大面积 PR 遗漏。

# ISSUE: wyt backup_enhancement 合入决策

- **状态**：Open / 待决
- **登记日期**：2026-04-24
- **目标分支**：`vincent/skill-enhancement`（从 `vincent/north-api-solution-a` 改名 + 合入 wyt 改动）
- **来源分支**：`wyt/backup_enhancement`（`https://gitcode.com/wyt122/agent-runtime-wyt.git`）
- **共同祖先**：upstream commit `a5ae32a`（比我们的起点 `d8fefe5` 旧）
- **wyt commit**：`fd4790c` "chore: sync local runtime state to backup_enhancement"（22 文件，+1263/-398 行）

---

## wyt 的整体重构意图（调研结论）

wyt 在做**"模型驱动 + 沙箱脚本化"的架构精简**。

### 老架构（HEAD / `a5ae32a` 附近）
- 每个业务动作一个独立 tool：`query_balance`、`transfer`、`buy_wealth` 等
- `VersatileInterruptRail` 按 tool 名分发
- `AskUserRail` 单独处理 ask_user 中断

### 新架构（wyt）
- **只剩 2 个工具**：
  - `call_versatile(query_description, query_intent, query_response_analysis_scripts)` ——通用业务工作流入口
  - `ask_user(question)` —— 追问
- `VersatileInterruptRail` 只拦截 `call_versatile`，按 `query_intent` 分发
- `query_response_analysis_scripts` 指向 `skills/<skill_name>/scripts/xxx.py`，沙箱里跑解析脚本
- `AskUserRail` 删除；`ask_user` 变成普通 tool
- **3 个新 skill**（每个包含 `SKILL.md` + `scripts/`）：
  - `rebuild_product_recommend_skill`
  - `rebuild_product_select_skill`
  - `model_driven_fund_planning_skill`
- LLM 用 `read_file` 按需读 SKILL.md 学习工具参数模板

这是一次扩展性升级：**新增业务 skill 不用改 agent 内核**，只添加 skill 目录 + SKILL.md + 沙箱脚本。

### 与 Solution A 的正交性

| 层 | 我的 Solution A | wyt backup_enhancement | 冲突？ |
|---|---|---|---|
| 北向协议（wrap_* / user_router / versatile_proxy） | 大改 | 不碰 | ✅ 无冲突 |
| executor.py 编排 | 修 3 个 helper + 补 import | 改 body 写入 + 加改写函数 + 去 params | 🔴 有冲突 |
| events.py | 加 PlanningExecutionProcessEvent | 不加（他不发 planning 事件） | 🟡 小冲突 |
| agents/EDPAgent/ 业务代码 | 不碰 | 大面积重构 | ✅ 无冲突（HEAD 没动） |
| agent.py 导入 | 加 SummaryEvent 等 | 减子集 | 🟡 小冲突 |

---

## 3 个决策点

### Q1: `_rewrite_recommend_delegate` intent 改写

**代码片段**（wyt 新增）：
```python
def _rewrite_recommend_delegate(intent, task_description):
    """临时兼容旧链路：推荐首跳改写为平台历史上可识别的入口。"""
    if intent != "理财推荐":
        return intent, task_description
    normalized_query = (task_description or "").strip()
    if not normalized_query or normalized_query == "推荐理财产品":
        normalized_query = "请推荐低风险理财产品"
    return "理财选品购买", normalized_query
```

**意图**：LLM 调 `call_versatile(query_intent="理财推荐", ...)` 后，平台侧实际的工作流引擎只认识老 intent `"理财选品购买"`，所以在 executor 层做一次改写。

**影响**：只影响 `query_intent == "理财推荐"` 的调用；其他 intent 不受影响。

**HEAD 没有这段代码**。HEAD 的 `_call_versatile_adapter` 直接用 `delegate.task_description` 和 `delegate.intent` 透传给 VA。

**建议**：✅ **保留 wyt 的改写逻辑**（业务兼容代码，不影响我们的 Solution A）。合并时需要把改写后的 `effective_intent/effective_query` 同时应用到 `body.custom_data.inputs`（HEAD 逻辑）和 `body.input`（wyt 和 HEAD 共有逻辑）。

**合并后代码草案**：
```python
effective_intent, effective_query = _rewrite_recommend_delegate(
    delegate.intent, delegate.task_description,
)
if effective_intent != delegate.intent or effective_query != delegate.task_description:
    logger.info("[Executor] 推荐入口临时改写：intent={} -> {}, query={!r} -> {!r}",
                delegate.intent, effective_intent,
                delegate.task_description, effective_query)

params = cached.get("params", {})
if "custom_data" in body and isinstance(body["custom_data"], dict):
    custom_data = dict(body["custom_data"])
    if "inputs" in custom_data and isinstance(custom_data["inputs"], dict):
        inputs = dict(custom_data["inputs"])
        inputs["query"] = effective_query      # ← 用 rewrite 后的
        inputs["intent"] = effective_intent    # ← 用 rewrite 后的
        custom_data["inputs"] = inputs
    body["custom_data"] = custom_data

input_section = dict(body.get("input") or {})
input_section["query"] = effective_query
input_section["intent"] = effective_intent
body["input"] = input_section
```

然后在 `_build_va_message` 调用处也改为 `query=effective_query`，在 log 里用 `effective_intent`。

---

### Q2: `_continue_versatile_adapter` 续轮 body 来源

**HEAD 行为**：
```python
cached = await self._redis.get_json(session_request_key(conv_id)) or {}
first_body = cached.get("body", original_body)    # 从 Redis 取首轮缓存 body
params = cached.get("params", {})
body = dict(first_body)
```

**wyt 行为**（注释明确）：
```python
# 对齐 YGQ：续轮直接使用当前请求携带的 body，确保 buyStatus/tranNo 等
# 当前轮输入能透传给下游工作流，而不是回退到首轮缓存 body。
body = dict(original_body)                         # 直接用当前请求 body
```

**业务含义**：
- HEAD 假设续轮要"复用首轮请求 body"（这样 Versatile 看到的 body 跟首轮一致）
- wyt 要求"用当前轮的 body"（因为当前轮的 buyStatus / tranNo 等字段对工作流必要）

**典型场景**：用户购买理财，首轮提交选品信息 + 金额 → VA 中断等确认；第二轮用户按按钮传 buyStatus=CONFIRMED → VA 续轮拿到 buyStatus 才能进下一个节点。HEAD 的行为会丢掉 buyStatus（用首轮 body 覆盖）；wyt 的行为保留。

**wyt 的是正解**。HEAD 的行为是一个隐藏 bug。

**建议**：✅ **采用 wyt 的"用当前 body"**。需要的话把 `intent/query` 也显式写入 body：

```python
# 对齐 YGQ：续轮直接用当前请求 body，保留 buyStatus/tranNo 等当前轮字段
body = dict(original_body)

# 对齐 Q1：续轮同样应用 intent 改写 + custom_data.inputs 双写
# （实际上续轮的 user_input 不带 intent，这段看情况保留；如果 Rail 传了 intent 再说）
body["stream"] = True
```

---

### Q3: `_build_va_message` 是否保留 `params` 参数

**HEAD 签名**：
```python
def _build_va_message(self, query, headers, body, task_id="", conv_id="", params=None):
```

**wyt 签名**：
```python
def _build_va_message(self, query, headers, body, task_id="", conv_id=""):
```

**差异**：wyt 去掉了 `params=`。

**影响链路**：
- `params` 在 HEAD 里从 `cached.get("params", {})` 读出，作为 HTTP query params 传给 Versatile
- wyt 架构下 `call_versatile` 所有参数都通过 body（`query_description/query_intent/query_response_analysis_scripts`）传递，**不再需要 URL query params**

**建议**：🟡 **保留 HEAD 的 params 参数不删**。理由：
- wyt 的新架构确实不需要 params，但删除是破坏性变更（如果有其他调用方依赖）
- 保留 params 参数即使不传也无害（默认 `None`）
- 将来 wyt 确认真的不用再删

合并时：
- `_build_va_message` 签名保持 HEAD（带 `params=None`）
- `_call_versatile_adapter` 里仍然传 `params=params`（HEAD 行为）
- `_continue_versatile_adapter` 如果改成用 current body，同样保留 params（从 cached 读或默认空）

---

### 附加发现：wyt 的 `final_result` 改进

**wyt 在 `_call_versatile_adapter` 里新增**：
```python
final_result: dict | None = None
# ...
if self._extract_end_node(event) is not None:
    has_end_node = True
    final_result = result          # ← wyt 新加
# ...
cascade = {"workflow_result": qa_result} if qa_result is not None else final_result
```

**语义**：当 `va_workflow_result_node` 未配置（→ `qa_result is None`）但检测到 End node 时，cascade 带 End node 本身而不是 `{"workflow_result": None}`。

**建议**：✅ **保留 wyt 的 final_result 改进**（是一个兜底小改进）。

---

### 附加发现：`versatile_adapter/adapter/executor.py` 被 wyt 删了 13 行

**已 diff**：wyt 删掉的是 `VersatileAdapterExecutor.execute()` 开头"**先发送 TASK_STATE_SUBMITTED Task 事件**"的代码块：

```python
# wyt 删除的代码：
# 先发送 Task 事件（a2a-sdk 1.0.0 要求）
from a2a.types.a2a_pb2 import Task, TaskStatus, TaskState
user_message = context.message
if task_id and conv_id and user_message:
    await event_queue.enqueue_event(
        Task(
            id=task_id,
            context_id=conv_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=[user_message],
        )
    )
```

**分析**：
- 这段是 a2a-sdk 1.0.0 的协议要求——服务端先回一个 `Task(SUBMITTED)` 告诉客户端"任务已接收"，再继续发 `TaskArtifactUpdateEvent`。
- 代码注释明确写了 "a2a-sdk 1.0.0 要求"。
- wyt 的 commit 没写他为什么删（他的 commit 消息是 `chore: sync local runtime state`，太泛）。
- 可能的删除原因（都是推测）：
  - (a) 他实测发现 DefaultRequestHandler 或 client 已经处理了 task submit，不需要手动发
  - (b) 他观察到这个 Task 事件被 a2a_service 错误处理（比如被 `_parse_stream_event` 误识别）
  - (c) 他的 a2a-sdk 版本变了，不再要求这步
- 删除是**破坏性变更**；保留是零风险。

**建议**：🟡 **保留 HEAD 行为（不采纳 wyt 的删除）**。合并时冲突自动解（不是同一块代码），手动选 HEAD 版本即可。后续如果 wyt 实测证明 SUBMITTED 事件有害，再单独讨论删除。

**⚠️ 待核实 V-1：a2a-sdk 版本要求与实际行为**

当前仓库里的 a2a-sdk 版本声明**互相不一致**：

| 位置 | 声明版本 |
|---|---|
| `applications/a2a_service/pyproject.toml` | `a2a-sdk==1.0.0`（正式版） |
| `applications/versatile_adapter/pyproject.toml` | `a2a-sdk[http-server]==1.0.0`（正式版） |
| `applications/a2a_service/agents/EDPAgent/pyproject.toml` | `a2a-sdk==1.0.0a1`（alpha 1） |
| `deployment/Dockerfile`（store 主数据） | 强制装 `a2a-sdk==1.0.0a1`，注释 *"openjiuwen 0.1.11 依赖 a2a-sdk==1.0.0a0，但本项目要求 1.0.0a1"* |
| 实际运行（container 里） | **1.0.0a1**（Dockerfile 的 `--reinstall-package` 说了算）|

线上实跑的是 **1.0.0a1**。但 pyproject 里有地方写 `1.0.0`，随着 pip 语义，如果某天 a2a-sdk 真的发了 `1.0.0` 正式版，跑 `uv sync` 装出的会是不同版本，行为可能变化。

HEAD 那段"先发送 `TASK_STATE_SUBMITTED` Task 事件"代码的注释说"**a2a-sdk 1.0.0 要求**"，但：

1. 当前实际跑的是 `1.0.0a1`（alpha），不是 `1.0.0`（正式）
2. alpha 和正式版 API 可能不一致
3. wyt 删这段也许是他那边实测了某个版本后发现 SDK 自己处理了 Task 事件

**需要核实的问题**：

- Q-a：**当前实际跑的 a2a-sdk 版本**到底该定为 `1.0.0` 还是 `1.0.0a1`？`1.0.0` 正式版已经发布了吗？
- Q-b：如果服务端**不手动**发 `Task(SUBMITTED)`，`DefaultRequestHandler` 或 `AgentExecutor` 抽象层会自动补发吗？（查 a2a-sdk 源码）
- Q-c：`1.0.0a1` vs `1.0.0` 正式版在 Task 事件协议上有差异吗？
- Q-d：`pyproject.toml` 里的版本声明是否需要统一（目前三处写 1.0.0、一处写 1.0.0a1，外加 Dockerfile 强装 1.0.0a1）？
- Q-e：升级路径是什么？等 a2a-sdk 出正式 1.0.0 了再一步到位，还是先把所有 pyproject 对齐到 1.0.0a1？

**行动项**：
- 核实上述 a2a-sdk 版本与协议细节后，再决定 wyt 删除 SUBMITTED 事件块是否正确
- 统一仓库内各处 pyproject.toml 的 a2a-sdk 版本声明，消除歧义
- Dockerfile 的强装行为要么移到 pyproject 里，要么在文档里明确它是**唯一版本 source of truth**

---

## 冲突分解（4 个文件，7 个冲突块）

| 文件 | 冲突块 | 建议 |
|---|---|---|
| `common/events.py` | 1 块（InterruptStartEvent / PlanningExecutionProcessEvent 分类注释）| 保留 HEAD（超集）|
| `agents/EDPAgent/agent.py` | 1 块（events 导入列表）| 保留 HEAD（超集）|
| `orchestrator/executor.py` | 4 块 | 按 Q1/Q2/Q3 决策逐块合并 |
| `versatile_adapter/adapter/executor.py` | 0 块（Auto-merge 成功）| 但要 diff 看 wyt 删了啥 |

---

## wyt 改动的 EDPAgent 业务代码去向（重要：主数据同步）

按已保存的 memory `reference_edpagent_source_of_truth`，EDPAgent 业务代码的主数据在 `agent-store-zhl/community/EDPAgent/`。

wyt 改的这批文件在 `agent-runtime/applications/a2a_service/agents/EDPAgent/`（**派生副本**），合并后**必须同步到 store 主数据**：

需要同步到 `agent-store-zhl/community/EDPAgent/` 的文件：
- `AgentRule.md`（修改）
- `agent.py`（修改）
- `agent_rule.py`（修改）
- `prompt.py`（修改）
- `rail/__init__.py`、`rail/execution_limit_rail.py`、`rail/versatile_interrupt_rail.py`（修改）
- `rail/ask_user_rail.py`（**删除**）
- `tool/__init__.py`、`tool/ask_user.py`、`tool/call_versatile.py`（新增 / 修改）
- `tool/query_balance.py`、`tool/transfer.py`（**删除**）
- `skills/rebuild_product_recommend_skill/`、`skills/rebuild_product_select_skill/`、`skills/model_driven_fund_planning_skill/`（**全新**）
- `test/index.html`（如果也在 store 有对应）

合并到 runtime 后需要 **手动 rsync 一次这些文件到 store** 以持久化，否则下次 `deployment/build.sh` 用 store 主数据跑构建，这些改动会被 store 的旧版本覆盖。

---

## 合并流程（待决策确认后执行）

1. 从 `vincent/north-api-solution-a` 改名为 `vincent/skill-enhancement`（local + myfork）
2. 在新分支上 `git cherry-pick fd4790c`
3. 按 Q1-Q3 决策解 3 个文件的冲突（主要是 executor.py 4 个冲突块）
4. 跑完整 pytest 套（a2a_service 103 / versatile_adapter 8）
5. Docker 重建 + 跑 E2E 实请求，确认北向输出 + call_versatile 入口都工作
6. 同步 EDPAgent 业务代码到 store 主数据（rsync）
7. commit + push 到 `myfork/vincent/skill-enhancement`

---

## 等待的输入

请你确认：

- **Q1 合并**：采用 `_rewrite_recommend_delegate` + 保留 HEAD 的 `body.custom_data.inputs` 双写？（建议 ✅）
- **Q2 续轮 body**：用 wyt 的"当前请求 body"取代 HEAD 的"首轮缓存 body"？（建议 ✅）
- **Q3 `_build_va_message.params`**：保留还是删除？（建议保留 🟡）
- **Versatile_adapter executor 的 -13 行**：已确认 wyt 删的是 `TASK_STATE_SUBMITTED` 事件发射块。建议保留 HEAD（不删）。
  - 关联 **待核实 V-1**（a2a-sdk 版本与协议要求）——在核实清楚前，保守保留 HEAD。
- **业务代码同步 store**：合并完由 Claude 自动 rsync 过去，还是你手动处理？

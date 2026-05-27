# Tasks: EDPAgent 作为自进化推理后端

**Plan:** [plan.md](./plan.md)
**Spec:** [edp-agent-backend.md](../../specs/edp-agent-backend.md)

---

## Vertical Slice 1: Config Foundation (no behavior change)

### Task 1.1: Add config fields for edp-agent

**Files:** `evolution/core/config.py`
**Depends on:** nothing

在 `EvolutionConfig` 中新增 `agent_framework_path` 和 `edp_agent_path` 字段，`inference_mode` 类型扩展为包含 `"edp-agent"`。

**Acceptance criteria:**
- `EvolutionConfig()` 创建时 `agent_framework_path` 默认值为 `None`
- `edp_agent_path` 默认值为当前项目中 `edp_agent/` 目录的自动发现路径
- 现有代码不改一行即可正常运行
- `inference_mode` 类型字符串支持 `"edp-agent"`（无运行时枚举校验打破）

**Verification:**
```bash
python -c "from evolution.core.config import EvolutionConfig; c = EvolutionConfig(); print(c.inference_mode, c.agent_framework_path, c.edp_agent_path)"
```

---

### Task 1.2: Add edp-agent to CLI choice

**Files:** `evolution/skills/evolve_skill.py`
**Depends on:** Task 1.1

在 `--inference` click.Choice 中新增 `"edp-agent"`。

**Acceptance criteria:**
- `--help` 显示三种选项：`single-turn`、`hermes-agent`、`edp-agent`
- `--inference edp-agent --dry-run` 显示 `Inference: edp-agent`

**Verification:**
```bash
python -m evolution.skills.evolve_skill --skill arxiv --inference edp-agent --dry-run
```

---

## Vertical Slice 2: reload_agent_rule (EDPAgent 最小改动)

### Task 2.1: Add reload_agent_rule() to edp_agent/agent.py

**Files:** `edp_agent/agent.py`
**Depends on:** nothing

在 `edp_agent/agent.py` 末尾新增 `reload_agent_rule(new_body: str) -> None` 函数。使用模块级 `_agent_rule` 单例，保留 frontmatter，替换 `markdown_body`，重建 system prompt 并调用 `agent.configure()`。

**Acceptance criteria:**
- 从 `_agent_rule` 保留所有 frontmatter 字段（scope/limits/scripts/todolist_steps），仅替换 `markdown_body`
- 重建 `system_prompt = new_body.strip() + "\n\n" + build_system_prompt()`
- 调用 `agent.configure(config.configure_prompt_template([...]))`
- 若 `_agent` 或 `_agent_rule` 为 None（未初始化），记录 warning 并 return（不抛异常）
- 若 `_agent_rule.markdown_body` 变更影响了 max_iterations，同步更新 config
- 函数不超过 20 行

**Verification:**
- 单元测试：mock `_agent` + `_agent_rule`，调用 `reload_agent_rule("new body")`，断言 `agent.configure` 被调用且 prompt template 包含 "new body"
- 单元测试：`_agent` 为 None 时不抛异常

---

## Vertical Slice 3: run_edp_agent (核心适配逻辑)

### Task 3.1: Add run_edp_agent() to skill_module.py

**Files:** `evolution/skills/skill_module.py`
**Depends on:** Tasks 1.1, 2.1

在 `skill_module.py` 中新增 `run_edp_agent(skill_text, task_input, config) -> dict` 函数，实现完整的 EDPAgent 推理流程。

**核心实现：**

```
run_edp_agent(skill_text, task_input, config)
  → 1. sys.path.insert (agent_framework_path)
  → 2. 首次: asyncio.run(_ensure_initialized())
  → 3. reload_agent_rule(skill_text)
  → 4. conv_id = str(uuid.uuid4())
  → 5. asyncio.run(_collect_stream(conv_id, task_input))
  → 6. release(conv_id)
  → 7. return {"output", "messages", "completed"}
```

**Acceptance criteria:**
- 返回统一 dict 格式：`{"output": str, "messages": list[dict], "completed": bool}`
- 首次调用时自动执行 `asyncio.run(initialize_dpa())`
- 后续调用仅执行 `reload_agent_rule()` + `agent_stream()`，不重复初始化
- 每次调用生成新 `conv_id`（UUID）
- `try/finally` 确保 `CheckpointerFactory.get_checkpointer().release(conv_id)` 一定执行
- 异常时返回 `{"output": "", "messages": [], "completed": False}`，不抛异常
- `completed=True` 表示流正常走到 `ConversationEndEvent`

**Verification:**
- 单元测试：mock `initialize_dpa` + `reload_agent_rule` + `agent_stream`，验证调用顺序
- 单元测试：mock `agent_stream` 抛异常，验证返回 `{"output": "", "messages": [], "completed": False}`
- 单元测试：验证 `release(conv_id)` 在异常路径也被调用

---

### Task 3.2: Implement _collect_stream() — 事件收集

**Files:** `evolution/skills/skill_module.py`
**Depends on:** Task 3.1

实现 `async def _collect_stream(conv_id, query) -> dict` 辅助函数，遍历 `agent_stream()` 的 async generator 并构建 messages 列表和 output 字符串。

**事件映射规则：**
- `ThinkChunkEvent`: `{"role": "think", "content": content}`
- `SummaryEvent` / `FinalAnswerChunkEvent`: 拼接到 output 字符串
- `ToolStartEvent`: `{"role": "tool", "name": plugin, "content": "start"}`
- `ToolEndEvent`: `{"role": "tool", "name": plugin, "content": "end"}`
- `ConversationEndEvent`: 标记 completed=True
- 其他事件: 可选记录 type 标记，不构建 message

**Acceptance criteria:**
- 正常流：output 不为空，messages 长度 > 0，completed=True
- 只产生 ThinkChunkEvent 无 FinalAnswer：output=""，messages 含 think 条目，completed=False
- messages 中每条包含 `role` 字段，type 信息存入 `event_type` 字段（可选）
- 不因未预期的 event 属性而崩溃（防御性 `getattr`）

**Verification:**
- 单元测试：构造 mock AgentEvent 流，验证 messages 结构和 output 拼接正确
- 单元测试：构造空事件流，验证 `output=""`, `messages=[]`, `completed=False`

---

### Task 3.3: Implement _ensure_initialized() — 延迟初始化

**Files:** `evolution/skills/skill_module.py`
**Depends on:** Task 3.1

实现 `async def _ensure_initialized() -> None` 辅助函数，管理 EDPAgent 的模块级一次性初始化。

**Acceptance criteria:**
- 使用模块级 `_edp_initialized: bool` 标志
- 首次调用执行 `await initialize_dpa()`
- 后续调用为 no-op
- 线程安全（asyncio.run 在每次评估创建新 event loop，无并发问题）

**Verification:**
- 单元测试：mock `initialize_dpa`，连续调用两次 `_ensure_initialized()`，断言 `initialize_dpa` 只被调用一次

---

## Vertical Slice 4: Wiring (集成接入点)

### Task 4.1: Wire run_edp_agent into SkillEvolutionAdapter

**Files:** `evolution/core/fitness.py`
**Depends on:** Tasks 2.1, 3.1

在 `SkillEvolutionAdapter.__init__` 中新增 `edp-agent` 分支：

```python
if self.inference == "edp-agent":
    from evolution.skills.skill_module import run_edp_agent
    self.run_fn = run_edp_agent
```

**Acceptance criteria:**
- `inference_mode="edp-agent"` 时 `self.run_fn` 指向 `run_edp_agent`
- `self.run_fn` 的调用签名 `(skill_text, task_input, config) -> dict` 与现有函数一致
- 不影响 `hermes-agent` 和 `single-turn` 的现有分支

**Verification:**
- 构造 `EvolutionConfig(inference_mode="edp-agent")`，创建 `SkillEvolutionAdapter(config)`，检查 `adapter.run_fn.__name__` 包含 `edp`

---

## Checkpoint: Manual Integration Test

在进入测试阶段前，手工验证（仅在有 EDPAgent 环境的 VM 上）：

```bash
python -m evolution.skills.evolve_skill \
  --skill arxiv \
  --inference edp-agent \
  --evaluator llm-judge \
  --iterations 3 \
  --eval-source synthetic
```

验证：
- initialize_dpa 只执行一次
- 每轮评估前后 body 正确 reload
- Redis session 无泄漏（检查 `KEYS *`）
- output 目录包含 `evolved_skill.md`, `trajectories.jsonl`

**本地 dry-run 验证（无 EDPAgent 环境也可执行）：**

```bash
python -m evolution.skills.evolve_skill --skill arxiv --inference edp-agent --dry-run
# 预期输出：Inference: edp-agent, 配置验证通过
```

---

## Vertical Slice 5: Tests

### Task 5.1: Unit tests — config + CLI

**Files:** `tests/core/test_config.py` (update), `tests/skills/test_evolve.py` (update)
**Depends on:** Tasks 1.1, 1.2

**Acceptance criteria:**
- `test_config_inference_mode_edp_agent` — `inference_mode="edp-agent"` 正确存储
- `test_config_edp_agent_path_default` — `edp_agent_path` 默认值为项目内路径
- `test_dry_run_accepts_edp_agent` — `--inference edp-agent --dry-run` 不报错

---

### Task 5.2: Unit tests — reload_agent_rule

**Files:** `tests/edp_agent/test_agent.py` (new)
**Depends on:** Task 2.1

**Acceptance criteria:**
- `test_reload_updates_markdown_body` — mock `_agent` + `_agent_rule`，验证 `agent.configure` 被调用且 prompt 包含新的 body
- `test_reload_preserves_frontmatter` — scope/limits/scripts 字段不被修改
- `test_reload_agent_none_no_error` — `_agent=None` 时不抛异常
- `test_reload_agent_rule_none_no_error` — `_agent_rule=None` 时不抛异常

---

### Task 5.3: Unit tests — run_edp_agent + event collection

**Files:** `tests/skills/test_skill_module.py` (update)
**Depends on:** Tasks 3.1, 3.2, 3.3

**Acceptance criteria:**
- `test_run_edp_agent_returns_dict` — mock 全部依赖，验证返回格式
- `test_run_edp_agent_lazy_init` — 首次调用触发 `initialize_dpa`，第二次不触发
- `test_collect_stream_builds_messages` — 构造 ThinkChunk + ToolStart + FinalAnswerChunk 事件流，验证 messages 结构
- `test_collect_stream_empty` — 空事件流 → `completed=False`
- `test_run_edp_agent_exception_handling` — 模拟异常，验证不抛异常且 `completed=False`
- `test_run_edp_agent_releases_session_on_error` — 异常路径仍调用 `release(conv_id)`

---

### Task 5.4: Integration tests — wiring

**Files:** `tests/core/test_fitness.py` (update)
**Depends on:** Task 4.1

**Acceptance criteria:**
- `test_adapter_wires_edp_agent` — `inference_mode="edp-agent"` 时 adapter 使用 `run_edp_agent`
- `test_adapter_run_fn_signature` — 调用 `adapter.run_fn(text, input, config)` 返回含 `output`/`messages`/`completed` 的 dict

---

## Dependency Graph

```
Task 1.1 (config fields) ──┬── Task 1.2 (CLI choice) ────┐
                           ├── Task 3.1 (run_edp_agent) ──┼── Task 4.1 (wiring) ── Checkpoint ── Task 5.4 (integration tests)
Task 2.1 (reload_agent) ───┤                             │                          │
                           └── Task 3.2 (_collect) ───────┤                          │
                               Task 3.3 (_ensure_init) ───┘                          │
                                                                                     │
Task 5.1 (unit: config+CLI) ─────────────────────────────────────────────────────────┤
Task 5.2 (unit: reload) ─────────────────────────────────────────────────────────────┤
Task 5.3 (unit: run_edp_agent) ──────────────────────────────────────────────────────┘
```

## Implementation Order

1. **Slice 1** (Tasks 1.1, 1.2) — config + CLI, zero risk, can be done locally
2. **Slice 2** (Task 2.1) — `reload_agent_rule()`, isolated change to edp_agent
3. **Slice 3** (Tasks 3.1, 3.2, 3.3) — core `run_edp_agent()` + helpers
4. **Slice 4** (Task 4.1) — 3-line wiring change
5. **Checkpoint** — dry-run locally, integration test on VM
6. **Slice 5** (Tasks 5.1–5.4) — comprehensive tests

## Notes

- Task 2.1 (`reload_agent_rule`) 和 Tasks 3.1–3.3 (`run_edp_agent`) 可以并行开发，它们是独立模块
- Task 2.1 需要在 VM 上验证（依赖 openjiuwen/ReActAgent 运行时），但单元测试可以在本地 mock
- 本地没有 EDPAgent 完整环境（openjiuwen/Redis/a2a_service），`run_edp_agent` 的集成验证必须在 VM 上进行
- `pyproject.toml` 已包含 `edp_agent*`，无需改动

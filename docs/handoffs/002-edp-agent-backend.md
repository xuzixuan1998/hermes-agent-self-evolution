# Handoff: EDPAgent Backend — 实现完成，待试运行

**Date:** 2026-05-27
**From:** Session implementing `docs/plans/002-edp-agent-backend/plan.md`
**To:** VM 试运行 & 集成验证 session

---

## What We Built

按照 plan.md 5 个 Vertical Slice 完成了全部实现和单元测试：

| Slice | 内容 | 文件 |
|-------|------|------|
| Slice 1 | Config 字段 + CLI 选项 | `evolution/core/config.py`, `evolution/skills/evolve_skill.py` |
| Slice 2 | `reload_agent_rule(new_body)` | `edp_agent/agent.py:656` (~20 行) |
| Slice 3 | `run_edp_agent()` + 3 个辅助函数 | `evolution/skills/skill_module.py:77-152` (~85 行) |
| Slice 4 | Wiring → `SkillEvolutionAdapter` | `evolution/core/fitness.py:225-227` (3 行) |
| Slice 5 | 单元测试 (17 个 test case) | `tests/edp_agent/test_agent.py`, 更新 `tests/core/`, `tests/skills/` |

未提交。当前分支 `main`，所有变更在工作区：

```
 M evolution/core/config.py
 M evolution/core/fitness.py
 M evolution/skills/evolve_skill.py
 M evolution/skills/skill_module.py
 M tests/core/test_config.py
 M tests/core/test_fitness.py
 M tests/skills/test_evolve.py
 M tests/skills/test_skill_module.py
?? edp_agent/         (含 reload_agent_rule 在内的整个目录)
?? tests/edp_agent/   (新测试文件)
?? docs/plans/002-edp-agent-backend/
?? docs/specs/edp-agent-backend.md
```

`pyproject.toml` 的 `include = ["evolution*", "edp_agent*"]` 已就绪（之前改的）。

---

## 核心调用链

```
SkillEvolutionAdapter.evaluate()
  → run_edp_agent(skill_text, task_input, config)
    → 1. config.agent_framework_path 不为空时注入 sys.path
    → 2. asyncio.run(_ensure_initialized())    # 首次调用 await initialize_dpa()
    → 3. reload_agent_rule(skill_text)          # 替换 markdown_body → system_prompt
    → 4. asyncio.run(_collect_stream(conv_id, query))
    │     → agent_stream(query, conv_id)        # 遍历 17 种 AgentEvent
    │     → ThinkChunkEvent → messages[role=think]
    │     → ToolStart/EndEvent → messages[role=tool]
    │     → SummaryEvent/FinalAnswerChunkEvent → output
    │     → ConversationEndEvent → completed=True
    → 5. asyncio.run(_release_session(conv_id)) # try/finally 保证执行
    → {"output": str, "messages": list[dict], "completed": bool}
```

**关键设计决策**：
- `asyncio.run()` 创建独立 event loop（EDPAgent 是全异步的，自进化框架是同步的）
- 每次评估用新 `conv_id`（UUID），不跨评估复用 session
- `reload_agent_rule` 只重建 prompt template，不动 model_client / sys_operation / Rails
- 模块级 `_edp_initialized` 标志防止重复初始化（Redis 连接只需一次）

---

## VM 试运行 Checkpoint

**前置条件**：VM 上已有完整 agent-framework 环境（openjiuwen/Redis/a2a_service）。

```bash
# 1. 拉代码到 VM
cd /path/to/hermes-agent-self-evolution
git pull  # 或 rsync

# 2. 本地 dry-run（无 EDPAgent 环境也可执行，验证配置通过）
python -m evolution.skills.evolve_skill --skill arxiv --inference edp-agent --dry-run
# 预期输出：Inference: edp-agent, 配置验证通过

# 3. 完整集成测试（需在 VM 上）
python -m evolution.skills.evolve_skill \
  --skill arxiv \
  --inference edp-agent \
  --evaluator llm-judge \
  --iterations 3 \
  --eval-source synthetic
```

**验证清单**：

- [ ] `initialize_dpa` 只执行一次（日志中搜 `[DPA] 初始化完成`，应出现 1 次）
- [ ] 每轮评估前后 body 正确 reload（搜 `[DPA] reload_agent_rule`，每次评估 1 条）
- [ ] Redis session 无泄漏（评估完成后 `redis-cli KEYS '*'` 检查，不应残留 `conv_id` 相关 key）
- [ ] `output/` 目录包含 `evolved_skill.md`, `trajectories.jsonl`, `metrics.json`, `config.json`
- [ ] `config.json` 中 `inference_mode` = `"edp-agent"`
- [ ] `trajectories.jsonl` 中 messages 包含 `think` / `tool` role 记录（非空）

### 可能的集成问题

| 风险 | 现象 | 排查方向 |
|------|------|----------|
| `asyncio.run()` 与已有 event loop 冲突 | `RuntimeError: This event loop is already running` | 检测 running loop，或使用 `nest_asyncio` |
| `_release_session` 失败 | 日志中搜 `Failed to release session` | Redis 连接状态，CheckpointerFactory 是否正确初始化 |
| `common.events` 导入失败 | `ImportError: No module named 'common'` | `agent_framework_path` 是否正确指向 a2a_service 目录 |
| `agent_stream` 空结果 | output="" / messages=[] | 检查 LLM 是否正常响应，查看 EDPAgent 日志 |
| 候选 body 未生效 | 每次评估用的都是原始 skill | 搜 `reload_agent_rule` 日志确认 body 内容在变化 |

### 传参说明

`run_edp_agent` 接受 config 对象中的以下字段：

| 字段 | 用途 | 必填 |
|------|------|------|
| `config.agent_framework_path` | a2a_service 路径，用于 `sys.path.insert` | 仅当 `common.events` 等不在 PYTHONPATH 时需要 |
| `config.agent_model` | 当前未直接使用（EDPAgent 通过自己的 settings 获取 model） | 否 |
| `config.agent_max_iterations` | 当前未直接使用（由 AgentRule.md frontmatter 控制） | 否 |

---

## 下次试运行的入口命令

```bash
# 最小路径 dry-run
python -m evolution.skills.evolve_skill --skill arxiv --inference edp-agent --dry-run

# 如果上面报 import error，说明 common.events 不在 PYTHONPATH，需要加 agent_framework_path
# 当前 CLI 没有 --agent-framework-path 参数，需要手动设置或在代码中配置
```

**注意**：目前的 CLI（`evolve_skill.py`）没有暴露 `--agent-framework-path` 参数。如果 VM 上运行需要指定此路径，有两种方式：

1. 临时 hack：在调用前 `export PYTHONPATH=/path/to/a2a_service:$PYTHONPATH`
2. 或者后续给 CLI 加 `--agent-framework-path` 参数

---

## Related Files

| 文件 | 角色 |
|------|------|
| `docs/specs/edp-agent-backend.md` | 完整 spec（约束、架构、接口约定） |
| `docs/plans/002-edp-agent-backend/plan.md` | 开发计划（决策、架构图、依赖关系） |
| `docs/plans/002-edp-agent-backend/todo.md` | 任务分解（每项含 acceptance criteria + verification 命令） |
| `edp_agent/agent.py` | EDPAgent 入口（`initialize_dpa`, `agent_stream`, **`reload_agent_rule`**） |
| `edp_agent/agent_rule.py` | `AgentRuleConfig` pydantic model（含 `markdown_body` 字段） |
| `edp_agent/prompt.py` | `build_system_prompt()` — AgentRule body 后面的补充 prompt |
| `evolution/skills/skill_module.py` | **`run_edp_agent`** + `_ensure_initialized` + `_collect_stream` + `_release_session` |
| `evolution/core/fitness.py:218` | `SkillEvolutionAdapter.__init__` — edp-agent 分支 |
| `evolution/core/config.py:17-18` | `agent_framework_path` / `edp_agent_path` 新字段 |
| `evolution/skills/evolve_skill.py:395` | `--inference` CLI choice |

---

## What is NOT Changing

- `initialize_dpa()` 的初始化逻辑（Redis/Runner/Rails/工具/Skill 注册）
- `agent_stream()` 的事件流逻辑
- `AgentRuleConfig`、`load_agent_rule()`、`build_system_prompt()`
- 现有 `run_hermes_agent()`、`run_single_turn()`
- EDPAgent 的 17 种 SSE 事件流 → 前端协议

---

## Suggested Skills

针对下一 session（VM 试运行 & 问题修复）：

- **`agent-skills:test`** — 试运行验收标准驱动：逐个执行 checklist 并记录结果
- **`agent-skills:debugging-and-error-recovery`** — 如果运行时出现 import error / asyncio 冲突 / Redis 泄漏，系统化排查
- **`agent-skills:shipping-and-launch`** — 试运行通过后准备提交（pre-launch checklist）

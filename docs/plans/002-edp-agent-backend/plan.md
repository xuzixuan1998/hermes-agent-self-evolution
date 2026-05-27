# Plan 002: EDPAgent 作为自进化推理后端

**Status:** Ready
**Created:** 2026-05-27
**Based on:** [Spec](../../specs/edp-agent-backend.md)

---

## Context

当前自进化 demo 支持两种推理后端：`single-turn`（dspy.ChainOfThought 单轮 LLM）和 `hermes-agent`（AIAgent.run_conversation）。现在需要接入第三种后端 — EDPAgent，使 GEPA 优化出的候选 AgentRule 内容能在每轮评估前动态装载到 EDPAgent 中执行。

EDPAgent 是一个基于 openjiuwen 的 ReActAgent，通过 `agent_stream(query, conv_id)` 流式返回 17 种 AgentEvent。它的 system prompt 由 `AgentRule.md`（YAML frontmatter + markdown body）构建。自进化需要动态替换 markdown body 而不重新初始化 Redis/Runner/Rails/工具等基础设施。

**核心设计原则**：EDPAgent 最小改动（只加一个 `reload_agent_rule()` 函数 ~15 行），自进化侧适配（新增 `run_edp_agent()` 处理 async→sync 转换、session 管理、事件收集）。

## Key Decisions

| 决策 | 选择 | 原因 |
|------|------|------|
| reload 策略 | 仅替换 system prompt，不动 model_client/sys_operation/Rails | 重新初始化 Redis/Runner 太重（~30s），且可能丢失正在运行的 session |
| async→sync | `asyncio.run()` 包裹整个调用链 | 自进化框架是同步的，EDPAgent 是全异步的；每次评估是一个独立的短周期，可以用 `asyncio.run()` 创建临时 event loop |
| conv_id | 每次评估生成新 UUID | 每次评估是独立任务，不需要跨轮复用 session |
| Session 清理 | 评估后 `release(conv_id)` 删除 Redis key | 防止 Redis 内存泄漏，积压 50-200 个 session |
| 路径注入 | `sys.path.insert` 添加 a2a_service 路径 | EDPAgent 依赖 `common.events/logger/crypto`，这些是 a2a_service 应用层代码，不是 pip 包 |
| messages 构建 | 从 17 种 AgentEvent 中提取关键事件 | Think/Tool/FinalAnswer 对 GEPA reflection 最有价值，其余事件忽略 |

## Architecture

### Before

```
SkillEvolutionAdapter.evaluate()
  → run_hermes_agent(skill_text, task_input, config)
    → AIAgent(model, quiet_mode=True)
    → agent.run_conversation(user_message, system_message=skill_text)
    → {"output", "messages", "completed"}
```

### After (new edp-agent path)

```
SkillEvolutionAdapter.evaluate()
  → run_edp_agent(skill_text, task_input, config)
    → 1. [首次] asyncio.run(initialize_dpa())     # Redis/Runner/Rails/工具
    → 2. reload_agent_rule(skill_text)             # 动态替换 body → 更新 prompt
    → 3. asyncio.run(_run_stream(conv_id, query))  # agent_stream → 收集事件
    → 4. Checkpointer.release(conv_id)             # 清理 Redis session
    → {"output", "messages", "completed"}
```

### Event → messages 映射

| AgentEvent | Role in messages |
|---|---|
| `ThinkChunkEvent` | `{"role": "think", "content": ...}` |
| `SummaryEvent` / `FinalAnswerChunkEvent` | 拼接为 output 字符串 |
| `ToolStartEvent` | `{"role": "tool", "name": ..., "content": "start"}` |
| `ToolEndEvent` | `{"role": "tool", "name": ..., "content": "end"}` |
| 其他 13 种事件 | 忽略或记录 type 标记 |

### Module/Metric Selection Matrix (updated)

| `--inference` | `--evaluator` | 执行函数 | 优化器 |
|---|---|---|---|
| `single-turn` | `fast` | run_single_turn | dspy.GEPA (legacy) |
| `single-turn` | `llm-judge` | run_single_turn | gepa.optimize |
| `hermes-agent` | `fast` | run_hermes_agent | gepa.optimize |
| `hermes-agent` | `llm-judge` | run_hermes_agent | gepa.optimize |
| **`edp-agent`** | `fast` | **run_edp_agent** | gepa.optimize |
| **`edp-agent`** | `llm-judge` | **run_edp_agent** | gepa.optimize |

## Files Modified

| File | Change | Risk |
|------|--------|------|
| `evolution/core/config.py` | `inference_mode` 新增 `"edp-agent"`，新增 `agent_framework_path`、`edp_agent_path` 字段 | 低 — 新增字段，默认值不影响现有流程 |
| `edp_agent/agent.py` | 新增 `reload_agent_rule(new_body)` 函数 | 中 — 修改 EDPAgent 核心文件，但只加不改 |
| `evolution/skills/skill_module.py` | 新增 `run_edp_agent()` + 内部辅助函数 | 中 — 新函数，不影响现有 run_hermes_agent |
| `evolution/core/fitness.py` | `SkillEvolutionAdapter.__init__` 新增 `edp-agent` 分支 | 低 — 一行 import + 一行 if |
| `evolution/skills/evolve_skill.py` | `--inference` click.Choice 新增 `"edp-agent"` | 低 — 一行字符串 |
| `pyproject.toml` | 已包含 `edp_agent*`，无需改动 | 无 |

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| EDPAgent 依赖 `common.events` 等 a2a_service 代码，本地导入失败 | `sys.path.insert` 注入路径，`try/except` 捕获 ImportError 给出清晰错误信息 |
| `asyncio.run()` 与现有 event loop 冲突（Jupyter/pytest） | 检测 running loop，已有则用 `nest_asyncio` 或跳过 |
| initialize_dpa() 很重（Redis 连接等），每次评估前初始化不可接受 | 模块级 `_initialized` 标志，首次调用初始化，后续仅 reload |
| reload 后 agent 状态不一致（正在执行的 tool call） | 每次评估用新 `conv_id`，session 隔离，不跨评估复用 |
| Redis session 泄漏 | `try/finally` 确保 `release(conv_id)` 一定执行 |

## What is NOT Changing

- `initialize_dpa()` 的初始化逻辑（Redis/Runner/Rails/工具/Skill 注册）
- `agent_stream()` 的事件流逻辑
- `AgentRuleConfig`、`load_agent_rule()`、`build_system_prompt()`
- `reload_agent_rule()` 只替换 markdown_body → system prompt，不动 frontmatter 的其他部分（scope/limits/scripts/todolist_steps）
- 现有的 `run_hermes_agent()`、`run_single_turn()`、`SkillEvolutionAdapter` 核心逻辑

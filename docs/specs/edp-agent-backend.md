# EDPAgent 作为自进化推理后端

## 元信息

- **日期**: 2026-05-27
- **状态**: 待实现

## 目标

将自进化 demo 的推理后端从 Hermes (`AIAgent.run_conversation`) 切换为 EDPAgent (`agent_stream`)，使 GEPA 优化出的候选 AgentRule 内容能在每轮评估前动态装载到 EDPAgent 中执行。

## 约束

| 约束 | 说明 |
|---|---|
| EDPAgent 最小改动 | 只在 `agent.py` 加一个 `reload_agent_rule()` 函数（~15 行），不动业务逻辑、Rails、工具、沙箱、Redis 等基础设施 |
| 自进化侧适配 | 新增 `run_edp_agent()` 函数，实现 async→sync 转换、session 管理、事件收集 |
| 部署方式 | 本地写代码 → 拉到 VM 上跑。VM 上已有完整 agent-framework 环境 |
| 接口兼容 | 保持和 `run_hermes_agent()` 相同的返回格式 `{"output", "messages", "completed"}` |

## 架构

```
SkillEvolutionAdapter.evaluate()
  → run_edp_agent(skill_text, task_input, config)
    → 1. 首次调用: await initialize_dpa()     # 一次性初始化（Redis/Runner/Rails/工具）
    → 2. reload_agent_rule(skill_text)         # 动态替换 markdown body → 更新 system prompt
    → 3. agent_stream(query=task_input, ...)   # 执行推理，收集 AgentEvent
    → 4. Checkpointer.release(conv_id)         # 清理 Redis session
    → {"output", "messages", "completed"}
```

## 改动清单

### 1. `edp_agent/agent.py` — 新增 `reload_agent_rule`

```python
def reload_agent_rule(new_body: str) -> None:
    """动态替换 AgentRule 的 markdown body，更新 system prompt。

    保持现有 frontmatter（scope/limits/scripts/todolist_steps）不变，
    仅替换注入 LLM 的业务逻辑部分。
    """
```

- 使用模块级 `_agent_rule` 和 `_agent` 单例
- 从 `_agent_rule` 保留 frontmatter，替换 `markdown_body`
- 重建 `system_prompt = new_body + build_system_prompt()`
- 调用 `agent.configure()` 更新 prompt template（不动 model_client / sys_operation 等配置）
- 如果 max_iterations 随 body 变化，同步更新

### 2. `evolution/skills/skill_module.py` — 新增 `run_edp_agent`

```python
def run_edp_agent(skill_text: str, task_input: str, config) -> dict:
    """通过 EDPAgent 执行一次推理。

    skill_text → AgentRule markdown body（不含 frontmatter）
    返回 {"output": str, "messages": list[dict], "completed": bool}
    """
```

关键实现细节：
- **首次初始化**: 检查 EDPAgent 是否已初始化，未初始化则 `asyncio.run(initialize_dpa())`
- **路径注入**: `sys.path.insert` 添加 a2a_service 目录（解决 `common.events` 等依赖）
- **reload**: 每次调用前 `reload_agent_rule(skill_text)`
- **conv_id**: 每次调用生成唯一 UUID
- **事件收集**: 遍历 `agent_stream()` 的 async generator，从 `FinalAnswerChunkEvent` / `SummaryEvent` 收集 output，从所有事件构建 messages
- **Session 清理**: 评估完成后 `CheckpointerFactory.get_checkpointer().release(conv_id)`
- **异常处理**: 捕获异常返回 `{"output": "", "messages": [], "completed": False}`

### 3. `evolution/core/config.py` — 新增配置

- `inference_mode` 新增可选值 `"edp-agent"`
- 新增字段:
  - `agent_framework_path: Path` — agent-framework（a2a_service）路径，用于 sys.path 注入
  - `edp_agent_path: Path` — edp_agent 目录路径

### 4. `evolution/core/fitness.py` — 适配

`SkillEvolutionAdapter.__init__` 中新增分支:
```python
if self.inference == "edp-agent":
    self.run_fn = run_edp_agent
```

### 5. `pyproject.toml` — 已完成

`include = ["evolution*", "edp_agent*"]` 已添加。

## 依赖关系

| 依赖 | 来源 | 处理方式 |
|---|---|---|
| `openjiuwen` | pip 包 (v0.1.11) | VM 上已安装，本地不依赖 |
| `common.events/logger/crypto` | a2a_service 应用层代码 | 运行时 `sys.path.insert` 注入 a2a_service 路径 |

## 接口约定

`run_edp_agent(skill_text, task_input, config) -> dict` 与现有 `run_hermes_agent` / `run_single_turn` 保持完全一致的签名和返回格式：

```python
{
    "output": str,       # 最终回复文本
    "messages": list[dict],  # 消息/事件记录列表
    "completed": bool,   # 是否正常完成
}
```

## AgentRule 优化范围

自进化优化的是 AgentRule.md 的 **markdown body**（业务逻辑部分），YAML frontmatter（scope / limits / todolist_steps / scripts）保持不变。

```
---                          # ← frontmatter 保持不动
scope:
  allowed: "..."
limits:
  max_iterations: 30
---
                             # ← body 是 GEPA 的优化目标
# 业务逻辑描述
1. 首先...
2. 然后...
```

## 事件 → messages 映射

从 `agent_stream()` 的 17 种 AgentEvent 中提取关键信息构建 messages：

| 事件 | 映射 |
|---|---|
| `ThinkChunkEvent` | 记录为 `{"role": "think", "content": ...}` |
| `SummaryEvent` / `FinalAnswerChunkEvent` | 拼接为最终 output |
| `ToolStartEvent` / `ToolEndEvent` | 记录为 `{"role": "tool", "name": ..., "content": ...}` |
| 其他事件 | 视需要记录或忽略 |

## 验收标准

- [ ] `inference_mode="edp-agent"` 能完整跑通一轮自进化（10 iterations, population=5）
- [ ] `run_edp_agent()` 返回值格式与 `run_hermes_agent()` 一致
- [ ] 候选 skill 的 markdown body 正确注入为 system prompt
- [ ] 每次评估后 Redis session 被正确清理
- [ ] EDPAgent 原始功能不受影响（`reload_agent_rule` 可回退到原始 AgentRule.md）
- [ ] 异常情况（LLM 超时、agent 报错）返回 `{"output": "", "messages": [], "completed": False}` 不中断进化流程

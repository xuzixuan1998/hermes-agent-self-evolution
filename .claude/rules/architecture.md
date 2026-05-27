# 架构规范

## 模块边界

```
evolution/           # 自进化框架（Phase 1: skill 进化）
├── core/            # 核心抽象：配置、fitness、约束、数据集
├── skills/          # Skill 进化入口 + skill 加载/评估
├── prompts/         # Prompt 进化（Phase 3, 未实现）
├── tools/           # Tool 进化（Phase 2, 未实现）
└── monitor/         # 监控（预留）

edp_agent/           # EDPAgent 本体（从 agent-store copy 进来的）
├── agent.py         # 公开接口：initialize_dpa() / agent_stream() / reload_agent_rule()
├── agent_rule.py    # AgentRule.md 解析
├── rail/            # Rails（中断策略）
├── tool/            # Tools（lite_todo, ask_user 等）
└── deployment/      # 部署脚本（Docker, bundle）
```

## 依赖方向

```
evolution/skills/skill_module.py
  → depends on → edp_agent/agent.py  (reload_agent_rule, agent_stream)
  → depends on → openjiuwen (CheckpointerFactory)

edp_agent/
  → 不依赖 evolution/
```

- `evolution/` 可以依赖 `edp_agent/`（仅 `skill_module.py` 一个文件）
- `edp_agent/` 是独立的，不能反向依赖 `evolution/`
- 跨模块调用通过公开接口（`run_edp_agent()` → `reload_agent_rule()`），不直接访问内部实现

## 接口约定

### 推理后端统一签名

所有推理后端（`single-turn`、`hermes-agent`、`edp-agent`）必须实现：

```python
def run_fn(skill_text: str, task_input: str, config: EvolutionConfig) -> dict:
    return {
        "output": str,       # 最终回复文本
        "messages": list[dict],  # 消息/事件记录
        "completed": bool,   # 是否正常完成
    }
```

异常不抛出 — 返回 `{"output": "", "messages": [], "completed": False}`。

### 事件映射

AGP agent 的事件（ThinkChunk、ToolStart、FinalAnswerChunk 等）由 `_collect_stream()` 统一映射为标准 messages 格式。

## 添加新推理后端

1. 在 `evolution/skills/skill_module.py` 新增 `run_xxx_agent()` 函数
2. 在 `evolution/core/fitness.py` 的 `SkillEvolutionAdapter.__init__` 新增分支
3. 在 `evolution/core/config.py` 的 `EvolutionConfig` 新增相关配置字段
4. 在 `evolution/skills/evolve_skill.py` 的 CLI `--inference` 选项新增值
5. 在 `tests/` 新增对应测试

## 数据流

```
SKILL.md (disk)
  → load_skill() → {frontmatter, body}
  → body → GEPA candidate
  → run_xxx_agent(skill_text=body, task_input) → agent inference
  → LLMJudge.score(agent_output) → FitnessScore
  → GEPA reflective mutation → new body
  → reassemble_skill(frontmatter, new_body) → evolved SKILL.md
```

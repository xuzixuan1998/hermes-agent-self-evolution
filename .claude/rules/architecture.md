# 架构规范

## 模块边界

```
evolution/           # 自进化框架
├── agents/          # 可插拔推理后端（BaseAgent 抽象基类）
│   ├── base.py      # BaseAgent ABC
│   ├── single_turn.py  # SingleTurnAgent — dspy.ChainOfThought
│   ├── hermes_agent.py # HermesAgent — AIAgent.run_conversation()
│   └── edp_agent.py    # EDPAgent — agent_stream() + lazy init
├── core/            # 核心抽象：配置、fitness、约束、数据集、artifact 解析
│   ├── artifact.py  # 通用 YAML frontmatter + body 解析
│   ├── config.py    # EvolutionConfig dataclass
│   ├── fitness.py   # EvolutionAdapter + LLMJudge
│   ├── constraints.py
│   ├── dataset_builder.py
│   └── external_importers.py
├── skills/          # Skill 进化
│   ├── skill_module.py # SkillModule + find_skill + load_skill wrapper
│   └── evolve_skill.py # CLI 入口
├── prompts/         # Prompt 进化
│   └── evolve_prompt.py # CLI 入口
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
evolution/agents/edp_agent.py
  → depends on → edp_agent/agent.py  (reload_agent_rule, agent_stream, initialize_dpa)
  → depends on → openjiuwen (CheckpointerFactory)

evolution/agents/hermes_agent.py
  → depends on → hermes-agent repo (AIAgent, via sys.path)

evolution/core/fitness.py
  → depends on → evolution/agents/  (BaseAgent 子类)

evolution/skills/ 和 evolution/prompts/
  → depends on → evolution/core/  (EvolutionAdapter, EvolutionConfig, artifact)

edp_agent/
  → 不依赖 evolution/
```

- `evolution/agents/` 可以依赖 `edp_agent/`（仅 `edp_agent.py` 一个文件）
- `edp_agent/` 是独立的，不能反向依赖 `evolution/`
- `evolution/agents/` 不依赖 `evolution/skills/` 或 `evolution/prompts/`

## 接口约定

### 推理后端统一接口

所有推理后端继承 `BaseAgent`（`evolution/agents/base.py`），实现 `run()` 方法：

```python
class BaseAgent(ABC):
    @abstractmethod
    def run(self, system_prompt: str, task_input: str, config: EvolutionConfig) -> dict:
        return {
            "output": str,       # 最终回复文本
            "messages": list[dict],  # 消息/事件记录
            "completed": bool,   # 是否正常完成
        }
```

异常不抛出 — 返回 `{"output": "", "messages": [], "completed": False}`。

### 事件映射

EDPAgent 的事件（ThinkChunk、ToolStart、FinalAnswerChunk 等）由 `_collect_stream()` 统一映射为标准 messages 格式。

## 添加新推理后端

1. 在 `evolution/agents/` 新增 `<name>.py`，创建 `class XxxAgent(BaseAgent)` 实现 `run()`
2. 在 `evolution/core/fitness.py` 的 `EvolutionAdapter.__init__` 新增分支
3. 在 `evolution/core/config.py` 的 `EvolutionConfig` 新增相关配置字段
4. 在 CLI（`evolve_skill.py` / `evolve_prompt.py`）的 `--inference` 选项新增值
5. 在 `tests/agents/` 新增对应测试

## 数据流

```
SKILL.md (or AgentRule.md) on disk
  → load_artifact(path) → {frontmatter, body}
  → body → GEPA candidate (key: "artifact_body")
  → agent.run(system_prompt=body, task_input) → agent inference
  → LLMJudge.score(agent_output) → FitnessScore
  → GEPA reflective mutation → new body
  → reassemble_artifact(frontmatter, new_body) → evolved artifact
```

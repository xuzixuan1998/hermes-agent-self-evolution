# ADR-001: 可插拔推理后端架构 + prompt/skill 进化分离

## Status

Accepted

## Date

2026-05-27

## Context

当前 `evolution/skills/skill_module.py` 混杂了两类职责：

1. **Agent 推理调用**：`run_single_turn`（dspy.ChainOfThought）、`run_hermes_agent`（AIAgent.run_conversation）、`run_edp_agent`（EDPAgent.agent_stream）
2. **Skill 文件操作**：`load_skill`、`reassemble_skill`、`find_skill`、`SkillModule`

此外，`--inference edp-agent` 模式实际优化的是 `AgentRule.md` 的 markdown body（即 system prompt），而非 `SKILL.md` 的 skill body。把 prompt 优化挂在 skill 进化下面造成概念混淆。

核心问题：
- 新增推理后端需要改 `skill_module.py`、`fitness.py`、`evolve_skill.py` 三个文件
- Agent 函数与 skill 解析函数没有清晰边界
- `SkillEvolutionAdapter` 名字暗示只用于 skill，但逻辑对 prompt 进化同样适用
- `load_skill`/`reassemble_skill` 的 YAML frontmatter + body 解析逻辑是通用的，不应独占在 skills 模块

## Decision

### 1. 新建 `evolution/agents/` — 可插拔推理后端

```
evolution/agents/
├── __init__.py
├── base.py           # BaseAgent ABC
├── single_turn.py    # SingleTurnAgent
├── hermes_agent.py   # HermesAgent
└── edp_agent.py      # EDPAgent
```

所有后端实现 `BaseAgent` 抽象基类：

```python
class BaseAgent(ABC):
    @abstractmethod
    def run(self, system_prompt: str, task_input: str, config: EvolutionConfig) -> dict:
        """返回 {"output": str, "messages": list[dict], "completed": bool}"""
```

新增后端只需：实现 `BaseAgent.run()` → 在 `fitness.py` 的 `EvolutionAdapter.__init__` 新增一个分支。

### 2. 新建 `evolution/core/artifact.py` — 通用制品解析

```python
def load_artifact(path: Path) -> dict:
    """解析 YAML frontmatter + markdown body → {path, raw, frontmatter, body, name, description}"""

def reassemble_artifact(frontmatter: str, body: str) -> str:
    """重组 frontmatter + body → 完整文本"""
```

`skill_module.load_skill` 和 `skill_module.reassemble_skill` 变成 thin wrapper，`prompts/` 下的代码也可复用。

### 3. `SkillEvolutionAdapter` → `EvolutionAdapter`

- 改名为 `EvolutionAdapter`（放在 `evolution/core/fitness.py`）
- 候选字段名 `skill_body` → `artifact_body`
- `EvolutionAdapter` 与具体的 artifact 类型（skill / prompt）解耦，skill 和 prompt 进化共享同一套 evaluation 逻辑

### 4. 新建 `evolution/prompts/evolve_prompt.py` — prompt 进化 CLI

```bash
python -m evolution.prompts.evolve_prompt \
    --prompt /path/to/AgentRule.md \
    --inference edp-agent \
    --evaluator llm-judge \
    --iterations 10
```

与 `evolve_skill` 平行，共享 `EvolutionAdapter`、`gepa.optimize()`、约束校验等底层引擎。

## Alternatives Considered

### 不改架构，继续在 skill_module.py 追加后端

- Pros: 零改动成本
- Cons: 每次加后端都碰 3 个文件；prompt 优化挂在 skill 下语义混乱；`load_skill` 逻辑无法复用
- Rejected: 已到临界点 — edp-agent 后端的加入已经暴露了架构问题

### Agent 放在 `evolution/core/` 下

- Pros: 少建一个目录
- Cons: `core` 放的是基础设施（config、fitness、constraints），agent 后端是业务逻辑
- Rejected: 职责不清，`core` 不应膨胀

### 参数改名 `skill_text` → `instructions` 或 `artifact_text`

- Pros: 语义更准确
- Cons: 改动范围大，对现有代码侵入性强
- Rejected: 用户选择保持 `system_prompt`（已在 BaseAgent 接口中使用）

### prompt 进化复用 `evolve_skill` CLI，加 `--artifact-type` flag

- Pros: 单一入口
- Cons: skill 和 prompt 的加载/写回逻辑不同（`--skill` 按名字查找 vs `--prompt` 传文件路径），强行共用一个入口会让 CLI 参数组合爆炸
- Rejected: 两个独立 CLI 入口，共享底层引擎，语义清晰

## Consequences

- **正面**：新增推理后端只需实现 `BaseAgent` 并在 `EvolutionAdapter` 注册一行
- **正面**：prompt 进化获得独立 CLI，概念清晰
- **正面**：`artifact.py` 的解析逻辑可被 skill、prompt、未来 tool 进化复用
- **负面**：`evolution/agents/` 新增目录，项目结构加深一层
- **负面**：重构涉及 10+ 文件变更，需要同步更新所有测试和 patch 路径
- **风险**：重构期间如果合并其他 PR 可能冲突 — 建议集中在一次 PR 中完成

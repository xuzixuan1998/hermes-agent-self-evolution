# 架构重构：可插拔推理后端 + prompt 进化

## Context

当前 `skill_module.py` 混杂了两类职责：agent 推理调用（`run_single_turn`、`run_hermes_agent`、`run_edp_agent`）和 skill 文件操作（`load_skill`、`find_skill`、`reassemble_skill`、`SkillModule`）。且 edp-agent 模式下实际优化的是 AgentRule.md 的 system prompt，不属于 skill 进化范畴。

重构目标：
1. 推理后端抽成 `evolution/agents/` 可插拔架构（BaseAgent 抽象基类）
2. 通用 YAML frontmatter + body 解析抽到 `evolution/core/artifact.py`
3. `SkillEvolutionAdapter` 改名 `EvolutionAdapter`，`skill_body` → `artifact_body`
4. 新增 `evolution/prompts/evolve_prompt.py` CLI 入口

## 改动清单

### 1. 新建 `evolution/agents/`

| 文件 | 内容 |
|---|---|
| `evolution/agents/__init__.py` | 空 |
| `evolution/agents/base.py` | `BaseAgent` ABC，抽象方法 `run(system_prompt, task_input, config) -> dict` |
| `evolution/agents/single_turn.py` | `SingleTurnAgent(BaseAgent)` — 从 `skill_module.py` 搬移 `run_single_turn` |
| `evolution/agents/hermes_agent.py` | `HermesAgent(BaseAgent)` — 从 `skill_module.py` 搬移 `run_hermes_agent` |
| `evolution/agents/edp_agent.py` | `EDPAgent(BaseAgent)` — 从 `skill_module.py` 搬移 `run_edp_agent` + `_ensure_initialized` + `_collect_stream` + `_release_session` |

### 2. 新建 `evolution/core/artifact.py`

从 `skill_module.py` 提取：

- `load_artifact(path: Path) -> dict` — 解析 YAML frontmatter + body（原 `load_skill` 逻辑）
- `reassemble_artifact(frontmatter: str, body: str) -> str` — 重组（原 `reassemble_skill` 逻辑）

### 3. 修改 `evolution/skills/skill_module.py`

- 删除：`run_single_turn`、`run_hermes_agent`、`run_edp_agent`、`_ensure_initialized`、`_collect_stream`、`_release_session`、`_edp_initialized`、`uuid`/`asyncio`/`logging` 导入
- 保留：`SkillModule`、`find_skill`
- `load_skill` → 变成 `load_artifact` 的 thin wrapper
- `reassemble_skill` → 变成 `reassemble_artifact` 的 thin wrapper

### 4. 修改 `evolution/core/fitness.py`

- `SkillEvolutionAdapter` → `EvolutionAdapter`
- 候选字典键 `skill_body` → `artifact_body`
- `self.run_fn = ...` → `self.agent = ...`（实例化 BaseAgent 子类，而非直接引用函数）
- 从 `evolution.agents` 导入 agent 类

### 5. 修改 `evolution/skills/evolve_skill.py`

- `seed_candidate = {"skill_body": ...}` → `{"artifact_body": ...}`
- `best_candidate.get("skill_body", "")` → `best_candidate.get("artifact_body", "")`
- `SkillEvolutionAdapter` → `EvolutionAdapter`

### 6. 新建 `evolution/prompts/evolve_prompt.py`

CLI 入口：`python -m evolution.prompts.evolve_prompt --prompt /path/to/AgentRule.md --inference edp-agent --iterations 10`

- `load_prompt(path)` → 调 `load_artifact`
- `reassemble_prompt(frontmatter, body)` → 调 `reassemble_artifact`
- 其余流程复用 `EvolutionAdapter` + `gepa.optimize()`

### 7. pytest 导入路径修复

- `tests/core/test_fitness.py`: `SkillEvolutionAdapter` → `EvolutionAdapter`，patch 路径从 `evolution.skills.skill_module.run_xxx` → `evolution.agents.xxx.run`
- `tests/skills/test_skill_module.py`: 去掉 agent 相关测试（移入 `tests/agents/`）
- `tests/skills/test_evolve.py`: `skill_body` → `artifact_body`
- `tests/edp_agent/test_agent.py`: 不变

### 8. 新建 `tests/agents/`

| 文件 | 内容 |
|---|---|
| `tests/agents/__init__.py` | 空 |
| `tests/agents/test_agents.py` | `test_skill_module.py` 中 agent 相关测试的搬迁 + 新增 BaseAgent 测试 |

## 依赖关系（重构后）

```
evolution/
├── agents/            # 可插拔推理后端
│   ├── base.py        # BaseAgent ABC
│   ├── single_turn.py # → depends on dspy
│   ├── hermes_agent.py # → depends on hermes-agent repo
│   └── edp_agent.py   # → depends on edp_agent/
├── core/
│   ├── artifact.py    # 通用 frontmatter+body 解析（新）
│   ├── config.py
│   ├── fitness.py     # EvolutionAdapter（改名）
│   ├── constraints.py
│   └── ...
├── skills/            # 纯 skill 进化
│   ├── skill_module.py # SkillModule + find_skill + load_skill wrapper
│   └── evolve_skill.py
└── prompts/           # prompt 进化
    └── evolve_prompt.py
```

- `evolution/agents/` → 不依赖 `evolution/skills/` 或 `evolution/prompts/`
- `evolution/core/fitness.py` → 依赖 `evolution/agents/`
- `evolution/skills/` 和 `evolution/prompts/` → 都依赖 `evolution/core/`

## 验证

1. `python -m pytest tests/ -v` 全部通过
2. `python -m evolution.skills.evolve_skill --skill <name> --dry-run` 正常
3. `python -m evolution.skills.evolve_skill --skill <name> --inference hermes-agent --dry-run` 正常
4. `python -m evolution.prompts.evolve_prompt --prompt edp_agent/AgentRule.md --inference edp-agent --dry-run` 正常
5. 三个后端统一通过 `BaseAgent.run()` 接口调用，返回格式一致

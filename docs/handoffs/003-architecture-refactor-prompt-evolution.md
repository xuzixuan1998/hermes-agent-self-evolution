# Handoff: 架构重构完成，下一步 — Prompt 进化端到端运行

**Date:** 2026-05-27
**From:** Session implementing `docs/plans/003-architecture-refactor/plan.md`
**To:** Prompt 进化端到端跑通 session

---

## What We Did

按照 `docs/plans/003-architecture-refactor/plan.md` 完成了全面架构重构，还差 commit&push：

### 改动范围

| 变更 | 文件 | 说明 |
|------|------|------|
| 新建 | `evolution/agents/base.py` | `BaseAgent` ABC |
| 新建 | `evolution/agents/single_turn.py` | `SingleTurnAgent` — 搬移自 `run_single_turn` |
| 新建 | `evolution/agents/hermes_agent.py` | `HermesAgent` — 搬移自 `run_hermes_agent` |
| 新建 | `evolution/agents/edp_agent.py` | `EDPAgent` — 搬移自 `run_edp_agent` + 3 个辅助函数 |
| 新建 | `evolution/core/artifact.py` | 通用 `load_artifact` / `reassemble_artifact`（YAML frontmatter + body） |
| 新建 | `evolution/prompts/evolve_prompt.py` | Prompt 进化 CLI（`--prompt` 入口，复用 `EvolutionAdapter`） |
| 新建 | `tests/agents/test_agents.py` | Agent 测试（搬迁 + BaseAgent 测试） |
| 收缩 | `evolution/skills/skill_module.py` | 删除所有 agent 函数（~170 行），`load_skill`/`reassemble_skill` → thin wrapper |
| 重命名 | `evolution/core/fitness.py` | `SkillEvolutionAdapter` → `EvolutionAdapter`，`skill_body` → `artifact_body`，`run_fn` → `agent`（BaseAgent 实例） |
| 适配 | `evolution/skills/evolve_skill.py` | 导入 + 键名适配 |
| 文档 | `CLAUDE.md` | 命令改为 `uv sync`/`uv run`，推理后端表、key files 更新 |
| 文档 | `.claude/rules/architecture.md` | 模块边界、依赖方向、接口约定、数据流全面更新 |
| 文档 | `.claude/rules/dependencies.md` | 新建：uv 包管理规范 + 依赖清单 |

### 测试

```
198 passed, 0 failed
```

dry-run 两条路径均正常：
- `python -m evolution.skills.evolve_skill --skill test --dry-run` ✓
- `python -m evolution.prompts.evolve_prompt --prompt edp_agent/AgentRule.md --inference edp-agent --dry-run` ✓

### 未提交

所有变更在工作区，未 commit：
```
M  .claude/rules/architecture.md
M  CLAUDE.md
M  evolution/core/fitness.py
M  evolution/skills/evolve_skill.py
M  evolution/skills/skill_module.py
M  tests/core/test_fitness.py
M  tests/edp_agent/test_agent.py
M  tests/skills/test_evolve.py
M  tests/skills/test_skill_module.py
?? .claude/rules/dependencies.md
?? docs/decisions/ADR-001-pluggable-agents.md
?? docs/plans/003-architecture-refactor/
?? evolution/agents/
?? evolution/core/artifact.py
?? evolution/prompts/evolve_prompt.py
?? tests/agents/
```

---

## 重构后架构速览

```
evolution/
├── agents/            # 可插拔推理后端
│   ├── base.py        # BaseAgent ABC
│   ├── single_turn.py # SingleTurnAgent
│   ├── hermes_agent.py # HermesAgent
│   └── edp_agent.py   # EDPAgent (+ _ensure_initialized, _collect_stream, _release_session)
├── core/
│   ├── artifact.py    # load_artifact / reassemble_artifact（通用）
│   ├── config.py
│   ├── fitness.py     # EvolutionAdapter (was SkillEvolutionAdapter)
│   ├── constraints.py
│   └── ...
├── skills/
│   ├── skill_module.py # SkillModule + find_skill + thin wrappers
│   └── evolve_skill.py
└── prompts/
    └── evolve_prompt.py  # NEW: prompt 进化 CLI
```

关键接口：

```python
class BaseAgent(ABC):
    @abstractmethod
    def run(self, system_prompt: str, task_input: str, config: EvolutionConfig) -> dict:
        # returns: {"output": str, "messages": list[dict], "completed": bool}
```

候选字典键：`"artifact_body"`（不再是 `"skill_body"`）。

---

## 下一步：Prompt 进化端到端跑通

`evolve_prompt.py` 已完成，但仅验证过 `--dry-run`。下一步需要在有完整 edp_agent 运行环境的机器上端到端跑通。

### 前提条件

1. **运行环境**：需要 agent-framework 全套依赖（openjiuwen、Redis、Runner、Rails、tools、`common.crypto` 等）。本地 WSL 缺少这些，需要在 VM 上跑。
2. **edp_agent 依赖**：`edp_agent/config.py` 导入 `common.crypto.decrypt_config_value`，需要 agent-framework 的 `common/` 模块在 PYTHONPATH 或 sys.path 中。
3. **Redis**：`initialize_dpa()` 会连接 Redis Checkpointer。

### 建议步骤

1. **commit + push 当前重构**到远端分支
2. **在 VM 上 pull 代码**，`uv sync` 安装依赖
3. **准备 AgentRule.md**（`edp_agent/AgentRule.md` 已存在，6 条 rule + 3 条 Rails）
4. **生成 eval dataset**：`SyntheticDatasetBuilder.generate(artifact_text=..., artifact_type="prompt")` — 需要确认 `artifact_type="prompt"` 在 builder 中是否兼容（builder 可能只适配了 "skill"）
5. **运行 dry-run** 确认 setup：
   ```bash
   uv run python -m evolution.prompts.evolve_prompt --prompt edp_agent/AgentRule.md --inference edp-agent --dry-run
   ```
6. **正式运行**（从小迭代开始）：
   ```bash
   uv run python -m evolution.prompts.evolve_prompt \
       --prompt edp_agent/AgentRule.md \
       --inference edp-agent \
       --evaluator fast \
       --iterations 3
   ```

### 已知风险点

| 风险 | 说明 |
|------|------|
| `SyntheticDatasetBuilder` 兼容性 | builder 的 `generate(artifact_type)` 参数可能只处理了 `"skill"`，需要确认 `"prompt"` 路径正常工作 |
| edp_agent 导入路径 | `EDPAgent.run()` 中 `from edp_agent.agent import reload_agent_rule, agent_stream` 在 VM 环境的 PYTHONPATH 下是否能正确解析 |
| gepa.optimize 实际调用 | dry-run 只在 `gepa.optimize()` 之前退出，真正的 GEPA 循环需要 LLM API 调用（`reflection_lm` 参数） |
| AgentRule 体积约束 | AgentRule.md 当前 ~7KB，15KB 限制应该够用，但需关注进化后的膨胀 |
| `_edp_initialized` 全局状态 | 多次 `EDPAgent.run()` 调用共享同一个初始化状态，gepa 多轮评估时是否稳定 |

### 可能会用到的文件

- `edp_agent/AgentRule.md` — 被优化的目标 prompt
- `edp_agent/agent.py` — `reload_agent_rule()` 动态替换 markdown_body
- `evolution/agents/edp_agent.py` — EDPAgent.run() 调用链
- `evolution/core/dataset_builder.py` — 数据集生成，确认 `artifact_type="prompt"` 是否兼容
- `evolution/core/fitness.py` — `EvolutionAdapter.evaluate()` 循环
- `docs/specs/edp-agent-backend.md` — EDPAgent 集成 spec

---

## Suggested Skills

- `agent-skills:incremental-implementation` — 下一步开发（commit → VM 部署 → 端到端调试）
- `agent-skills:debugging-and-error-recovery` — 端到端运行时问题诊断
- `agent-skills:test-driven-development` — 如需为 evolve_prompt.py 补全集成测试
- `agent-skills:planning-and-task-breakdown` — 如果需要先做 commit/PR 再部署

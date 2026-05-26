# Plan 001: Real Agent Inference + Selectable Evaluators

**Status:** Ready
**Created:** 2026-05-26
**Based on:** [Spec 001](../specs/001-real-agent-inference-and-evaluator-selection.md) | [Handoff 001](../handoffs/001-real-agent-inference.md)

---

## Context

当前 `evolve_skill.py` 使用单轮 LLM (`dspy.ChainOfThought`) + 关键词重叠启发式评分来评估 skill 质量。GEPA 的 reflection LLM 看不到 agent 的执行轨迹（tool calls、tool results、multi-turn reasoning），只能基于一个标量分数做变异。同时 `LLMJudge` 类（3D LLM 评分：correctness + procedure_following + conciseness）已完整实现但从未接入进化流程。

**核心问题**：`dspy.GEPA` 的 metric 签名 `(example, prediction, trace) -> float` 只能返回标量，无法传递 trajectory/side_info 给 reflection LLM。

**目标**：迁移到独立 `gepa` 库，让 evaluator 返回 `(score, side_info)` 元组，使 GEPA reflection LLM 能看到完整执行轨迹并做出针对性变异。

## Key Decisions

| 决策 | 选择 | 原因 |
|------|------|------|
| GEPA 库 | 迁移到独立 `gepa.optimize_anything`，保留 dspy.GEPA 作为 fallback | dspy.GEPA 不支持 side_info，独立 gepa 的 `(score, side_info)` 协议是传递 trajectory 的唯一方式 |
| Agent toolsets | `skills_tools` + `context_engine` | 最常用的工具组合，足够覆盖典型 skill 使用场景 |
| Trajectory 存储 | 保存到 `trajectories.jsonl` | 支持离线分析和调试，`_summarize_trajectory()` 截断保证不膨胀 |

## Architecture

### Before

```
evolve_skill.py
  └─ dspy.GEPA(metric=skill_fitness_metric)
       ├─ SkillModule (dspy.ChainOfThought) — 单轮 LLM
       └─ skill_fitness_metric — 关键词重叠，返回 float
```

### After

```
evolve_skill.py
  ├─ [legacy] dspy.GEPA(metric=skill_fitness_metric)  ← single-turn + fast 专用
  │    ├─ SkillModule (dspy.ChainOfThought)
  │    └─ skill_fitness_metric → float
  │
  └─ [new] gepa.optimize_anything(evaluator=make_gepa_evaluator(config))
       └─ evaluator(candidate, example) → (score, side_info)
            ├─ 1. 执行: run_hermes_agent() | run_single_turn()
            ├─ 2. 评分: LLMJudge.score() | _keyword_overlap()
            └─ 3. side_info: {Input, Output, Expected, Feedback, Trajectory}
```

### Module/Metric Selection Matrix

| `--inference` | `--evaluator` | 执行函数 | 评分函数 | side_info | 优化器 |
|---|---|---|---|---|---|
| `single-turn` | `fast` | run_single_turn | _keyword_overlap | 基础 | dspy.GEPA (legacy) |
| `single-turn` | `llm-judge` | run_single_turn | LLMJudge.score | +Feedback | gepa.optimize_anything |
| `hermes-agent` | `fast` | run_hermes_agent | _keyword_overlap | +Trajectory | gepa.optimize_anything |
| `hermes-agent` | `llm-judge` | run_hermes_agent | LLMJudge.score | +Trajectory +Feedback | gepa.optimize_anything |

## Files Modified

所有改动在现有文件中，不创建新文件：

| File | Change |
|------|--------|
| `evolution/core/config.py` | +`inference_mode`, +`evaluator`, +`agent_model`, +`agent_max_iterations` |
| `pyproject.toml` | +`gepa` 依赖 |
| `evolution/core/dataset_builder.py` | +`EvalDataset.to_gepa_datainst()` 方法 |
| `evolution/skills/skill_module.py` | +`run_hermes_agent()`, +`run_single_turn()` 独立函数 |
| `evolution/core/fitness.py` | +`make_gepa_evaluator()` factory, +`_keyword_overlap()`, +`_summarize_trajectory()` |
| `evolution/skills/evolve_skill.py` | +4 CLI options, 两条代码路径（独立 gepa 主路径 + dspy.GEPA fallback）, 输出增强 |

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| gepa 库 API 不稳定 | 保留 dspy.GEPA fallback 路径（single-turn + fast） |
| AIAgent 每次调用很重（加载 tools） | 评估次数有限（~50-200 次/run），可接受；后续可加 instance 缓存 |
| LLM judge 调用使成本翻倍 | `--evaluator fast` 避免 judge 调用，仅需 agent inference 成本 |
| Trajectory 数据量大 | `_summarize_trajectory()` 截断到 20 条消息，完整 raw messages 落盘 JSONL |

## What is NOT Changing

- `SkillModule` 类、`skill_fitness_metric`、`LLMJudge` 类
- `find_skill()`, `load_skill()`, `reassemble_skill()`
- 数据集构建（synthetic/sessiondb/golden）
- 约束验证（ConstraintValidator）
- Holdout 评估逻辑（增强但不重写）

# Handoff: Real Agent Inference + Evaluator Selection

**Date:** 2026-05-26
**From:** Session discussing GEPA evaluation pipeline architecture
**To:** Development planning session

---

## What We Did

1. Analyzed the current GEPA evaluation pipeline and identified two key gaps:
   - `SkillModule` evaluates skills via single-turn LLM (`dspy.ChainOfThought`), not real Hermes agent inference — no tool calls, no multi-turn reasoning, no execution trajectory
   - Fitness scoring uses fast keyword-overlap heuristic; `LLMJudge` (3D LLM scoring: correctness + procedure_following + conciseness) is fully implemented but never wired in

2. Confirmed that `AIAgent.run_conversation()` (at `run_agent.py:4092`) is the real Hermes agent runtime with full multi-turn tool loop, and returns `result["messages"]` containing the complete execution trajectory

3. Confirmed that the GEPA input dataset does NOT require ground truth — `expected_behavior` is a prose rubric, not a correct answer. GEPA uses execution traces and evaluator side_info for reflective mutation

4. Wrote the spec: **`docs/specs/001-real-agent-inference-and-evaluator-selection.md`**

---

## Spec Summary

Four changes across three files, no new files:

| Change | File | Description |
|--------|------|-------------|
| `HermesAgentModule` | `evolution/skills/skill_module.py` | Wraps `AIAgent.run_conversation()` as `dspy.Module`, returns `dspy.Prediction(output=..., messages=...)` |
| `llm_judge_metric()` | `evolution/core/fitness.py` | Adapts `LLMJudge.score()` → `(example, pred, trace) -> float` |
| Config fields | `evolution/core/config.py` | +`agent_model`, +`agent_max_iterations`, +`inference_mode`, +`evaluator` |
| CLI + wiring | `evolution/skills/evolve_skill.py` | `--inference single-turn|hermes-agent`, `--evaluator fast|llm-judge`, module/metric selection |

Module/Metric selection matrix:

| `--inference` | `--evaluator` | Module | Metric |
|---|---|---|---|
| `single-turn` | `fast` | `SkillModule` (existing) | `skill_fitness_metric` (existing) |
| `single-turn` | `llm-judge` | `SkillModule` (existing) | `llm_judge_metric` (new) |
| `hermes-agent` | `fast` | `HermesAgentModule` (new) | `skill_fitness_metric` (existing) |
| `hermes-agent` | `llm-judge` | `HermesAgentModule` (new) | `llm_judge_metric` (new) |

Defaults are `single-turn` + `fast` for full backward compatibility.

---

## Key Technical Context

### Hermes Agent API
```python
from run_agent import AIAgent

agent = AIAgent(
    model="anthropic/claude-sonnet-4-20250514",
    quiet_mode=True,
    suppress_status_output=True,
    max_iterations=15,
    enabled_toolsets=["skills_tools", "context_engine"],
)
result = agent.run_conversation(
    user_message="task description",
    system_message="skill text as system prompt",
)
# result["final_response"] — str
# result["messages"]     — list[dict], full tool call history
```

### Trajectory Flow
```
GEPA Engine
  └─ evaluator(candidate, example)
       ├─ HermesAgentModule.forward(task_input)
       │    └─ AIAgent.run_conversation() → {final_response, messages}
       ├─ llm_judge_metric(example, prediction) → float
       └─ return (score, side_info)
            └─ side_info["Trajectory"] = result["messages"]
            └─ side_info["Feedback"] = judge feedback text
  └─ GEPA reflection LLM reads side_info → proposes targeted mutation
```

### GEPA Library Context
- Standalone `gepa` library at `/home/zixuan_xu/dev/gepa/` (installed via pip, NOT the DSPy built-in)
- Uses `optimize_anything()` API with evaluator returning `(score, side_info_dict)`
- SideInfo (ASI) is the "gradient" — tells reflection LLM *why* candidate failed
- The `gepa_demo/` package in that repo shows the pattern we're following

### Important: Current Code vs. gepa Library
- `evolution/skills/evolve_skill.py` currently calls `dspy.GEPA(...)` — DSPy's built-in optimizer
- The actual GEPA library uses `optimize_anything(seed_candidate, evaluator, dataset, valset, config)` — different API
- Development plan needs to decide: stick with `dspy.GEPA` (simpler, already wired) or migrate to standalone `gepa` library (more powerful, better side_info support)

---

## Related Files

| File | Relevance |
|------|-----------|
| `docs/specs/001-real-agent-inference-and-evaluator-selection.md` | Full spec — read this first |
| `PLAN.md` | Project architecture overview |
| `README.md` | Setup and usage |
| `evolution/skills/evolve_skill.py` | Main entry point, CLI, evolution loop |
| `evolution/skills/skill_module.py` | Current `SkillModule` + `load_skill` / `find_skill` |
| `evolution/core/fitness.py` | `skill_fitness_metric`, `LLMJudge`, `FitnessScore` |
| `evolution/core/config.py` | `EvolutionConfig` dataclass |
| `~/.hermes/hermes-agent/run_agent.py` | `AIAgent` class, `run_conversation()` method |
| `~/.hermes/hermes-agent/agent/conversation_loop.py` | Real agent loop (line 232), result dict (line 4156) |
| `/home/zixuan_xu/dev/gepa/gepa_demo/` | Reference implementation: evaluator pattern, LLMJudge pattern |
| `/home/zixuan_xu/dev/gepa/.venv/lib/python3.12/site-packages/gepa/optimize_anything.py` | GEPA library public API and evaluator protocol |

---

## Next Session: Development Plan

The user wants to write a development plan based on the spec. Key decisions to make:

1. **DSPy GEPA vs. standalone gepa library** — which optimizer API to target?
2. **`enabled_toolsets`** — which Hermes tools to enable during evaluation?
3. **Trajectory storage** — save to disk? JSONL format? Part of metrics.json?
4. **Model selection strategy** — agent inference model vs. judge model vs. optimizer model
5. **Implementation order** — which of the 4 changes to do first, dependencies between them

---

## Suggested Skills

For the next agent picking up this work:

- **`agent-skills:plan`** — Break the spec into ordered implementation tasks with acceptance criteria
- **`agent-skills:spec-driven-development`** — If the spec needs refinement before planning

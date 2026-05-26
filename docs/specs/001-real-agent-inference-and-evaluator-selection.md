# Spec 001: Real Agent Inference + Selectable Evaluators

**Status:** Draft
**Created:** 2026-05-26
**Author:** Zixuan Xu

---

## Objective

Replace the current single-turn LLM evaluation (`dspy.ChainOfThought`) with real Hermes agent inference (`AIAgent.run_conversation()`), and make the evaluator (fast heuristic vs. LLM 3D judge) selectable via CLI. This gives GEPA access to full execution trajectories (tool calls, tool results, multi-turn reasoning) for its reflective mutation analysis.

**Target users:** Hermes Agent developers running skill evolution. Same audience as the current pipeline.

**Problem today:**

| Today | After |
|-------|-------|
| `SkillModule` uses `dspy.ChainOfThought` — single-turn LLM, no tool calls | `HermesAgentModule` uses `AIAgent.run_conversation()` — multi-turn agent with real tool loop |
| `skill_fitness_metric` is keyword-overlap heuristic, hardcoded | CLI selectable: `--evaluator fast` (heuristic) or `--evaluator llm-judge` (3D scoring) |
| No trajectory captured for GEPA reflection | Evaluator returns full `messages` list as side_info |
| `LLMJudge` class exists but is never called | Wired into pipeline when `--evaluator llm-judge` |

---

## Commands

```bash
# Current (unchanged behavior, default backward-compatible)
python -m evolution.skills.evolve_skill --skill arxiv

# Full agent inference + LLM judge
python -m evolution.skills.evolve_skill --skill arxiv \
  --inference hermes-agent \
  --evaluator llm-judge

# Agent inference + fast evaluator (for rapid iteration)
python -m evolution.skills.evolve_skill --skill arxiv \
  --inference hermes-agent \
  --evaluator fast

# Single-turn + LLM judge (for comparison/baseline)
python -m evolution.skills.evolve_skill --skill arxiv \
  --inference single-turn \
  --evaluator llm-judge
```

### CLI Options (new)

| Option | Values | Default | Description |
|--------|--------|---------|-------------|
| `--inference` | `single-turn`, `hermes-agent` | `single-turn` | How to execute the skill during evaluation |
| `--evaluator` | `fast`, `llm-judge` | `fast` | How to score agent outputs |
| `--agent-model` | model string | same as `--eval-model` | Model for Hermes agent inference |
| `--agent-max-iterations` | int | `15` | Max tool-calling rounds per agent run |

### CLI Options (existing, unchanged)

`--skill`, `--iterations`, `--eval-source`, `--dataset-path`, `--optimizer-model`, `--eval-model`, `--hermes-repo`, `--run-tests`, `--dry-run`

---

## Project Structure

```
evolution/
├── core/
│   ├── config.py              # +agent_model, +agent_max_iterations to EvolutionConfig
│   ├── fitness.py             # +llm_judge_metric() wrapper function
│   └── dataset_builder.py     # unchanged
├── skills/
│   ├── evolve_skill.py        # +--inference, +--evaluator CLI options, wiring
│   └── skill_module.py        # +HermesAgentModule class
```

### Files to Create

None. All changes are modifications to existing files.

### Files to Modify

| File | Change |
|------|--------|
| `evolution/skills/skill_module.py` | Add `HermesAgentModule(dspy.Module)` class |
| `evolution/core/fitness.py` | Add `llm_judge_metric()` — DSPy-compatible wrapper around `LLMJudge.score()` |
| `evolution/core/config.py` | Add `agent_model: str`, `agent_max_iterations: int`, `inference_mode: str`, `evaluator: str` to `EvolutionConfig` |
| `evolution/skills/evolve_skill.py` | Add `--inference`, `--evaluator`, `--agent-model`, `--agent-max-iterations` CLI options; wire module/metric selection; handle side_info from evaluator for GEPA reflection |

---

## Architecture

### Component 1: HermesAgentModule (`skill_module.py`)

```python
class HermesAgentModule(dspy.Module):
    """Wraps AIAgent.run_conversation() as a DSPy module."""

    def __init__(self, skill_text: str, config: EvolutionConfig):
        self.skill_text = skill_text
        self.agent = AIAgent(
            model=config.agent_model,
            quiet_mode=True,
            suppress_status_output=True,
            max_iterations=config.agent_max_iterations,
            enabled_toolsets=["skills_tools", "context_engine", ...],  # configurable
        )

    def forward(self, task_input: str) -> dspy.Prediction:
        result = self.agent.run_conversation(
            user_message=task_input,
            system_message=self.skill_text,
        )
        return dspy.Prediction(
            output=result["final_response"] or "",
            messages=result["messages"],        # full trajectory
            api_calls=result["api_calls"],
        )
```

Design decisions:
- `AIAgent` is instantiated once in `__init__`, reused across all `forward()` calls. This is heavy (loads tools, builds system prompt) — amortized over many evaluations.
- `messages` field carries the full execution trajectory: every assistant turn, every tool call with arguments, every tool result.
- `max_iterations=15` default balances thoroughness vs. cost. Each agent run can consume 3-10 API calls depending on tool usage.

### Component 2: `llm_judge_metric()` (`fitness.py`)

Adapts `LLMJudge.score()` (returns `FitnessScore`) to DSPy's metric signature `(example, prediction, trace=None) -> float`:

```python
def llm_judge_metric(example, prediction, trace=None) -> float:
    judge = _get_judge()  # singleton, initialized once
    score = judge.score(
        task_input=example.task_input,
        expected_behavior=example.expected_behavior,
        agent_output=prediction.output,
        skill_text=_current_skill_text,
    )
    # Attach feedback to prediction for GEPA side_info
    prediction.feedback = score.feedback
    return score.composite
```

Key: the `feedback` text from LLMJudge becomes part of the ASI that GEPA's reflection LLM reads. When combined with agent trajectories, this gives the reflection LLM both *what* went wrong (trajectory) and *how to improve* (judge feedback).

### Component 3: Evaluator Integration with GEPA

The evaluator function (passed to `optimize_anything` or DSPy's `metric`) is responsible for returning trajectory data as side_info:

```
evaluator(candidate, example)
  │
  ├─ 1. candidate["skill_body"] → HermesAgentModule
  ├─ 2. Run agent → prediction (output + messages)
  ├─ 3. Score via --evaluator (fast or llm-judge)
  └─ 4. Return (score, side_info)
       └─ side_info: {
            "Input": example.task_input,
            "Output": prediction.output,
            "Trajectory": prediction.messages,
            "Expected": example.expected_behavior,
            "Feedback": prediction.feedback,   # from LLMJudge
          }
```

Note: If using DSPy's built-in `dspy.GEPA` (not the standalone `gepa` library), the integration point is through the `trace` parameter in the metric function. If using the standalone `gepa` library's `optimize_anything`, the evaluator returns `(score, side_info)` directly.

### Module/Metric Selection Matrix

| `--inference` | `--evaluator` | Module | Metric |
|---|---|---|---|
| `single-turn` | `fast` | `SkillModule` (existing) | `skill_fitness_metric` (existing) |
| `single-turn` | `llm-judge` | `SkillModule` (existing) | `llm_judge_metric` (new) |
| `hermes-agent` | `fast` | `HermesAgentModule` (new) | `skill_fitness_metric` (existing) |
| `hermes-agent` | `llm-judge` | `HermesAgentModule` (new) | `llm_judge_metric` (new) |

---

## Code Style

Follow existing patterns in the codebase:
- `dspy.Module` subclass with `forward()` returning `dspy.Prediction`
- `click.option` for CLI, `rich` for console output
- Dataclass-based config in `EvolutionConfig`
- Metric functions follow `(example, prediction, trace=None) -> float` signature

No new dependencies. `AIAgent` is already importable from hermes-agent.

---

## Testing Strategy

### Unit Tests
- `test_hermes_agent_module.py`: verify `HermesAgentModule.forward()` returns `dspy.Prediction` with `output` and `messages` fields
- `test_llm_judge_metric.py`: verify `llm_judge_metric()` returns float 0-1, attaches feedback to prediction

### Integration Tests
- `test_evolve_e2e.py`: run full evolution with `--inference single-turn --evaluator fast` (regression), then `--inference hermes-agent --evaluator llm-judge` (new path)
- Mock `AIAgent` to avoid real API calls in CI

### Manual Validation
- Run `--inference hermes-agent --evaluator llm-judge` on a real skill (e.g. `arxiv`)
- Verify console output shows multi-dimensional scores (correctness, procedure_following, conciseness) instead of single float
- Verify output directory contains trajectory data in metrics or a separate trajectory file
- Check that evolved skill passes constraint validation

---

## Boundaries

### Always Do
- Keep `single-turn` + `fast` as defaults — backward compatible, no surprises
- `AIAgent` instantiation once per module, reused across forward() calls
- Capture full `messages` list from `run_conversation()` result
- Pass trajectory as side_info so GEPA reflection LLM can see it

### Ask First
- Which `enabled_toolsets` to use for Hermes agent evaluation (impacts token usage and evaluation fidelity)
- Whether trajectory data should be saved to disk (large — can be 10s of KB per evaluation)
- Whether to add a `--trajectory-output` flag to save trajectories to a JSONL file for offline analysis
- Model choice for agent inference vs. evaluator judge (they can be different)

### Never Do
- Run Hermes agent with destructive tools enabled (terminal, file write, etc.)
- Use `AIAgent` in non-quiet mode — stdout would interleave with evolution progress bars
- Expose raw `AIAgent` constructor args directly to CLI — use config dataclass
- Skip constraint validation after evolution regardless of inference/evaluator mode

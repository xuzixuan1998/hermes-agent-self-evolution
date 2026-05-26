# Tasks: Real Agent Inference + Selectable Evaluators

**Plan:** [plan.md](./plan.md)
**Spec:** [001-real-agent-inference-and-evaluator-selection](../../specs/001-real-agent-inference-and-evaluator-selection.md)

---

## Vertical Slice 1: Config Foundation (no behavior change)

### Task 1.1: Add config fields
**Files:** `evolution/core/config.py`
**Depends on:** nothing

Add `inference_mode`, `evaluator`, `agent_model`, `agent_max_iterations` to `EvolutionConfig`.

**Acceptance criteria:**
- `EvolutionConfig()` 创建时新字段使用默认值（`single-turn`, `fast`, `None`, `10`）
- `agent_model=None` 时行为如旧（不设置具体模型字符串）
- 现有代码不改一行即可正常运行

**Verification:**
- `python -c "from evolution.core.config import EvolutionConfig; c = EvolutionConfig(); print(c.inference_mode, c.evaluator)"` → `single-turn fast`

---

### Task 1.2: Add gepa dependency
**Files:** `pyproject.toml`
**Depends on:** nothing

添加 `gepa` 到项目依赖。

**Acceptance criteria:**
- `pip install -e .` 成功且 gepa 可用
- `python -c "from gepa.optimize_anything import optimize_anything"` 成功

**Verification:**
- `python -c "import gepa; print(gepa.__version__)"` 输出版本号

---

## Vertical Slice 2: Dataset + Agent Execution (bottom-up, no wiring)

### Task 2.1: Add to_gepa_datainst()
**Files:** `evolution/core/dataset_builder.py`
**Depends on:** Task 1.1

在 `EvalDataset` 添加 `to_gepa_datainst(split)` 方法。

**Acceptance criteria:**
- 返回 `list[dict]`，每个 dict 包含 `input`, `answer`, `additional_context`
- 不修改现有 `to_dspy_examples()` 行为
- 空 split 返回空列表

**Verification:**
- 构造含 2 个 example 的 EvalDataset，调用 `to_gepa_datainst("train")` → 返回 2 个正确格式的 dict

---

### Task 2.2: Add run_single_turn() and run_hermes_agent()
**Files:** `evolution/skills/skill_module.py`
**Depends on:** Task 1.1

添加 `run_single_turn(skill_text, task_input, config)` 和 `run_hermes_agent(skill_text, task_input, config)` 两个独立函数。

**Acceptance criteria:**
- 两个函数返回统一 dict 格式：`{"output": str, "messages": list[dict], "completed": bool}`
- `run_single_turn` 使用 `dspy.ChainOfThought`，`messages` 至少包含 user + assistant 两条
- `run_hermes_agent` lazy import `AIAgent`，使用 `quiet_mode=True`
- 不修改现有 `SkillModule` 类

**Verification:**
- Mock `AIAgent` → 调用 `run_hermes_agent` → 返回 dict 含 `output`、`messages`、`completed`
- 调用 `run_single_turn` → 返回 dict，`completed=True`，`messages` 长度 >= 2

---

## Vertical Slice 3: Evaluator Factory (core logic)

### Task 3.1: Extract _keyword_overlap() + add _summarize_trajectory()
**Files:** `evolution/core/fitness.py`
**Depends on:** nothing

从 `skill_fitness_metric` 提取 `_keyword_overlap()` 函数；添加 `_summarize_trajectory()` 辅助函数。

**Acceptance criteria:**
- `_keyword_overlap(output, expected)` 行为与原有逻辑一致
- `_summarize_trajectory(messages)` 返回包含 `total_messages`, `tool_calls_used`, `summary` 的 dict
- 截断到最多 20 条消息
- `skill_fitness_metric` 内部调用 `_keyword_overlap()` 保持不变

**Verification:**
- `_keyword_overlap("hello world", "hello")` → > 0.5
- `_keyword_overlap("", "expected")` → 0.0
- 构造 25 条 message 的 trajectory → `summary` 字符串包含 "... (5 more messages)"

---

### Task 3.2: Add make_gepa_evaluator() factory
**Files:** `evolution/core/fitness.py`
**Depends on:** Tasks 2.2, 3.1

实现 evaluator factory，返回符合 gepa 协议的 `(candidate, example) -> (score, side_info)` 函数。

**Acceptance criteria:**
- `single-turn + fast` → 调用 run_single_turn + _keyword_overlap
- `single-turn + llm-judge` → 调用 run_single_turn + LLMJudge.score()
- `hermes-agent + fast` → 调用 run_hermes_agent + _keyword_overlap，side_info 含 Trajectory
- `hermes-agent + llm-judge` → 调用 run_hermes_agent + LLMJudge.score()，side_info 含 Trajectory + Feedback
- 返回的 score 为 float，side_info 为 dict
- 保留 `LLMJudge` 类和 `skill_fitness_metric` 函数不变

**Verification:**
- 4 种组合各写一个 pytest，mock 执行函数，验证返回的 `(score, side_info)` 结构和内容

---

## Vertical Slice 4: CLI + Wiring (integration)

### Task 4.1: Add CLI options + wiring
**Files:** `evolution/skills/evolve_skill.py`
**Depends on:** Tasks 2.2, 3.2

添加 `--inference`, `--evaluator`, `--agent-model`, `--agent-max-iterations` CLI options，实现两条代码路径的路由逻辑。

**Acceptance criteria:**
- `--inference single-turn --evaluator fast`（默认）走 dspy.GEPA 路径，行为与改造前完全一致
- 其他 3 种组合走 `gepa.optimize_anything` 路径
- `--agent-model` 不指定时回退到 `--optimizer-model` 的值
- `--dry-run` 输出包含 inference_mode 和 evaluator 信息
- `--help` 显示所有 4 个新 option

**Verification:**
- `python -m evolution.skills.evolve_skill --skill arxiv --dry-run` → 输出 `inference: single-turn, evaluator: fast`
- `python -m evolution.skills.evolve_skill --skill arxiv --inference hermes-agent --evaluator llm-judge --dry-run` → 输出新配置

---

### Task 4.2: Output enhancement (trajectories.jsonl + config.json)
**Files:** `evolution/skills/evolve_skill.py`
**Depends on:** Task 4.1

在 output 目录新增 `trajectories.jsonl` 和 `config.json`。

**Acceptance criteria:**
- gepa 路径运行时，`output/<skill>/<timestamp>/trajectories.jsonl` 存在
- 每行一个 JSON object，包含 `candidate_preview`, `task`, `score`, `trajectory_summary`
- `config.json` 包含完整配置快照（inference_mode、evaluator、模型等）
- dspy.GEPA 路径也保存 `config.json`

**Verification:**
- 运行 gepa 路径后检查 `trajectories.jsonl` 行数 > 0，JSON 格式正确

---

## Checkpoint: End-to-End Smoke Test

在进入测试阶段前，手动运行一次完整流程：

```bash
python -m evolution.skills.evolve_skill \
  --skill arxiv \
  --inference single-turn \
  --evaluator llm-judge \
  --iterations 3 \
  --eval-source synthetic
```

验证：
- 不报错完成
- output 目录包含 `evolved_skill.md`, `metrics.json`, `trajectories.jsonl`, `config.json`
- 控制台输出包含 LLM judge 评分维度

---

## Vertical Slice 5: Tests

### Task 5.1: Unit tests — config + dataset
**Files:** `tests/core/test_config.py` (new/update), `tests/core/test_dataset_builder.py` (update)
**Depends on:** Tasks 1.1, 2.1

**Acceptance criteria:**
- `test_config_defaults` — 新字段默认值正确
- `test_config_agent_model_fallback` — agent_model=None 时不设置模型（fallback 在 wiring 层处理）
- `test_to_gepa_datainst` — 正确转换为 gepa dict 格式
- `test_to_gepa_datainst_empty_split` — 空列表返回空列表

---

### Task 5.2: Unit tests — agent functions + evaluator
**Files:** `tests/skills/test_skill_module.py` (update), `tests/core/test_fitness.py` (update)
**Depends on:** Tasks 2.2, 3.1, 3.2

**Acceptance criteria:**
- `test_run_single_turn_returns_dict` — mock dspy，验证返回格式
- `test_run_hermes_agent_returns_dict` — mock AIAgent，验证返回格式
- `test_keyword_overlap_full_match` — 完全匹配 → ~1.0
- `test_keyword_overlap_empty_output` — 空输出 → 0.0
- `test_summarize_trajectory_extracts_tool_calls` — 正确提取 tool call 名称
- `test_summarize_trajectory_truncation` — 超过 20 条时截断
- `test_make_evaluator_fast_single` → (float, dict) 不含 Trajectory
- `test_make_evaluator_llm_judge_single` → side_info 含 Feedback
- `test_make_evaluator_fast_hermes` → side_info 含 Trajectory
- `test_make_evaluator_llm_judge_hermes` → side_info 含 Trajectory + Feedback

---

### Task 5.3: Integration tests — evolve paths
**Files:** `tests/skills/test_evolve.py` (new)
**Depends on:** Task 4.1

**Acceptance criteria:**
- `test_evolve_dspy_path_regression` — mock 全部外部依赖，验证 single-turn+fast 仍调用 dspy.GEPA
- `test_evolve_standalone_path_smoke` — mock `optimize_anything`，验证 hermes-agent+llm-judge 走 gepa 路径
- `test_evolve_dry_run_shows_config` — dry-run 输出包含新配置信息

---

## Dependency Graph

```
Task 1.1 (config fields) ──┬── Task 2.1 (dataset) ──┐
                           ├── Task 2.2 (agent fns) ──┼── Task 3.2 (evaluator) ──┬── Task 4.1 (CLI+wiring) ──┬── Task 5.3 (integration)
Task 1.2 (gepa dep) ───────┤                         │                          │                          │
                           └── Task 3.1 (helpers) ────┘                          │                          │
                                                                                 └── Task 4.2 (output) ──────┤
                                                                                                              │
                                                              Task 5.1 (unit:config+dataset) ──────────────────┤
                                                              Task 5.2 (unit:agent+evaluator) ────────────────┘
```

## Implementation Order

1. **Slice 1** (Tasks 1.1, 1.2) — infra setup, zero risk
2. **Slice 2** (Tasks 2.1, 2.2) — bottom-up building blocks
3. **Slice 3** (Tasks 3.1, 3.2) — core evaluator logic
4. **Slice 4** (Tasks 4.1, 4.2) — integration + CLI
5. **Checkpoint** — manual e2e smoke test
6. **Slice 5** (Tasks 5.1, 5.2, 5.3) — tests

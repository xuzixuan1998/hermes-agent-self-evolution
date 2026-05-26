# Hermes Agent Self-Evolution

**Evolutionary self-improvement for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

Hermes Agent Self-Evolution uses GEPA (Genetic Evolutionary Pareto Algorithm) to automatically evolve and optimize Hermes Agent's skills — running real agent inference, scoring with LLM-as-judge, and producing measurably better versions through reflective evolutionary search.

**No GPU training required.** Everything operates via API calls — mutating text, evaluating with real agent execution, and selecting the best variants.

## How It Works

```
Read current skill ──► Generate eval dataset ──► GEPA Optimizer
                                                     │
                          ┌──────────────────────────┘
                          ▼
                    Run agent on each example (single-turn or full Hermes agent)
                          │
                          ▼
                    Score outputs (fast keyword match or LLM 3D judge)
                          │
                          ▼
                    GEPA reads trajectories + scores → proposes targeted mutations
                          │
                          ▼
                    Constraint gates (size limits, structure checks)
                          │
                          ▼
                    Best variant + trajectories.jsonl + metrics
```

GEPA reads execution trajectories (tool calls, tool results, multi-turn reasoning) to understand *why* things fail — not just that they fail — then proposes targeted improvements. Supports both fast single-turn LLM evaluation and full Hermes agent inference with real tool calling.

## Quick Start

```bash
# Install
git clone https://github.com/NousResearch/hermes-agent-self-evolution.git
cd hermes-agent-self-evolution
pip install -e ".[dev]"

# Set API keys and hermes-agent path
export DEEPSEEK_API_KEY=sk-...          # or OPENAI_API_KEY
export HERMES_AGENT_REPO=~/.hermes/hermes-agent

# Default: single-turn + fast evaluator (backward compatible)
python -m evolution.skills.evolve_skill \
    --skill arxiv --iterations 10

# Full agent inference + LLM judge (recommended for best results)
python -m evolution.skills.evolve_skill \
    --skill arxiv \
    --inference hermes-agent \
    --evaluator llm-judge \
    --iterations 10 \
    --agent-model deepseek-v4-flash \
    --agent-max-iterations 8
```

## Inference & Evaluator Modes

| `--inference` | `--evaluator` | Engine | Description |
|---------------|---------------|--------|-------------|
| `single-turn` (default) | `fast` (default) | dspy.GEPA | Single LLM call, keyword overlap scoring — fastest, backward compatible |
| `single-turn` | `llm-judge` | gepa.optimize | Single LLM call + 3D judge scoring (correctness, procedure, conciseness) |
| `hermes-agent` | `fast` | gepa.optimize | Real agent with tool calls, fast keyword scoring + trajectory capture |
| `hermes-agent` | `llm-judge` | gepa.optimize | Real agent + full 3D judge — richest feedback for GEPA reflection |

### Evaluator Details

**`fast`** — Keyword overlap heuristic. Computes Jaccard-like overlap between agent output and expected behavior rubric. Near-zero cost, instant.

**`llm-judge`** — Multi-dimensional LLM scoring:
- `correctness` (0-1): Did the response correctly address the task?
- `procedure_following` (0-1): Did it follow the expected approach?
- `conciseness` (0-1): Was it appropriately concise?
- `feedback` (text): Actionable improvement suggestions for GEPA reflection

## CLI Options

```
Usage: python -m evolution.skills.evolve_skill [OPTIONS]

Core:
  --skill TEXT                  Name of the skill to evolve  [required]
  --iterations INTEGER          Number of GEPA iterations (default: 10)

Inference (how to execute the skill during evaluation):
  --inference [single-turn|hermes-agent]  (default: single-turn)
  --agent-model TEXT            Model for Hermes agent inference
                                (defaults to --optimizer-model)
  --agent-max-iterations INTEGER  Max tool-calling rounds per agent (default: 10)

Evaluator (how to score agent outputs):
  --evaluator [fast|llm-judge]  (default: fast)

Models:
  --optimizer-model TEXT        Model for GEPA reflection/mutation (default: openai/gpt-4.1)
  --eval-model TEXT             Model for LLM judge scoring (default: openai/gpt-4.1-mini)

Data:
  --eval-source [synthetic|golden|sessiondb]  (default: synthetic)
  --dataset-path TEXT           Path to existing eval dataset (JSONL directory)

Other:
  --hermes-repo TEXT            Path to hermes-agent repo
  --dry-run                     Validate setup without running optimization
  --run-tests                   Run pytest suite as constraint gate
```

## Output Structure

Each run creates a timestamped output directory:

```
output/<skill>/<YYYYmmdd_HHMMSS>/
├── evolved_skill.md       # Optimized skill file
├── baseline_skill.md      # Original skill for comparison
├── metrics.json           # Scores, sizes, timings
├── config.json            # Full configuration snapshot
└── trajectories.jsonl     # Per-example agent execution traces
```

Each line in `trajectories.jsonl` contains:
```json
{
  "task_input": "Find papers about GRPO...",
  "expected": "Agent performs arXiv search, then uses Semantic Scholar...",
  "output": "Here are the results...",
  "score": 0.84,
  "feedback": "Correctly identified papers but conciseness could improve...",
  "trajectory": {
    "total_messages": 11,
    "tool_calls_used": ["web_extract", "terminal"],
    "summary": "11 messages, 2 unique tools: web_extract, terminal"
  }
}
```

## Evaluation Dataset

Datasets are generated by `SyntheticDatasetBuilder` — an LLM reads the target skill and produces diverse test cases:

```
datasets/skills/<skill>/
├── train.jsonl      (50% — GEPA training/optimization)
├── val.jsonl        (25% — GEPA validation/candidate selection)
└── holdout.jsonl    (25% — final evaluation, not used in optimization)
```

Each example has:
- `task_input` — realistic user query
- `expected_behavior` — evaluation rubric (what a good response looks like)
- `difficulty` — easy / medium / hard
- `category` — for stratified evaluation

## Examples

```bash
# Fast iteration: single-turn + fast evaluator (cheapest)
python -m evolution.skills.evolve_skill --skill arxiv \
    --inference single-turn --evaluator fast --iterations 5

# Quality-focused: full agent + LLM judge (best results, higher cost)
python -m evolution.skills.evolve_skill --skill arxiv \
    --inference hermes-agent --evaluator llm-judge \
    --iterations 10 --agent-model deepseek-v4-flash --agent-max-iterations 8

# Dry-run: validate setup without spending API calls
python -m evolution.skills.evolve_skill --skill arxiv \
    --inference hermes-agent --evaluator llm-judge --dry-run

# Use existing dataset (skip generation)
python -m evolution.skills.evolve_skill --skill arxiv \
    --eval-source golden --dataset-path datasets/skills/arxiv/

# Mine real session history for eval data
python -m evolution.skills.evolve_skill --skill arxiv \
    --eval-source sessiondb --iterations 10
```

## Guardrails

Every evolved variant must pass:
1. **Size limits** — Skills ≤15KB
2. **Growth limits** — Max 20% size increase over baseline
3. **Non-empty** — Must contain actual content
4. **Skill structure** — Valid YAML frontmatter (name + description)
5. **PR review** — All changes go through human review, never direct commit

## License

MIT — (c) 2026 Nous Research

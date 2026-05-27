"""Fitness functions for evaluating evolved artifacts.

Uses LLM-as-judge with rubrics to score agent outputs.
Supports length penalties and multi-dimensional scoring.
"""

import dspy
from dataclasses import dataclass
from typing import Optional

from evolution.core.config import EvolutionConfig


@dataclass
class FitnessScore:
    """Multi-dimensional fitness score."""
    correctness: float = 0.0  # Did the agent produce correct output? (0-1)
    procedure_following: float = 0.0  # Did it follow the skill's procedure? (0-1)
    conciseness: float = 0.0  # Was it appropriately concise? (0-1)
    length_penalty: float = 0.0  # Penalty for being too verbose (0-1, 0 = no penalty)
    feedback: str = ""  # Textual feedback for GEPA's reflective analysis

    @property
    def composite(self) -> float:
        """Weighted composite score."""
        raw = (
            0.5 * self.correctness
            + 0.3 * self.procedure_following
            + 0.2 * self.conciseness
        )
        return max(0.0, raw - self.length_penalty)


class LLMJudge:
    """LLM-as-judge scorer with rubric-based evaluation.

    Scores agent outputs on multiple dimensions and provides
    textual feedback that GEPA can use for reflective mutation.
    """

    class JudgeSignature(dspy.Signature):
        """Evaluate an agent's response against an expected behavior rubric.

        Score the response on three dimensions (0.0 to 1.0 each):
        1. correctness: Did the response correctly address the task?
        2. procedure_following: Did it follow the expected approach/procedure?
        3. conciseness: Was it appropriately concise without omitting important info?

        Also provide specific, actionable feedback on what could be improved.
        """
        task_input: str = dspy.InputField(desc="The task the agent was given")
        expected_behavior: str = dspy.InputField(desc="Rubric describing what a good response looks like")
        agent_output: str = dspy.InputField(desc="The agent's actual response")
        skill_text: str = dspy.InputField(desc="The skill/instructions the agent was following")
        correctness: float = dspy.OutputField(desc="Score 0.0-1.0: Did the response correctly address the task?")
        procedure_following: float = dspy.OutputField(desc="Score 0.0-1.0: Did it follow the expected procedure?")
        conciseness: float = dspy.OutputField(desc="Score 0.0-1.0: Appropriately concise?")
        feedback: str = dspy.OutputField(desc="Specific, actionable feedback on what could be improved")

    def __init__(self, config: EvolutionConfig):
        self.config = config
        self.judge = dspy.ChainOfThought(self.JudgeSignature)

    def score(
        self,
        task_input: str,
        expected_behavior: str,
        agent_output: str,
        skill_text: str,
        artifact_size: Optional[int] = None,
        max_size: Optional[int] = None,
    ) -> FitnessScore:
        """Score an agent output using LLM-as-judge."""

        lm = dspy.LM(self.config.eval_model)

        with dspy.context(lm=lm):
            result = self.judge(
                task_input=task_input,
                expected_behavior=expected_behavior,
                agent_output=agent_output,
                skill_text=skill_text,
            )

        # Parse scores (clamp to 0-1)
        correctness = _parse_score(result.correctness)
        procedure_following = _parse_score(result.procedure_following)
        conciseness = _parse_score(result.conciseness)

        # Length penalty
        length_penalty = 0.0
        if artifact_size is not None and max_size is not None:
            ratio = artifact_size / max_size
            if ratio > 0.9:
                # Penalty ramps from 0 at 90% to 0.3 at 100%+
                length_penalty = min(0.3, (ratio - 0.9) * 3.0)

        return FitnessScore(
            correctness=correctness,
            procedure_following=procedure_following,
            conciseness=conciseness,
            length_penalty=length_penalty,
            feedback=str(result.feedback),
        )


def _keyword_overlap(output: str, expected: str) -> float:
    """Fast keyword-overlap heuristic score (0.0-1.0)."""
    if not output.strip():
        return 0.0

    expected_lower = expected.lower()
    output_lower = output.lower()

    expected_words = set(expected_lower.split())
    output_words = set(output_lower.split())
    if not expected_words:
        return 0.5

    overlap = len(expected_words & output_words) / len(expected_words)
    return min(1.0, max(0.0, 0.3 + 0.7 * overlap))


def _summarize_trajectory(messages: list[dict], max_messages: int = 20) -> dict:
    """Summarize an agent execution trajectory for side_info.

    Extracts tool call names, message count, and a truncated summary.
    """
    total = len(messages)
    truncated = messages[:max_messages]
    remaining = total - max_messages

    tool_calls_used = []
    for msg in truncated:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                name = tc.get("function", {}).get("name", "")
                if name and name not in tool_calls_used:
                    tool_calls_used.append(name)

    summary = f"{total} messages, {len(tool_calls_used)} unique tools"
    if tool_calls_used:
        summary += f": {', '.join(tool_calls_used)}"
    if remaining > 0:
        summary += f" ... ({remaining} more messages)"

    return {
        "total_messages": total,
        "tool_calls_used": tool_calls_used,
        "summary": summary,
    }


def skill_fitness_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> float:
    """DSPy-compatible metric function for skill optimization.

    This is what gets passed to dspy.GEPA(metric=...).
    Returns a float 0-1 score.
    """
    agent_output = getattr(prediction, "output", "") or ""
    expected = getattr(example, "expected_behavior", "") or ""

    return _keyword_overlap(agent_output, expected)


def _parse_score(value) -> float:
    """Parse a score value, handling various LLM output formats."""
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    try:
        return min(1.0, max(0.0, float(str(value).strip())))
    except (ValueError, TypeError):
        return 0.5  # Default to neutral on parse failure


def make_gepa_evaluator(config: "EvolutionConfig"):
    """Create a per-example evaluator compatible with SkillEvolutionAdapter.

    Returns a function (data, response) -> EvaluationResult.
    data is a gepa DefaultDataInst dict with 'input', 'answer' keys.
    """
    evaluator = config.evaluator
    judge = LLMJudge(config) if evaluator == "llm-judge" else None

    def evaluator_fn(data, response: str):
        task_input = ""
        expected = ""
        if isinstance(data, dict):
            task_input = data.get("input", "") or ""
            expected = data.get("answer", "") or ""

        feedback = ""
        if evaluator == "llm-judge" and judge:
            fitness = judge.score(
                task_input=task_input,
                expected_behavior=expected,
                agent_output=response,
                skill_text="",
            )
            score = fitness.composite
            feedback = fitness.feedback
        else:
            score = _keyword_overlap(response, expected)

        from gepa.adapters.default_adapter.default_adapter import EvaluationResult
        return EvaluationResult(score=score, feedback=feedback)

    return evaluator_fn


class SkillEvolutionAdapter:
    """Custom GEPAAdapter that runs skill evaluation via Hermes agent or single-turn LLM.

    Implements gepa's GEPAAdapter protocol so the full agent execution (tool calls,
    multi-turn reasoning) is part of the evaluation, not just a single LLM completion.
    """

    def __init__(self, config: "EvolutionConfig"):
        from evolution.skills.skill_module import run_single_turn, run_hermes_agent

        self.config = config
        self.inference = config.inference_mode
        self.evaluator = config.evaluator

        if self.inference == "edp-agent":
            from evolution.skills.skill_module import run_edp_agent
            self.run_fn = run_edp_agent
        elif self.inference == "hermes-agent":
            self.run_fn = run_hermes_agent
        else:
            self.run_fn = run_single_turn

        self.judge = LLMJudge(config) if self.evaluator == "llm-judge" else None
        self._trajectories: list[dict] = []

    @property
    def trajectories(self) -> list[dict]:
        return self._trajectories

    def evaluate(self, batch, candidate, capture_traces=False):
        """Run candidate on each DataInst and return EvaluationBatch."""
        from gepa.core.adapter import EvaluationBatch

        skill_text = ""
        if isinstance(candidate, dict):
            skill_text = candidate.get("skill_body", "") or ""

        outputs = []
        scores = []
        trajectories = []

        for data in batch:
            task_input = ""
            expected = ""
            if isinstance(data, dict):
                task_input = data.get("input", "") or ""
                expected = data.get("answer", "") or ""

            result = self.run_fn(skill_text, task_input, self.config)
            output = result["output"]
            messages = result["messages"]

            if self.evaluator == "llm-judge" and self.judge:
                fitness = self.judge.score(
                    task_input=task_input,
                    expected_behavior=expected,
                    agent_output=output,
                    skill_text=skill_text,
                )
                score = fitness.composite
                feedback = fitness.feedback
            else:
                score = _keyword_overlap(output, expected)
                feedback = ""

            outputs.append(output)
            scores.append(score)

            # Always record trajectory for analysis
            traj = {
                "task_input": task_input,
                "expected": expected,
                "output": output[:500] if output else "",
                "score": score,
                "feedback": feedback,
                "trajectory": _summarize_trajectory(messages) if self.inference == "hermes-agent" else {},
                "messages": messages[:10] if messages else [],
            }
            trajectories.append(traj)
            self._trajectories.append(traj)

        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories if capture_traces else None,
        )

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        """Build a reflective dataset from evaluation trajectories."""
        dataset = {}
        for comp in components_to_update:
            records = []
            if eval_batch.trajectories:
                for traj in eval_batch.trajectories:
                    records.append({
                        "Inputs": {"task": traj.get("task_input", "")},
                        "Generated Outputs": {"output": traj.get("output", "")},
                        "Feedback": traj.get("feedback", ""),
                        "Expected": traj.get("expected", ""),
                        "Score": traj.get("score", 0.0),
                    })
            dataset[comp] = records
        return dataset

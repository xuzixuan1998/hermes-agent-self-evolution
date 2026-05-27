"""Tests for fitness functions: keyword overlap, trajectory summarization,
make_gepa_evaluator, and EvolutionAdapter."""

import pytest
from unittest.mock import MagicMock, patch

from evolution.core.config import EvolutionConfig
from evolution.core.fitness import (
    _keyword_overlap,
    _summarize_trajectory,
    make_gepa_evaluator,
    EvolutionAdapter,
    skill_fitness_metric,
)


class TestKeywordOverlap:
    def test_full_match(self):
        assert _keyword_overlap("hello world", "hello world") > 0.9

    def test_partial(self):
        assert _keyword_overlap("hello world foo bar", "hello") > 0.5

    def test_empty_output(self):
        assert _keyword_overlap("", "expected") == 0.0

    def test_empty_expected(self):
        assert _keyword_overlap("output", "") == 0.5


class TestSummarizeTrajectory:
    def test_extracts_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "search"}}],
            },
            {"role": "user", "content": "result"},
        ]
        result = _summarize_trajectory(msgs)
        assert result["tool_calls_used"] == ["search"]
        assert result["total_messages"] == 2

    def test_truncation(self):
        msgs = [{"role": "user", "content": f"msg_{i}"} for i in range(25)]
        result = _summarize_trajectory(msgs)
        assert "(5 more messages)" in result["summary"]
        assert result["total_messages"] == 25

    def test_no_tool_calls(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        result = _summarize_trajectory(msgs)
        assert result["tool_calls_used"] == []


class TestMakeGepaEvaluator:
    """Tests for make_gepa_evaluator.

    Returns (data: dict, response: str) -> EvaluationResult(score, feedback, objective_scores).
    """

    @staticmethod
    def _data(**overrides):
        ex = {"input": "solve the task", "answer": "expected answer"}
        ex.update(overrides)
        return ex

    def test_fast_evaluator(self):
        """fast evaluator uses _keyword_overlap."""
        config = EvolutionConfig(inference_mode="single-turn", evaluator="fast")
        fn = make_gepa_evaluator(config)
        result = fn(self._data(), "expected answer test output")
        assert isinstance(result.score, float)
        assert result.score > 0.5

    def test_fast_evaluator_low_score(self):
        """fast evaluator: low keyword overlap."""
        config = EvolutionConfig(inference_mode="single-turn", evaluator="fast")
        fn = make_gepa_evaluator(config)
        result = fn(self._data(answer="completely different"), "test output")
        assert result.score <= 0.5

    def test_llm_judge_evaluator(self):
        """llm-judge evaluator uses LLMJudge, returns score + feedback."""
        config = EvolutionConfig(inference_mode="single-turn", evaluator="llm-judge")
        with patch("evolution.core.fitness.LLMJudge") as mock_judge_cls:
            mock_judge_instance = MagicMock()
            mock_judge_instance.score.return_value.composite = 0.85
            mock_judge_instance.score.return_value.feedback = "Decent"
            mock_judge_cls.return_value = mock_judge_instance

            fn = make_gepa_evaluator(config)
            result = fn(self._data(), "test output")

        assert isinstance(result.score, float)
        assert result.score == 0.85
        assert result.feedback == "Decent"


class TestEvolutionAdapter:
    """Tests for EvolutionAdapter (custom GEPAAdapter)."""

    def _config(self, inference="single-turn", evaluator="fast"):
        return EvolutionConfig(inference_mode=inference, evaluator=evaluator)

    def _mock_agent(self, agent_path, return_value=None):
        """Patch an agent class and return the mock instance."""
        rv = return_value or {"output": "test output", "messages": [], "completed": True}
        mock_agent_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run.return_value = rv
        mock_agent_cls.return_value = mock_instance
        return patch(agent_path, mock_agent_cls), mock_instance

    def test_evaluate_basic(self):
        """evaluate() returns EvaluationBatch with correct lengths."""
        config = self._config()
        with patch("evolution.agents.single_turn.SingleTurnAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {"output": "test output", "messages": [], "completed": True}
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)
            batch = [
                {"input": "task1", "answer": "expected1"},
                {"input": "task2", "answer": "expected2"},
            ]
            result = adapter.evaluate(batch, {"artifact_body": "artifact text"}, capture_traces=False)

            assert len(result.scores) == 2
            assert len(result.outputs) == 2
            assert result.trajectories is None
            assert mock_agent.run.call_count == 2

    def test_evaluate_with_traces(self):
        """evaluate() with capture_traces=True returns trajectories."""
        config = self._config(inference="hermes-agent")
        with patch("evolution.agents.hermes_agent.HermesAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {
                "output": "test output",
                "messages": [{"role": "user", "content": "hi"}],
                "completed": True,
            }
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)
            batch = [{"input": "task1", "answer": "expected1"}]
            result = adapter.evaluate(batch, {"artifact_body": "artifact text"}, capture_traces=True)

            assert len(result.trajectories) == 1
            assert result.trajectories[0]["output"] == "test output"

    def test_make_reflective_dataset(self):
        """make_reflective_dataset builds per-component records."""
        config = self._config()
        with patch("evolution.agents.single_turn.SingleTurnAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {"output": "test", "messages": [], "completed": True}
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)
            batch = [{"input": "task1", "answer": "expected1"}]
            eval_batch = adapter.evaluate(batch, {"artifact_body": "artifact"}, capture_traces=True)

            dataset = adapter.make_reflective_dataset(
                {"artifact_body": "artifact"}, eval_batch, ["artifact_body"]
            )

            assert "artifact_body" in dataset
            assert len(dataset["artifact_body"]) == 1
            rec = dataset["artifact_body"][0]
            assert rec["Inputs"]["task"] == "task1"
            assert "Score" in rec

    def test_trajectories_property(self):
        """adapter.trajectories collects evaluation traces."""
        config = self._config(inference="hermes-agent")
        with patch("evolution.agents.hermes_agent.HermesAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {
                "output": "test output",
                "messages": [],
                "completed": True,
            }
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)
            batch = [{"input": "task1", "answer": "expected1"}]
            adapter.evaluate(batch, {"artifact_body": "artifact"}, capture_traces=True)

            assert len(adapter.trajectories) == 1
            assert adapter.trajectories[0]["task_input"] == "task1"


class TestSkillFitnessMetricUnchanged:
    """Verify the DSPy metric function still works."""

    def test_returns_float(self):
        import dspy
        ex = dspy.Example(task_input="task", expected_behavior="expected output").with_inputs("task_input")
        pred = dspy.Prediction(output="some expected output text")
        score = skill_fitness_metric(ex, pred)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_empty_output(self):
        import dspy
        ex = dspy.Example(task_input="task", expected_behavior="expected").with_inputs("task_input")
        pred = dspy.Prediction(output="")
        score = skill_fitness_metric(ex, pred)
        assert score == 0.0


class TestEvolutionAdapterEdpAgent:
    """Tests for EvolutionAdapter wiring with edp-agent."""

    def test_adapter_wires_edp_agent(self):
        """inference_mode='edp-agent' creates EDPAgent instance."""
        config = EvolutionConfig(inference_mode="edp-agent")
        with patch("evolution.agents.edp_agent.EDPAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {"output": "ok", "messages": [], "completed": True}
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)

            assert adapter.agent is mock_agent
            mock_agent_cls.assert_called_once()

    def test_adapter_agent_run_signature(self):
        """adapter.agent.run returns dict with output/messages/completed."""
        config = EvolutionConfig(inference_mode="edp-agent")
        with patch("evolution.agents.edp_agent.EDPAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {
                "output": "test",
                "messages": [{"role": "user", "content": "hi"}],
                "completed": True,
            }
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)
            result = adapter.agent.run("artifact text", "task input", config)

        assert isinstance(result, dict)
        assert "output" in result
        assert "messages" in result
        assert "completed" in result
        assert result["output"] == "test"
        assert result["completed"] is True

    def test_adapter_evaluate_with_edp_agent(self):
        """evaluate() calls EDPAgent.run when inference_mode='edp-agent'."""
        config = EvolutionConfig(inference_mode="edp-agent", evaluator="fast")

        with patch("evolution.agents.edp_agent.EDPAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = {
                "output": "test output",
                "messages": [],
                "completed": True,
            }
            mock_agent_cls.return_value = mock_agent

            adapter = EvolutionAdapter(config)
            batch = [
                {"input": "task1", "answer": "expected1"},
                {"input": "task2", "answer": "expected2"},
            ]
            result = adapter.evaluate(batch, {"artifact_body": "artifact text"}, capture_traces=False)

            assert len(result.scores) == 2
            assert mock_agent.run.call_count == 2
            mock_agent.run.assert_called_with("artifact text", "task2", config)

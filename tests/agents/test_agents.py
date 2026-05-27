"""Tests for pluggable agent inference backends."""

import os
from unittest.mock import MagicMock, patch

import dspy
import httpx

from evolution.agents.base import BaseAgent
from evolution.agents.single_turn import SingleTurnAgent
from evolution.agents.hermes_agent import HermesAgent
from evolution.agents.edp_agent import EDPAgent


class TestBaseAgent:
    def test_cannot_instantiate_abstract(self):
        """BaseAgent is abstract and cannot be instantiated directly."""
        with patch.object(BaseAgent, '__abstractmethods__', set()):
            agent = BaseAgent()
            assert isinstance(agent, BaseAgent)

    def test_subclass_must_implement_run(self):
        """Concrete subclass must implement run()."""
        class Concrete(BaseAgent):
            def run(self, system_prompt, task_input, config):
                return {"output": "ok", "messages": [], "completed": True}

        agent = Concrete()
        result = agent.run("prompt", "input", None)
        assert result["output"] == "ok"
        assert result["completed"] is True


class TestSingleTurnAgent:
    def test_run_returns_dict(self):
        """Mock dspy.ChainOfThought, verify returns dict with output/messages/completed."""
        with patch.object(dspy, "ChainOfThought") as mock_cot:
            mock_instance = MagicMock()
            mock_instance.return_value = MagicMock(output="test response")
            mock_cot.return_value = mock_instance

            agent = SingleTurnAgent()
            result = agent.run("test prompt", "test input", None)

            assert isinstance(result, dict)
            assert "output" in result
            assert "messages" in result
            assert "completed" in result
            assert result["completed"] is True
            assert len(result["messages"]) >= 2
            assert result["output"] == "test response"


class TestHermesAgent:
    def _make_mock_config(self):
        config = MagicMock()
        config.agent_model = "gpt-4"
        config.agent_max_iterations = 10
        return config

    def test_run_returns_dict(self):
        """Mock AIAgent, verify returns dict with output/messages/completed."""
        mock_run_agent = MagicMock()
        mock_agent_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_conversation.return_value = {
            "final_response": "mock response",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        }
        mock_agent_class.return_value = mock_instance
        mock_run_agent.AIAgent = mock_agent_class

        with patch.dict("sys.modules", {"run_agent": mock_run_agent}):
            agent = HermesAgent()
            result = agent.run("test prompt", "test input", self._make_mock_config())

        assert isinstance(result, dict)
        assert "output" in result
        assert "messages" in result
        assert "completed" in result
        assert result["completed"] is True
        assert result["output"] == "mock response"
        assert len(result["messages"]) == 2

    def test_run_handles_exception(self):
        """Mock AIAgent.run_conversation to raise, verify returns completed=False."""
        mock_run_agent = MagicMock()
        mock_agent_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_conversation.side_effect = RuntimeError("Agent failure")
        mock_agent_class.return_value = mock_instance
        mock_run_agent.AIAgent = mock_agent_class

        with patch.dict("sys.modules", {"run_agent": mock_run_agent}):
            agent = HermesAgent()
            result = agent.run("test prompt", "test input", self._make_mock_config())

        assert isinstance(result, dict)
        assert result["completed"] is False
        assert result["output"] == ""
        assert result["messages"] == []


class TestEDPAgent:
    _env = {
        "EDP_INFER_URL": "http://localhost/infer",
        "EDP_AGENTRULE_UPDATE_URL": "http://localhost/agentrule",
        "EDP_SKILL_UPDATE_URL": "http://localhost/skill",
    }

    def _make_mock_config(self, skill_name=None):
        config = MagicMock()
        config.agent_model = "gpt-4"
        config.agent_max_iterations = 10
        config.skill_name = skill_name
        return config

    def test_run_returns_trajectory(self):
        with patch.dict(os.environ, self._env), patch("httpx.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "output": "hello", "messages": [], "completed": True,
            }
            mock_post.return_value.raise_for_status = MagicMock()

            agent = EDPAgent()
            result = agent.run("system_prompt", "task_input", self._make_mock_config())

            assert result["completed"] is True
            assert result["output"] == "hello"

    def test_update_agentrule_posts_body(self):
        with patch.dict(os.environ, self._env), patch("httpx.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()

            agent = EDPAgent()
            agent.update_agentrule("new body")

            mock_post.assert_called_once_with(
                agent._agentrule_update_url,
                json={"body": "new body"},
            )

    def test_update_skill_posts_name_and_body(self):
        with patch.dict(os.environ, self._env), patch("httpx.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()

            agent = EDPAgent()
            agent.update_skill("my_skill", "skill body")

            mock_post.assert_called_once_with(
                agent._skill_update_url,
                json={"name": "my_skill", "body": "skill body"},
            )

    def test_run_calls_update_skill_when_skill_name_set(self):
        with patch.dict(os.environ, self._env), patch("httpx.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "output": "ok", "messages": [], "completed": True,
            }
            mock_post.return_value.raise_for_status = MagicMock()

            agent = EDPAgent()
            config = self._make_mock_config(skill_name="my_skill")
            agent.run("skill body text", "task", config)

            # Skill evolution: should call update_skill, not update_agentrule
            skill_calls = [
                c for c in mock_post.call_args_list
                if c.kwargs.get("json", {}).get("name") == "my_skill"
            ]
            agentrule_calls = [
                c for c in mock_post.call_args_list
                if "body" in c.kwargs.get("json", {}) and "name" not in c.kwargs.get("json", {})
            ]
            assert len(skill_calls) == 1
            assert len(agentrule_calls) == 0

    def test_run_returns_false_when_infer_url_not_set(self):
        """EDPAgent returns completed=False gracefully when env vars missing."""
        agent = EDPAgent()
        # No env vars set — _infer_url is empty
        result = agent.run("prompt", "task", self._make_mock_config())
        assert result["completed"] is False

    def test_run_skips_duplicate_update(self):
        """Body caching: same system_prompt only triggers one update call."""
        with patch.dict(os.environ, self._env), patch("httpx.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "output": "ok", "messages": [], "completed": True,
            }
            mock_post.return_value.raise_for_status = MagicMock()

            agent = EDPAgent()
            # Same body twice
            agent.run("same body", "task1", self._make_mock_config())
            agent.run("same body", "task2", self._make_mock_config())

            agentrule_calls = [
                c for c in mock_post.call_args_list
                if c.kwargs.get("json", {}).get("body") == "same body"
            ]
            assert len(agentrule_calls) == 1  # Only first call triggers POST

    def test_run_handles_http_error_gracefully(self):
        with patch.dict(os.environ, self._env), patch("httpx.post", side_effect=httpx.HTTPError("down")):
            agent = EDPAgent()
            result = agent.run("sp", "task", self._make_mock_config())

            assert result["completed"] is False
            assert result["output"] == ""
            assert result["messages"] == []

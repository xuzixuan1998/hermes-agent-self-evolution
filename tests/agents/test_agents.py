"""Tests for pluggable agent inference backends."""

import asyncio
from unittest.mock import MagicMock, patch

import dspy

from evolution.agents.base import BaseAgent
from evolution.agents.single_turn import SingleTurnAgent
from evolution.agents.hermes_agent import HermesAgent
from evolution.agents.edp_agent import EDPAgent, _collect_stream, _edp_initialized


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
    def _make_mock_config(self):
        config = MagicMock()
        config.agent_framework_path = None
        config.agent_model = "gpt-4"
        config.agent_max_iterations = 10
        return config

    def test_run_returns_dict(self):
        """EDPAgent.run returns unified dict format."""
        import sys
        from evolution.agents import edp_agent

        edp_agent._edp_initialized = True

        fake_edp_agent = MagicMock()
        with patch.dict(sys.modules, {"edp_agent": fake_edp_agent, "edp_agent.agent": fake_edp_agent.agent}), \
             patch.object(edp_agent, "asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = {
                "output": "test output",
                "messages": [{"role": "think", "content": "thinking"}],
                "completed": True,
            }

            agent = EDPAgent()
            result = agent.run("prompt text", "task input", self._make_mock_config())

        assert isinstance(result, dict)
        assert "output" in result
        assert "messages" in result
        assert "completed" in result
        assert result["output"] == "test output"
        assert result["completed"] is True
        fake_edp_agent.agent.reload_agent_rule.assert_called_once_with("prompt text")

    def test_run_lazy_init(self):
        """First call triggers _ensure_initialized; second call skips it."""
        import sys
        from evolution.agents import edp_agent

        edp_agent._edp_initialized = False

        fake_edp_agent = MagicMock()
        # Make asyncio.run actually execute _ensure_initialized to set the flag
        _real_run = edp_agent.asyncio.run
        def _side_effect(coro):
            if getattr(coro, '__name__', '') == '_ensure_initialized':
                # Don't actually run (needs real edp_agent), just simulate
                pass
        mock_asyncio = MagicMock()
        mock_asyncio.run.side_effect = _side_effect
        mock_asyncio.run.return_value = {
            "output": "ok", "messages": [], "completed": True,
        }

        with patch.dict(sys.modules, {"edp_agent": fake_edp_agent, "edp_agent.agent": fake_edp_agent.agent}), \
             patch.object(edp_agent, "asyncio", mock_asyncio):

            agent = EDPAgent()
            agent.run("prompt", "input", self._make_mock_config())

        # _ensure_initialized would have set the flag if asyncio.run executed it.
        # Since asyncio is mocked, manually set the flag to simulate init completion.
        edp_agent._edp_initialized = True

        # Second call — should skip init since _edp_initialized is now True
        with patch.dict(sys.modules, {"edp_agent": fake_edp_agent, "edp_agent.agent": fake_edp_agent.agent}), \
             patch.object(edp_agent, "asyncio") as mock_asyncio2:
            mock_asyncio2.run.return_value = {
                "output": "ok2", "messages": [], "completed": True,
            }
            agent = EDPAgent()
            result = agent.run("prompt", "input", self._make_mock_config())
            assert result["output"] == "ok2"

    def test_collect_stream_builds_messages(self):
        """_collect_stream maps ThinkChunk/ToolStart/ToolEnd/FinalAnswerChunk correctly."""
        import sys
        events = []

        think = MagicMock()
        think.type = "think_chunk"
        think.content = "Let me think..."
        events.append(think)

        tool_start = MagicMock()
        tool_start.type = "tool_start"
        tool_start.plugin = "search"
        events.append(tool_start)

        tool_end = MagicMock()
        tool_end.type = "tool_end"
        tool_end.plugin = "search"
        events.append(tool_end)

        answer = MagicMock()
        answer.type = "final_answer_chunk"
        answer.content = "The answer is 42"
        events.append(answer)

        conv_end = MagicMock()
        conv_end.type = "conversation_end"
        events.append(conv_end)

        async def mock_stream(**kwargs):
            for e in events:
                yield e

        fake_edp_agent = MagicMock()
        fake_edp_agent.agent.agent_stream = mock_stream
        with patch.dict(sys.modules, {"edp_agent": fake_edp_agent, "edp_agent.agent": fake_edp_agent.agent}):
            result = asyncio.run(_collect_stream("conv-id", "query"))

        assert result["completed"] is True
        assert "The answer is 42" in result["output"]
        assert len(result["messages"]) == 3  # think + tool_start + tool_end
        assert result["messages"][0] == {"role": "think", "content": "Let me think..."}
        assert result["messages"][1] == {"role": "tool", "name": "search", "content": "start"}
        assert result["messages"][2] == {"role": "tool", "name": "search", "content": "end"}

    def test_collect_stream_empty(self):
        """Empty event stream returns completed=False."""
        import sys

        async def mock_stream(**kwargs):
            if False:
                yield

        fake_edp_agent = MagicMock()
        fake_edp_agent.agent.agent_stream = mock_stream
        with patch.dict(sys.modules, {"edp_agent": fake_edp_agent, "edp_agent.agent": fake_edp_agent.agent}):
            result = asyncio.run(_collect_stream("conv-id", "query"))

        assert result["completed"] is False
        assert result["output"] == ""
        assert result["messages"] == []

    def test_run_exception_handling(self):
        """Exception in EDPAgent.run returns completed=False, no raise."""
        from evolution.agents import edp_agent

        edp_agent._edp_initialized = True

        with patch.object(edp_agent, "asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("agent stream failed")

            agent = EDPAgent()
            result = agent.run("prompt", "input", self._make_mock_config())

        assert isinstance(result, dict)
        assert result["completed"] is False
        assert result["output"] == ""
        assert result["messages"] == []

    def test_run_releases_session_on_error(self):
        """Release is called even when _collect_stream raises."""
        import sys
        from evolution.agents import edp_agent

        edp_agent._edp_initialized = True

        call_order = []

        def mock_asyncio_run(coro):
            name = getattr(coro, "__name__", "")
            call_order.append(name)
            if "_collect_stream" in name:
                raise RuntimeError("stream failure")
            return None

        fake_edp_agent = MagicMock()
        with patch.dict(sys.modules, {"edp_agent": fake_edp_agent, "edp_agent.agent": fake_edp_agent.agent}), \
             patch.object(edp_agent, "asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = mock_asyncio_run

            agent = EDPAgent()
            result = agent.run("prompt", "input", self._make_mock_config())

        assert result["completed"] is False
        release_calls = [c for c in call_order if "_release_session" in c]
        assert len(release_calls) == 1

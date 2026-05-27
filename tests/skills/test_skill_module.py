"""Tests for skill module loading and parsing."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import dspy

from evolution.skills.skill_module import (
    load_skill,
    reassemble_skill,
    run_edp_agent,
    run_hermes_agent,
    run_single_turn,
)


SAMPLE_SKILL = """---
name: test-skill
description: A skill for testing things
version: 1.0.0
metadata:
  hermes:
    tags: [testing]
---

# Test Skill — Testing Things

## When to Use
Use this when you need to test things.

## Procedure
1. First, do the thing
2. Then, verify it worked
3. Report results

## Pitfalls
- Don't forget to check edge cases
"""


class TestLoadSkill:
    def test_parses_frontmatter(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(SAMPLE_SKILL)
        skill = load_skill(skill_file)

        assert skill["name"] == "test-skill"
        assert skill["description"] == "A skill for testing things"
        assert "version: 1.0.0" in skill["frontmatter"]

    def test_parses_body(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(SAMPLE_SKILL)
        skill = load_skill(skill_file)

        assert "# Test Skill" in skill["body"]
        assert "## Procedure" in skill["body"]
        assert "Don't forget" in skill["body"]

    def test_raw_contains_everything(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(SAMPLE_SKILL)
        skill = load_skill(skill_file)

        assert skill["raw"] == SAMPLE_SKILL

    def test_path_is_stored(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(SAMPLE_SKILL)
        skill = load_skill(skill_file)

        assert skill["path"] == skill_file


class TestReassembleSkill:
    def test_roundtrip(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(SAMPLE_SKILL)
        skill = load_skill(skill_file)

        reassembled = reassemble_skill(skill["frontmatter"], skill["body"])
        assert "---" in reassembled
        assert "name: test-skill" in reassembled
        assert "# Test Skill" in reassembled

    def test_preserves_frontmatter(self):
        frontmatter = "name: my-skill\ndescription: Does stuff"
        body = "# My Skill\nDo the thing."
        result = reassemble_skill(frontmatter, body)

        assert result.startswith("---\n")
        assert "name: my-skill" in result
        assert "# My Skill" in result

    def test_evolved_body_replaces_original(self):
        frontmatter = "name: my-skill\ndescription: Does stuff"
        evolved_body = "# EVOLVED\nNew and improved procedure."
        result = reassemble_skill(frontmatter, evolved_body)

        assert "EVOLVED" in result
        assert "New and improved" in result


class TestRunSingleTurn:
    """Tests for run_single_turn."""

    def test_run_single_turn_returns_dict(self):
        """Mock dspy.ChainOfThought, verify returns dict with output/messages/completed."""
        with patch.object(dspy, "ChainOfThought") as mock_cot:
            mock_instance = MagicMock()
            mock_instance.return_value = MagicMock(output="test response")
            mock_cot.return_value = mock_instance

            result = run_single_turn("test skill", "test input", None)

            assert isinstance(result, dict)
            assert "output" in result
            assert "messages" in result
            assert "completed" in result
            assert result["completed"] is True
            assert len(result["messages"]) >= 2
            assert result["output"] == "test response"


class TestRunHermesAgent:
    """Tests for run_hermes_agent."""

    def _make_mock_config(self):
        config = MagicMock()
        config.agent_model = "gpt-4"
        config.agent_max_iterations = 10
        return config

    def test_run_hermes_agent_returns_dict(self):
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
            result = run_hermes_agent(
                "test skill", "test input", self._make_mock_config()
            )

        assert isinstance(result, dict)
        assert "output" in result
        assert "messages" in result
        assert "completed" in result
        assert result["completed"] is True
        assert result["output"] == "mock response"
        assert len(result["messages"]) == 2

    def test_run_hermes_agent_handles_exception(self):
        """Mock AIAgent.run_conversation to raise, verify returns completed=False."""
        mock_run_agent = MagicMock()
        mock_agent_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.run_conversation.side_effect = RuntimeError("Agent failure")
        mock_agent_class.return_value = mock_instance
        mock_run_agent.AIAgent = mock_agent_class

        with patch.dict("sys.modules", {"run_agent": mock_run_agent}):
            result = run_hermes_agent(
                "test skill", "test input", self._make_mock_config()
            )

        assert isinstance(result, dict)
        assert result["completed"] is False
        assert result["output"] == ""
        assert result["messages"] == []


class TestRunEdpAgent:
    """Tests for run_edp_agent and its helper functions."""

    def _make_mock_config(self):
        config = MagicMock()
        config.agent_framework_path = None
        config.agent_model = "gpt-4"
        config.agent_max_iterations = 10
        return config

    def test_run_edp_agent_returns_dict(self):
        """run_edp_agent returns unified dict format."""
        from evolution.skills import skill_module

        skill_module._edp_initialized = True

        with patch.object(skill_module, "asyncio") as mock_asyncio, \
             patch("edp_agent.agent.reload_agent_rule") as mock_reload:
            mock_asyncio.run.return_value = {
                "output": "test output",
                "messages": [{"role": "think", "content": "thinking"}],
                "completed": True,
            }

            result = run_edp_agent("skill text", "task input", self._make_mock_config())

        assert isinstance(result, dict)
        assert "output" in result
        assert "messages" in result
        assert "completed" in result
        assert result["output"] == "test output"
        assert result["completed"] is True
        mock_reload.assert_called_once_with("skill text")

    def test_run_edp_agent_lazy_init(self):
        """First call triggers _ensure_initialized; second call skips it."""
        from evolution.skills import skill_module

        skill_module._edp_initialized = False

        with patch.object(skill_module, "asyncio") as mock_asyncio, \
             patch("edp_agent.agent.reload_agent_rule"), \
             patch("edp_agent.agent.initialize_dpa") as mock_init_dpa:

            mock_asyncio.run.return_value = {
                "output": "ok", "messages": [], "completed": True,
            }

            # First call — _edp_initialized=False
            run_edp_agent("skill", "input", self._make_mock_config())

        # After first call, _edp_initialized should be True
        assert skill_module._edp_initialized is True

        # Second call — should skip init
        skill_module._edp_initialized = True
        with patch.object(skill_module, "asyncio") as mock_asyncio2, \
             patch("edp_agent.agent.reload_agent_rule"):
            mock_asyncio2.run.return_value = {
                "output": "ok2", "messages": [], "completed": True,
            }
            result = run_edp_agent("skill", "input", self._make_mock_config())
            assert result["output"] == "ok2"

    def test_collect_stream_builds_messages(self):
        """_collect_stream maps ThinkChunk/ToolStart/ToolEnd/FinalAnswerChunk correctly."""
        from evolution.skills.skill_module import _collect_stream

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

        with patch("evolution.skills.skill_module.agent_stream", mock_stream):
            result = asyncio.run(_collect_stream("conv-id", "query"))

        assert result["completed"] is True
        assert "The answer is 42" in result["output"]
        assert len(result["messages"]) == 3  # think + tool_start + tool_end
        assert result["messages"][0] == {"role": "think", "content": "Let me think..."}
        assert result["messages"][1] == {"role": "tool", "name": "search", "content": "start"}
        assert result["messages"][2] == {"role": "tool", "name": "search", "content": "end"}

    def test_collect_stream_empty(self):
        """Empty event stream returns completed=False."""
        from evolution.skills.skill_module import _collect_stream

        async def mock_stream(**kwargs):
            if False:
                yield  # noqa: unreachable

        with patch("evolution.skills.skill_module.agent_stream", mock_stream):
            result = asyncio.run(_collect_stream("conv-id", "query"))

        assert result["completed"] is False
        assert result["output"] == ""
        assert result["messages"] == []

    def test_run_edp_agent_exception_handling(self):
        """Exception in run_edp_agent returns completed=False, no raise."""
        from evolution.skills import skill_module

        skill_module._edp_initialized = True

        with patch.object(skill_module, "asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("agent stream failed")

            result = run_edp_agent("skill", "input", self._make_mock_config())

        assert isinstance(result, dict)
        assert result["completed"] is False
        assert result["output"] == ""
        assert result["messages"] == []

    def test_run_edp_agent_releases_session_on_error(self):
        """Release is called even when _collect_stream raises."""
        from evolution.skills import skill_module

        skill_module._edp_initialized = True

        call_order = []

        def mock_asyncio_run(coro):
            name = getattr(coro, "__name__", "")
            call_order.append(name)
            if "_collect_stream" in name:
                raise RuntimeError("stream failure")
            return None

        with patch.object(skill_module, "asyncio") as mock_asyncio, \
             patch("edp_agent.agent.reload_agent_rule"):
            mock_asyncio.run.side_effect = mock_asyncio_run

            result = run_edp_agent("skill", "input", self._make_mock_config())

        assert result["completed"] is False
        # _release_session should have been called after _collect_stream failed
        release_calls = [c for c in call_order if "_release_session" in c]
        assert len(release_calls) == 1

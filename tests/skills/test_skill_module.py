"""Tests for skill module loading and parsing."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import dspy

from evolution.skills.skill_module import (
    load_skill,
    reassemble_skill,
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

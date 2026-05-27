"""prompt.py and AgentRule.md must not reference legacy todolist tools after Phase 1.

Both feed into the system prompt — prompt.py via build_system_prompt(),
AgentRule.md via the markdown body that agent_rule.load injects.
"""
from __future__ import annotations

from pathlib import Path


_LEGACY_TOOL_NAMES = ("todolist_create", "todolist_modify", "todolist_query")


def test_prompt_does_not_mention_legacy_todolist_tools():
    from EDPAgent.prompt import build_system_prompt

    prompt = build_system_prompt()
    for legacy in _LEGACY_TOOL_NAMES:
        assert legacy not in prompt, f"legacy {legacy!r} still in prompt.py"


def test_prompt_does_not_have_64_section():
    """Section 6.3/6.4 (任务规划/Todolist) should be deleted entirely."""
    from EDPAgent.prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "### 6.3 任务规划" not in prompt
    assert "### 6.4 任务规划" not in prompt
    assert "任务规划（Todolist）" not in prompt


def test_agent_rule_md_does_not_mention_legacy_todolist_tools():
    """AgentRule.md is injected into the system prompt — must not instruct LLM
    to call todolist_create / todolist_modify (deleted in Phase 1)."""
    agent_rule_path = Path(__file__).resolve().parent.parent / "AgentRule.md"
    text = agent_rule_path.read_text(encoding="utf-8")
    for legacy in _LEGACY_TOOL_NAMES:
        assert legacy not in text, f"legacy {legacy!r} still in AgentRule.md"

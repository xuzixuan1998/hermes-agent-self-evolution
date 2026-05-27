"""TOOLS list registration: lite_todo replaces legacy 3-tool todolist."""
from __future__ import annotations


def test_lite_todo_tools_returns_single_tool():
    from EDPAgent.tool.lite_todo import lite_todo_tools

    tools = lite_todo_tools()
    assert len(tools) == 1
    assert tools[0].card.name == "lite_todo_write"


def test_TOOLS_contains_lite_todo_write():
    from EDPAgent.tool import TOOLS

    names = {t.card.name for t in TOOLS}
    assert "lite_todo_write" in names


def test_TOOLS_does_not_contain_legacy_todolist_tools():
    """Legacy 3 tools (todolist_create / todolist_modify / todolist_query)
    must be gone from registration after Phase 1.
    """
    from EDPAgent.tool import TOOLS

    names = {t.card.name for t in TOOLS}
    legacy_names = {"todolist_create", "todolist_modify", "todolist_query"}
    assert names.isdisjoint(legacy_names), (
        f"Legacy todolist tools still in TOOLS: {names & legacy_names}"
    )


def test_TOOLS_keeps_business_tools():
    """ask_user / call_mcp / call_versatile must still be registered."""
    from EDPAgent.tool import TOOLS

    names = {t.card.name for t in TOOLS}
    assert "ask_user" in names
    assert "call_mcp" in names
    assert "call_versatile" in names

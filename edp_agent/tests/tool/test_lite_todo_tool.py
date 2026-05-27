"""LiteTodoWriteTool — step_id-based schema, overwrite-style, all-terminal auto-clear."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_invoke_with_new_list_persists(fake_session):
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    tool = LiteTodoWriteTool()
    inputs = {
        "todos": [
            {"step_id": 1, "status": "pending"},
            {"step_id": 4, "status": "pending"},
        ]
    }
    result = await tool.invoke(inputs, session=fake_session)

    assert result.success is True
    persisted = fake_session.get_state(LiteTodoManager.STATE_KEY)
    # manager 持久化的是模型字段（step_id + status），不带 content
    assert persisted == [
        {"step_id": 1, "status": "pending"},
        {"step_id": 4, "status": "pending"},
    ]


@pytest.mark.asyncio
async def test_invoke_returns_todos_in_data_with_resolved_content(fake_session):
    """tool 返回的 data.todos 包含 step_id + status + 已解析的 content（便于下游不必再 lookup）。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    inputs = {"todos": [{"step_id": 1, "status": "pending"}]}
    result = await tool.invoke(inputs, session=fake_session)

    assert result.data["todos"] == [
        {"step_id": 1, "status": "pending", "content": "推荐理财产品"}
    ]
    assert result.data["count"] == 1


@pytest.mark.asyncio
async def test_second_invoke_overwrites_first(fake_session):
    """Each call replaces the entire list (overwrite-style)."""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    tool = LiteTodoWriteTool()
    await tool.invoke(
        {"todos": [{"step_id": 1, "status": "pending"}, {"step_id": 2, "status": "pending"}]},
        session=fake_session,
    )
    await tool.invoke(
        {"todos": [{"step_id": 3, "status": "pending"}]},
        session=fake_session,
    )

    assert fake_session.get_state(LiteTodoManager.STATE_KEY) == [
        {"step_id": 3, "status": "pending"}
    ]


@pytest.mark.asyncio
async def test_all_done_clears_state(fake_session):
    """All status=done → state 置空。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    tool = LiteTodoWriteTool()
    await tool.invoke(
        {"todos": [{"step_id": 1, "status": "done"}, {"step_id": 2, "status": "done"}]},
        session=fake_session,
    )

    assert fake_session.get_state(LiteTodoManager.STATE_KEY) == []


@pytest.mark.asyncio
async def test_invoke_rejects_skip_optional_status(fake_session):
    """v2 schema：skip_optional 已下线——不打算做的步骤直接不放进 todos 即可。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    result = await tool.invoke(
        {"todos": [{"step_id": 2, "status": "skip_optional"}]},
        session=fake_session,
    )

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_partial_done_does_not_clear(fake_session):
    """有 pending 项 → 不清空。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    tool = LiteTodoWriteTool()
    await tool.invoke(
        {"todos": [{"step_id": 1, "status": "done"}, {"step_id": 2, "status": "pending"}]},
        session=fake_session,
    )

    assert fake_session.get_state(LiteTodoManager.STATE_KEY) == [
        {"step_id": 1, "status": "done"},
        {"step_id": 2, "status": "pending"},
    ]


@pytest.mark.asyncio
async def test_invoke_rejects_in_progress_status(fake_session):
    """v2 schema：禁用 in_progress（"运行中"由 todo_status 单独承载）。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    result = await tool.invoke(
        {"todos": [{"step_id": 1, "status": "in_progress"}]},
        session=fake_session,
    )

    assert result.success is False
    assert result.error is not None
    assert "status" in result.error.lower() or "schema" in result.error.lower()


@pytest.mark.asyncio
async def test_invoke_rejects_step_id_out_of_range(fake_session):
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    result = await tool.invoke(
        {"todos": [{"step_id": 99, "status": "pending"}]},
        session=fake_session,
    )

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_invoke_rejects_duplicate_step_ids(fake_session):
    """每个 step_id 在 todos 里只能出现一次。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    result = await tool.invoke(
        {"todos": [
            {"step_id": 1, "status": "pending"},
            {"step_id": 1, "status": "pending"},
        ]},
        session=fake_session,
    )

    assert result.success is False
    assert "duplicate" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_invoke_rejects_legacy_content_field(fake_session):
    """v2 不再接受 content 字段（schema extra="forbid"）；LLM 错传应被拒。"""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    result = await tool.invoke(
        {"todos": [{"content": "推荐理财产品", "status": "pending"}]},
        session=fake_session,
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_invoke_without_session_fails():
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool

    tool = LiteTodoWriteTool()
    result = await tool.invoke({"todos": []}, session=None)
    assert result.success is False


@pytest.mark.asyncio
async def test_empty_todos_clears_state(fake_session):
    """Empty list = clear."""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    tool = LiteTodoWriteTool()
    # First populate
    await tool.invoke(
        {"todos": [{"step_id": 1, "status": "pending"}]},
        session=fake_session,
    )
    # Then empty
    await tool.invoke({"todos": []}, session=fake_session)

    assert fake_session.get_state(LiteTodoManager.STATE_KEY) == []

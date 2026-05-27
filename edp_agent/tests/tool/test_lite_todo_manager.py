"""LiteTodoManager — load / save / clear with Session State key isolation."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_load_returns_empty_when_no_state(fake_session):
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    manager = LiteTodoManager()
    todos = await manager.load(fake_session)
    assert todos == []


@pytest.mark.asyncio
async def test_save_persists_to_session_state_under_lite_key(fake_session):
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager
    from EDPAgent.tool.lite_todo.models import TodoItem, TodoStatus

    manager = LiteTodoManager()
    todos = [
        TodoItem(step_id=1, status=TodoStatus.PENDING),
        TodoItem(step_id=3, status=TodoStatus.PENDING),
    ]
    await manager.save(fake_session, todos)

    raw = fake_session.get_state("lite_todolist")
    assert raw is not None
    assert len(raw) == 2
    # JSON-serialized dicts (Redis compatible) — only step_id + status; no content
    assert raw[0] == {"step_id": 1, "status": "pending"}
    assert raw[1] == {"step_id": 3, "status": "pending"}


@pytest.mark.asyncio
async def test_save_then_load_roundtrip(fake_session):
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager
    from EDPAgent.tool.lite_todo.models import TodoItem, TodoStatus

    manager = LiteTodoManager()
    original = [TodoItem(step_id=4, status=TodoStatus.DONE)]
    await manager.save(fake_session, original)

    reloaded = await manager.load(fake_session)
    assert len(reloaded) == 1
    assert reloaded[0].step_id == 4
    assert reloaded[0].content == "查询理财账户余额，如果资金不足进行资金筹划，并购买理财产品"
    assert reloaded[0].status == TodoStatus.DONE


@pytest.mark.asyncio
async def test_clear_empties_state(fake_session):
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager
    from EDPAgent.tool.lite_todo.models import TodoItem, TodoStatus

    manager = LiteTodoManager()
    await manager.save(fake_session, [TodoItem(step_id=1, status=TodoStatus.DONE)])
    await manager.clear(fake_session)

    assert await manager.load(fake_session) == []


@pytest.mark.asyncio
async def test_state_key_isolation_from_legacy_todolist(fake_session):
    """lite_todolist key must not touch legacy 'todolist' key."""
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager
    from EDPAgent.tool.lite_todo.models import TodoItem, TodoStatus

    # Pre-seed legacy state to verify isolation
    fake_session.update_state({"todolist": [{"index": 1, "content": "legacy", "status": "pending"}]})

    manager = LiteTodoManager()
    await manager.save(fake_session, [TodoItem(step_id=1, status=TodoStatus.PENDING)])

    # Legacy untouched
    assert fake_session.get_state("todolist") == [{"index": 1, "content": "legacy", "status": "pending"}]
    # Lite written under its own key (new step_id schema)
    assert fake_session.get_state("lite_todolist") == [{"step_id": 1, "status": "pending"}]


@pytest.mark.asyncio
async def test_state_key_constant_is_lite_todolist():
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    assert LiteTodoManager.STATE_KEY == "lite_todolist"

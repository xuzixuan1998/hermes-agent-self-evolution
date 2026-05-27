"""Stream processor派生测试 + 话术契约测试.

Verify _parse_lite_todo_tool_result emits the right TodoList* event sequence
with content text matching docs/prd/talking_points.md L11 format.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# ── Helpers ─────────────────────────────────────────────────────────────────
def _make_tool_end_event(plugin: str, data: dict, content: str = ""):
    """Mimic raw runner event with type='tool_end'."""
    return SimpleNamespace(
        type="tool_end",
        payload={"plugin": plugin, "data": data, "content": content},
    )


def _scripts(todolist_start="开始规划todolist", todolist_end="todolist规划完成"):
    return SimpleNamespace(
        todolist_start=todolist_start,
        todolist_end=todolist_end,
    )


# ── Tests ───────────────────────────────────────────────────────────────────
def test_parse_emits_start_n_items_end_in_order():
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={
            "todos": [
                {"step_id": 1, "status": "pending"},
                {"step_id": 4, "status": "pending"},
            ],
            "count": 2,
        },
    )
    events = proc.process(raw)

    # First the ToolEndEvent itself, then TodoListStart/Items/End
    types = [getattr(e, "type", None) for e in events]
    assert types == ["tool_end", "todolist_start", "todolist_item", "todolist_item", "todolist_end"]


def test_item_content_matches_talking_points_format():
    """talking_points L11: '1.推荐理财产品（待执行）<br/>'。
    visible 编号用 1-based 数组下标（连续编号，避免跳号）；
    canonical step_id 在 item.id 里给后端用。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={
            "todos": [
                {"step_id": 1, "status": "pending"},
                {"step_id": 3, "status": "pending"},
                {"step_id": 4, "status": "done"},
            ],
        },
    )
    events = proc.process(raw)

    items = [e for e in events if getattr(e, "type", None) == "todolist_item"]
    assert len(items) == 3
    # 视觉编号是 1, 2, 3（连续），不是 step_id 1, 3, 4
    assert items[0].content == "1.推荐理财产品（待执行）<br/>"
    assert items[1].content == "2.确定购买产品和金额（待执行）<br/>"
    assert items[2].content == "3.查询理财账户余额，如果资金不足进行资金筹划，并购买理财产品（完成）<br/>"
    # item.id 仍是 canonical step_id（不是 visible 编号）
    assert items[0].id == 1
    assert items[1].id == 3
    assert items[2].id == 4


def test_item_id_is_step_id():
    """v2: item.id 直接是 step_id（不再是数组下标 1-based 位置）。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [
            {"step_id": 3, "status": "pending"},
            {"step_id": 1, "status": "pending"},
        ]},
    )
    events = proc.process(raw)
    items = [e for e in events if getattr(e, "type", None) == "todolist_item"]
    assert items[0].id == 3
    assert items[1].id == 1


def test_item_title_is_canonical_step_name():
    """title 直接是 CANONICAL_STEPS[step_id]，框架反查得出。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [{"step_id": 1, "status": "pending"}]},
    )
    events = proc.process(raw)
    item = [e for e in events if getattr(e, "type", None) == "todolist_item"][0]
    assert item.title == "推荐理财产品"


def test_item_status_passes_pending_done_through():
    """status 字段直传 pending / done（in_progress 与 skip_optional 都已下线）。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [
            {"step_id": 1, "status": "done"},
            {"step_id": 3, "status": "pending"},
        ]},
    )
    events = proc.process(raw)
    items = [e for e in events if getattr(e, "type", None) == "todolist_item"]
    assert items[0].status == "done"
    assert items[1].status == "pending"
    assert "（完成）" in items[0].content
    assert "（待执行）" in items[1].content


def test_todolist_start_uses_scripts_config():
    """TodoListStartEvent.content from injected ScriptsConfig.todolist_start."""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts(todolist_start="开始规划todolist"))
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [{"step_id": 1, "status": "pending"}]},
    )
    events = proc.process(raw)
    start = [e for e in events if getattr(e, "type", None) == "todolist_start"][0]
    assert start.content == "开始规划todolist"


def test_todolist_end_uses_scripts_config_with_count():
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts(todolist_end="todolist规划完成"))
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [
            {"step_id": 1, "status": "pending"},
            {"step_id": 4, "status": "pending"},
        ]},
    )
    events = proc.process(raw)
    end = [e for e in events if getattr(e, "type", None) == "todolist_end"][0]
    assert end.content == "todolist规划完成"
    assert end.count == 2


def test_falls_back_to_default_text_when_scripts_none():
    """Without ScriptsConfig, fallback to built-in defaults — no crash."""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=None)
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [{"step_id": 1, "status": "pending"}]},
    )
    events = proc.process(raw)
    start = [e for e in events if getattr(e, "type", None) == "todolist_start"][0]
    end = [e for e in events if getattr(e, "type", None) == "todolist_end"][0]
    assert start.content == "已生成任务规划"
    assert end.content == "任务规划完成"


def test_empty_todos_emits_only_tool_end():
    """All-done auto-clear case: tool_data.todos == [] → no TodoList* events."""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": [], "count": 0},
    )
    events = proc.process(raw)
    types = [getattr(e, "type", None) for e in events]
    assert types == ["tool_end"]  # no TodoList* derivation when list is empty


def test_other_plugin_does_not_trigger_lite_parse():
    """call_versatile / call_mcp tool_end must NOT trigger lite_todo derivation."""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="call_versatile",
        data={"todos": [{"step_id": 1, "status": "pending"}]},
    )
    events = proc.process(raw)
    types = [getattr(e, "type", None) for e in events]
    assert "todolist_start" not in types
    assert "todolist_item" not in types
    assert "todolist_end" not in types


def test_legacy_plugin_does_not_trigger_anything():
    """Old todolist_create plugin name (legacy) is not handled at all anymore."""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="todolist_create",
        data={"tasks": [{"index": 1, "content": "x", "status": "pending"}]},
    )
    events = proc.process(raw)
    types = [getattr(e, "type", None) for e in events]
    # tool_end emitted (because that's the standard tool_end handling)
    # but no TodoList* derivation
    assert "todolist_start" not in types
    assert "todolist_item" not in types
    assert "todolist_end" not in types


def test_processor_no_legacy_state_fields():
    """_StreamProcessor must not have legacy state fields anymore."""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    assert not hasattr(proc, "_emitted_todolist_ids")
    assert not hasattr(proc, "_todo_titles")
    assert not hasattr(proc, "_started_todo_ids")


def test_processor_has_no_legacy_methods():
    from EDPAgent.agent import _StreamProcessor

    assert not hasattr(_StreamProcessor, "_parse_todo_tool_result")
    assert not hasattr(_StreamProcessor, "_map_todo_status")

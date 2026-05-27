"""E2E tests: real Aliyun LLM + mock Versatile + verify 北向 event sequence.

Strategy:
- Use openai SDK in OpenAI-compatible mode pointed at Aliyun DashScope
- Expose lite_todo_write as a tool (OpenAI tool schema, derived from ToolCard.input_params)
- Mock call_versatile so the LLM "sees" a delegated business tool but it returns canned data
- Verify the LLM correctly invokes lite_todo_write with reasonable structure
- Run real LiteTodoWriteTool.invoke() with the LLM's args
- Run real _StreamProcessor against synthesized tool_end raw event
- Assert 北向 event sequence + content matches docs/prd/talking_points.md
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.e2e]


# ── Helpers ─────────────────────────────────────────────────────────────────
def _aliyun_client(env: dict[str, str]):
    from openai import OpenAI
    return OpenAI(api_key=env["ALIYUN_API_KEY"], base_url=env["ALIYUN_API_BASE"])


def _lite_todo_tool_spec() -> dict:
    """OpenAI tool spec derived from EDPAgent.tool.lite_todo.tool_card.LITE_TODO_CARD."""
    from EDPAgent.tool.lite_todo.tool_card import LITE_TODO_CARD
    return {
        "type": "function",
        "function": {
            "name": LITE_TODO_CARD.name,
            "description": LITE_TODO_CARD.description,
            "parameters": LITE_TODO_CARD.input_params,
        },
    }


def _mock_call_versatile_tool_spec() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "call_versatile",
            "description": "调用业务工作流（理财推荐/查询/购买等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_intent": {
                        "type": "string",
                        "description": "业务意图，如：理财推荐 / 查询账户余额 / 理财购买",
                    },
                    "params": {"type": "object"},
                },
                "required": ["query_intent"],
            },
        },
    }


def _make_tool_end_event(plugin: str, data: dict, content: str = ""):
    return SimpleNamespace(
        type="tool_end",
        payload={"plugin": plugin, "data": data, "content": content},
    )


def _scripts():
    return SimpleNamespace(
        todolist_start="开始规划todolist",
        todolist_end="todolist规划完成",
    )


# ── Tests ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_llm_calls_lite_todo_write_for_planning_request(aliyun_env):
    """Real LLM should call lite_todo_write when asked to plan a multi-step task."""
    client = _aliyun_client(aliyun_env)
    response = client.chat.completions.create(
        model=aliyun_env["ALIYUN_MODEL"],
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一名理财助手。当用户提出多步任务时，你必须先调用 lite_todo_write 工具规划任务清单"
                    "（不要先回复用户）。每个 todo 写明 content（中文，简短）和 status。"
                ),
            },
            {
                "role": "user",
                "content": "我要买理财产品。请先帮我规划一下完整步骤。",
            },
        ],
        tools=[_lite_todo_tool_spec(), _mock_call_versatile_tool_spec()],
        tool_choice="auto",
    )
    msg = response.choices[0].message
    assert msg.tool_calls, "LLM should have invoked a tool"
    # First tool call should be lite_todo_write
    assert msg.tool_calls[0].function.name == "lite_todo_write"
    args = json.loads(msg.tool_calls[0].function.arguments)
    assert "todos" in args
    assert isinstance(args["todos"], list)
    assert len(args["todos"]) >= 2  # multi-step plan
    for t in args["todos"]:
        assert "content" in t
        assert t["status"] in ("pending", "in_progress", "done")


@pytest.mark.asyncio
async def test_llm_args_round_trip_through_tool_and_processor(aliyun_env, fake_session):
    """End-to-end: LLM args → real tool invoke → real _StreamProcessor → assert 话术."""
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.agent import _StreamProcessor
    from EDPAgent.tool.lite_todo.models import TODO_STATUS_CN

    # Step 1: Get planning args from real LLM
    client = _aliyun_client(aliyun_env)
    response = client.chat.completions.create(
        model=aliyun_env["ALIYUN_MODEL"],
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一名理财助手。立即调用 lite_todo_write 工具规划"
                    "「推荐稳健型理财产品」任务的完整步骤，所有 status 设为 pending（首项可设为 in_progress）。"
                    "至少 3 步，content 字段必须是中文。"
                ),
            },
            {"role": "user", "content": "推荐稳健型理财产品"},
        ],
        tools=[_lite_todo_tool_spec()],
        tool_choice={"type": "function", "function": {"name": "lite_todo_write"}},
    )
    args = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    todos_in = args["todos"]

    # Step 2: Run real LiteTodoWriteTool with LLM's args
    tool = LiteTodoWriteTool()
    tool_result = await tool.invoke(args, session=fake_session)
    assert tool_result.success is True
    todos_out = tool_result.data["todos"]
    assert len(todos_out) == len(todos_in)

    # Step 3: Synthesize tool_end raw event (as Runner would emit) and feed to _StreamProcessor
    proc = _StreamProcessor(scripts=_scripts())
    raw = _make_tool_end_event(
        plugin="lite_todo_write",
        data={"todos": todos_out, "count": len(todos_out)},
    )
    events = proc.process(raw)

    # Step 4: Verify 北向 event sequence
    types = [getattr(e, "type", None) for e in events]
    assert types[0] == "tool_end"
    assert types[1] == "todolist_start"
    assert types[-1] == "todolist_end"
    item_types = [t for t in types if t == "todolist_item"]
    assert len(item_types) == len(todos_out)

    # Step 5: Verify 话术 content (talking_points L11 format)
    items = [e for e in events if getattr(e, "type", None) == "todolist_item"]
    for idx, (item, todo) in enumerate(zip(items, todos_out), start=1):
        status_cn = TODO_STATUS_CN.get(todo["status"], todo["status"])
        expected_content = f"{idx}.{todo['content']}（{status_cn}）<br/>"
        assert item.content == expected_content, (
            f"item[{idx}] content mismatch: {item.content!r} != {expected_content!r}"
        )
        assert item.id == idx
        assert item.title == todo["content"]
        assert item.status == todo["status"]

    start = [e for e in events if getattr(e, "type", None) == "todolist_start"][0]
    end = [e for e in events if getattr(e, "type", None) == "todolist_end"][0]
    assert start.content == "开始规划todolist"
    assert end.content == "todolist规划完成"
    assert end.count == len(todos_out)


@pytest.mark.asyncio
async def test_full_planning_with_mock_versatile_response(aliyun_env, fake_session):
    """Multi-turn: LLM plans → calls call_versatile (mocked) → updates todos to done.

    Verifies 'overwrite-style' semantics in real LLM usage:
    - Round 1: LLM emits lite_todo_write with all-pending list + call_versatile
    - We mock call_versatile result, feed back as tool message
    - Round 2: LLM emits lite_todo_write with first todo updated to done

    Final assertion: state correctly reflects last write (overwrite, not append).
    """
    from EDPAgent.tool.lite_todo.todo import LiteTodoWriteTool
    from EDPAgent.tool.lite_todo.manager import LiteTodoManager

    client = _aliyun_client(aliyun_env)
    tool = LiteTodoWriteTool()
    messages = [
        {
            "role": "system",
            "content": (
                "你是理财助手。第一步立即调用 lite_todo_write 规划 2-3 步任务（中文 content）。"
                "之后用户提供工具结果时，再次调用 lite_todo_write 把第一项 status 改为 done，"
                "其余项保持原状。每次必须传完整 todos 列表（覆盖式）。"
            ),
        },
        {"role": "user", "content": "推荐理财产品"},
    ]

    # Round 1
    r1 = client.chat.completions.create(
        model=aliyun_env["ALIYUN_MODEL"],
        messages=messages,
        tools=[_lite_todo_tool_spec(), _mock_call_versatile_tool_spec()],
        tool_choice="auto",
    )
    msg1 = r1.choices[0].message
    assert msg1.tool_calls, "round 1: expected tool call"
    todo_call = next((tc for tc in msg1.tool_calls if tc.function.name == "lite_todo_write"), None)
    assert todo_call is not None, "round 1: expected lite_todo_write"
    args1 = json.loads(todo_call.function.arguments)
    result1 = await tool.invoke(args1, session=fake_session)
    assert result1.success is True
    persisted_after_round1 = fake_session.get_state(LiteTodoManager.STATE_KEY)
    assert len(persisted_after_round1) == len(args1["todos"])

    # Simulate round 2: feed tool result back, ask LLM to mark first as done
    messages.append(msg1.model_dump(exclude_none=True))
    messages.append({
        "role": "tool",
        "tool_call_id": todo_call.id,
        "content": json.dumps(result1.data, ensure_ascii=False),
    })
    # If LLM also called call_versatile in round 1, mock that response
    for tc in msg1.tool_calls:
        if tc.function.name == "call_versatile":
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"products": [{"name": "稳健 A", "code": "X1"}]}, ensure_ascii=False),
            })

    messages.append({
        "role": "user",
        "content": "推荐工具已返回结果。请把第一项任务标记为 done，其余保持原状。",
    })

    r2 = client.chat.completions.create(
        model=aliyun_env["ALIYUN_MODEL"],
        messages=messages,
        tools=[_lite_todo_tool_spec()],
        tool_choice={"type": "function", "function": {"name": "lite_todo_write"}},
    )
    msg2 = r2.choices[0].message
    todo_call2 = msg2.tool_calls[0]
    args2 = json.loads(todo_call2.function.arguments)
    result2 = await tool.invoke(args2, session=fake_session)
    assert result2.success is True

    # Verify: at least the first todo is now done (LLM may also have advanced others)
    persisted_after_round2 = fake_session.get_state(LiteTodoManager.STATE_KEY)
    if persisted_after_round2:  # not all-done auto-cleared
        assert any(t["status"] == "done" for t in persisted_after_round2), (
            f"expected at least one done after round 2; got: {persisted_after_round2}"
        )
        # Length unchanged → covers (no append)
        assert len(persisted_after_round2) == len(args1["todos"]), (
            f"expected overwrite (same length), got {len(persisted_after_round2)} vs {len(args1['todos'])}"
        )

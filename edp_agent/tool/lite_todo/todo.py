"""LiteTodoWriteTool — single tool, overwrite-style.

- 一次性传完整列表，覆盖式更新（无 append/delete/modify 子操作）
- step_id 由 ``models._active_steps`` 配置驱动；schema 强约束
- 全部 done 自动清空 state
- 空列表也清空

LLM 失败重试策略：
    若 LLM 传入了非法 step_id（schema 通常已经拦住）或重复 step_id，
    pydantic 会 raise；本工具捕获后返回 success=False + 明确错误文本，
    框架会让 LLM 在下一轮根据错误自我修正。

⚠️ 重要：构造 ``LiteTodoWriteTool()`` 之前必须先调用 ``configure_steps()``
   （由 ``agent.initialize_dpa()`` 加载 AgentRule.md 后自动调用）。否则 ToolCard
   构建会抛 RuntimeError。
"""
from __future__ import annotations

from typing import AsyncIterator, List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Tool

from .manager import LiteTodoManager
from .models import TodoItem, get_canonical_steps, get_step_ids
from .tool_card import build_lite_todo_card


class _ToolOutput:
    """Local result type — mirrors legacy ToolOutput shape but without pydantic dep."""

    def __init__(self, success: bool, data=None, error: str | None = None):
        self.success = success
        self.data = data
        self.error = error


class LiteTodoWriteTool(Tool):
    """单工具，覆盖式语义。"""

    _manager = LiteTodoManager()

    def __init__(self):
        # 每次实例化都基于当前 _active_steps 构建 ToolCard
        super().__init__(build_lite_todo_card())

    async def invoke(self, inputs: Input, **kwargs) -> _ToolOutput:
        session = kwargs.get("session")
        if session is None:
            return _ToolOutput(success=False, data=None, error="session is required")

        raw_todos = inputs.get("todos") if isinstance(inputs, dict) else None
        if raw_todos is None:
            return _ToolOutput(success=False, data=None, error="missing 'todos' field")

        # Parse + validate via TodoItem schema (step_id ∈ 当前 _active_steps)
        try:
            todos: List[TodoItem] = [TodoItem(**t) for t in raw_todos]
        except Exception as e:
            return _ToolOutput(
                success=False,
                data=None,
                error=(
                    f"invalid todo schema: {e}. "
                    f"step_id 必须是 {get_step_ids()} 之一，"
                    f"status 必须是 pending / done 之一。"
                    "不打算做的步骤请直接不放进 todos 列表（不要传 skip_optional 之类的状态）。"
                ),
            )

        # 拒绝重复 step_id（同一会话内每个步骤只应出现一次）
        seen_ids = [t.step_id for t in todos]
        if len(seen_ids) != len(set(seen_ids)):
            return _ToolOutput(
                success=False,
                data=None,
                error=(
                    f"duplicate step_id detected: {seen_ids}; "
                    f"每个 step_id 在 todos 列表里只能出现一次。"
                ),
            )

        # All-done OR empty → clear state
        all_done = bool(todos) and all(t.status.value == "done" for t in todos)
        if not todos or all_done:
            await self._manager.clear(session)
            persisted = []
        else:
            await self._manager.save(session, todos)
            canonical = get_canonical_steps()
            # 持久化时同时保存 step_id, status 与解析出的 content（便于下游不必再 lookup）
            persisted = []
            for t in todos:
                dump = t.model_dump(mode="json")
                dump["content"] = canonical[t.step_id]
                persisted.append(dump)

        logger.info(
            f"[lite_todo] write: {len(todos)} todos, persisted={len(persisted)}, "
            f"step_ids={seen_ids}"
        )

        return _ToolOutput(
            success=True,
            data={
                "todos": persisted,
                "count": len(persisted),
            },
            error=None,
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[_ToolOutput]:
        raise NotImplementedError("LiteTodoWriteTool does not support streaming")


def lite_todo_tools() -> list[Tool]:
    """Return the lite_todo tool list (single tool).

    注意：必须在 ``configure_steps()`` 之后调用——否则 LiteTodoWriteTool() 构造会抛。
    """
    return [LiteTodoWriteTool()]

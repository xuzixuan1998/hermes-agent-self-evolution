# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist tool implementations."""

import json
from typing import AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Tool, ToolCard
from openjiuwen.core.session.agent import Session

from .manager import TodoListManager
from .models import TodoItem, TodoList, TodoStatus, ToolOutput
from .tool_card import (
    TODO_CREATE_CARD,
    TODO_MODIFY_CARD,
    TODO_QUERY_CARD,
)


class TodoToolError(Exception):
    """Todolist 工具异常基类"""
    pass


class TodoToolBase(Tool):
    """任务清单工具基类 - 通过Session State接口管理"""

    # Manager 实例（无状态，每次调用方法时传入 session）
    _manager = TodoListManager()

    def __init__(self, card: ToolCard):
        super().__init__(card)


def _parse_tasks_from_string(tasks_str: str) -> list[str]:
    """解析分号分隔的任务字符串

    Args:
        tasks_str: 分号分隔的任务字符串，支持英文 ; 和中文 ；

    Returns:
        任务内容列表
    """
    if not tasks_str:
        return []
    # 按分号分隔，支持英文 ; 和中文 ；
    import re
    return [t.strip() for t in re.split(r"[;；]", tasks_str) if t.strip()]


def _parse_tasks_from_json(json_str: str) -> list[str]:
    """解析 JSON 格式的任务字符串

    Args:
        json_str: JSON 格式的任务字符串

    Returns:
        任务内容列表
    """
    if not json_str:
        return []
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            result = []
            for item in data:
                if isinstance(item, dict):
                    content = item.get("content", "")
                else:
                    content = str(item)
                if content:
                    result.append(content)
            return result
        return []
    except json.JSONDecodeError:
        logger.warning(f"[Todolist] Failed to parse JSON tasks: {json_str}")
        return []


def _format_todolist(todolist: TodoList) -> str:
    """格式化任务清单为可读字符串

    Args:
        todolist: 任务列表

    Returns:
        格式化后的字符串
    """
    if not todolist:
        return "任务清单为空"

    lines = []
    for task in todolist:
        status_icon = {
            TodoStatus.PENDING: "[ ]",
            TodoStatus.IN_PROGRESS: "[>]",
            TodoStatus.COMPLETED: "[√]",
            TodoStatus.CANCELLED: "[-]",
            TodoStatus.FAILED: "[x]"
        }.get(task.status, "[?]")

        line = f"{status_icon} index={task.index}: {task.content}"
        if task.activeForm:
            line += f" ({task.activeForm})"
        if task.status == TodoStatus.COMPLETED and task.result:
            line += f" -> {task.result}"
        lines.append(line)

    return "\n".join(lines)


class TodoCreateTool(TodoToolBase):
    """创建任务工具 - 继承标准Tool基类"""

    def __init__(self):
        super().__init__(TODO_CREATE_CARD)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """执行任务创建

        Args:
            inputs: 输入参数，支持 tasks(分号分隔) 或 json_tasks(JSON格式)
            **kwargs: 包含 session

        Returns:
            创建结果
        """
        session: Session = kwargs.get("session")
        if not session:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                reason="TodoCreateTool requires a valid session in kwargs"
            )

        # 解析输入
        if isinstance(inputs, dict):
            tasks_str = inputs.get("tasks", "")
            json_tasks_str = inputs.get("json_tasks", "")
        else:
            tasks_str = ""
            json_tasks_str = ""

        # 解析任务内容
        tasks = []
        if json_tasks_str:
            tasks = _parse_tasks_from_json(json_tasks_str)
        if not tasks and tasks_str:
            tasks = _parse_tasks_from_string(tasks_str)

        if not tasks:
            return ToolOutput(
                success=False,
                data=None,
                error="No tasks provided. Please provide either 'tasks' or 'json_tasks' parameter."
            )

        # 创建任务
        try:
            new_tasks = await self._manager.create_todolist(session, tasks, activate_first=True)

            result_text = f"成功创建 {len(new_tasks)} 个任务:\n"
            for task in new_tasks:
                result_text += f"  - index={task.index}: {task.content}\n"

            return ToolOutput(
                success=True,
                data={
                    "tasks": [task.model_dump() for task in new_tasks],
                    "count": len(new_tasks),
                    "formatted": result_text
                },
                error=None
            )
        except Exception as e:
            logger.error(f"[Todolist] Failed to create tasks: {e}")
            return ToolOutput(
                success=False,
                data=None,
                error=str(e)
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[ToolOutput]:
        """流式输出(当前不支持)"""
        raise NotImplementedError("TodoCreateTool does not support streaming")


class TodoQueryTool(TodoToolBase):
    """查询任务工具"""

    def __init__(self):
        super().__init__(TODO_QUERY_CARD)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """执行任务查询

        Args:
            inputs: 输入参数，支持 status 过滤
            **kwargs: 包含 session

        Returns:
            查询结果
        """
        session: Session = kwargs.get("session")
        if not session:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                reason="TodoQueryTool requires a valid session in kwargs"
            )

        # 解析输入
        if isinstance(inputs, dict):
            status_str = inputs.get("status", "")
            include_completed = inputs.get("include_completed", True)
        else:
            status_str = ""
            include_completed = True

        try:
            if status_str:
                # 按状态过滤
                try:
                    status = TodoStatus(status_str)
                except ValueError:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Invalid status: {status_str}. Must be one of: {[s.value for s in TodoStatus]}"
                    )

                todolist = await self._manager.get_tasks_by_status(session, status, include_completed)
            else:
                # 返回全部
                todolist = await self._manager.load(session)

            formatted = _format_todolist(todolist)

            return ToolOutput(
                success=True,
                data={
                    "tasks": [task.model_dump() for task in todolist],
                    "count": len(todolist),
                    "formatted": formatted
                },
                error=None
            )
        except Exception as e:
            logger.error(f"[Todolist] Failed to query tasks: {e}")
            return ToolOutput(
                success=False,
                data=None,
                error=str(e)
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[ToolOutput]:
        """流式输出(当前不支持)"""
        raise NotImplementedError("TodoQueryTool does not support streaming")


class TodoModifyTool(TodoToolBase):
    """修改任务工具（含追加/删除）"""

    def __init__(self):
        super().__init__(TODO_MODIFY_CARD)

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """执行任务修改

        Args:
            inputs: 输入参数，包含 action 和其他参数
            **kwargs: 包含 session

        Returns:
            修改结果
        """
        session: Session = kwargs.get("session")
        if not session:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                reason="TodoModifyTool requires a valid session in kwargs"
            )

        # 解析输入
        if isinstance(inputs, dict):
            action = inputs.get("action", "")
            index = inputs.get("index")
            tasks_str = inputs.get("tasks", "")
            json_tasks_str = inputs.get("json_tasks", "")
            activate_immediately = inputs.get("activate_immediately", False)
            updates = inputs.get("updates", {})
            result = inputs.get("result", "")
        else:
            action = ""
            index = None
            tasks_str = ""
            json_tasks_str = ""
            activate_immediately = False
            updates = {}
            result = ""

        if not action:
            return ToolOutput(
                success=False,
                data=None,
                error="Action is required. Must be one of: start, complete, fail, cancel, update, delete, append"
            )

        try:
            if action == "append":
                # 追加新任务
                tasks = []
                if json_tasks_str:
                    tasks = _parse_tasks_from_json(json_tasks_str)
                if not tasks and tasks_str:
                    tasks = _parse_tasks_from_string(tasks_str)

                if not tasks:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error="No tasks provided for append action"
                    )

                # 加载现有任务列表，追加新任务
                todolist = await self._manager.load(session)
                start_index = len(todolist) + 1

                new_tasks = []
                for i, content in enumerate(tasks):
                    new_task = TodoItem(
                        index=start_index + i,
                        content=content,
                        status=TodoStatus.PENDING
                    )
                    todolist.append(new_task)
                    new_tasks.append(new_task)

                # 处理 activate_immediately
                if activate_immediately:
                    has_in_progress = any(task.status == TodoStatus.IN_PROGRESS for task in todolist)
                    if not has_in_progress and new_tasks:
                        new_tasks[0].status = TodoStatus.IN_PROGRESS

                await self._manager.save(session, todolist)

                result_text = f"成功追加 {len(new_tasks)} 个任务:\n"
                for task in new_tasks:
                    result_text += f"  - index={task.index}: {task.content}\n"

                # 返回完整任务列表，LLM 无需额外调用 query
                all_tasks = await self._manager.load(session)
                formatted_all = _format_todolist(all_tasks)

                return ToolOutput(
                    success=True,
                    data={
                        "tasks": [task.model_dump() for task in new_tasks],
                        "count": len(new_tasks),
                        "formatted": result_text,
                        "all_tasks": [task.model_dump() for task in all_tasks],
                        "all_tasks_formatted": formatted_all,
                    },
                    error=None
                )

            elif action == "delete":
                # 删除任务
                if index is None:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error="Index is required for delete action"
                    )

                success = await self._manager.delete_task(session, index)
                if success:
                    # 返回完整任务列表，LLM 无需额外调用 query
                    all_tasks = await self._manager.load(session)
                    formatted_all = _format_todolist(all_tasks)
                    return ToolOutput(
                        success=True,
                        data={
                            "index": index,
                            "action": "deleted",
                            "all_tasks": [task.model_dump() for task in all_tasks],
                            "all_tasks_formatted": formatted_all,
                        },
                        error=None
                    )
                else:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Task with index {index} not found"
                    )

            elif action in ("start", "complete", "fail", "cancel", "update"):
                # 需要 index 参数的操作
                if index is None:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Index is required for {action} action"
                    )

                # 是否需要自动激活下一个 pending 任务（仅 complete 时使用）
                should_activate_next = False

                # 获取任务（一次性 load，避免后续重复 load）
                todolist = await self._manager.load(session)
                task = None
                task_idx = -1
                for i, t in enumerate(todolist):
                    if t.index == index:
                        task = t
                        task_idx = i
                        break
                if not task:
                    return ToolOutput(
                        success=False,
                        data=None,
                        error=f"Task with index {index} not found"
                    )

                if action == "start":
                    # 检查是否可启动（线性顺序约束）
                    can_start = True
                    for t in todolist:
                        if t.index < index and t.status != TodoStatus.COMPLETED:
                            can_start = False
                            break
                    if not can_start:
                        # 检查是否已有其他 IN_PROGRESS 任务
                        in_progress = next((t for t in todolist if t.status == TodoStatus.IN_PROGRESS), None)
                        if in_progress:
                            return ToolOutput(
                                success=False,
                                data=None,
                                error=f"Cannot start task {index}: task {in_progress.index} is already in progress"
                            )
                        # 检查前序任务
                        return ToolOutput(
                            success=False,
                            data=None,
                            error=f"Cannot start task {index}: previous tasks must be completed first"
                        )

                    # 检查是否已有 IN_PROGRESS 任务（单一 IN_PROGRESS 约束）
                    in_progress = next((t for t in todolist if t.status == TodoStatus.IN_PROGRESS), None)
                    if in_progress and in_progress.index != index:
                        return ToolOutput(
                            success=False,
                            data=None,
                            error=f"Cannot start task {index}: task {in_progress.index} is already in progress"
                        )

                    task.status = TodoStatus.IN_PROGRESS

                elif action == "complete":
                    task.status = TodoStatus.COMPLETED
                    # 完成任务时，activeForm 需要置空
                    task.activeForm = ""
                    # 保存任务执行结果
                    if result:
                        task.result = result
                    # 标记需要自动激活下一个 pending 任务
                    should_activate_next = True

                elif action == "fail":
                    task.status = TodoStatus.FAILED

                elif action == "cancel":
                    task.status = TodoStatus.CANCELLED

                # 处理 update action 的 tasks_str 参数（作为新的任务描述）
                if action == "update" and tasks_str:
                    task.content = tasks_str

                # Apply updates for status change actions (start, complete, fail, cancel, update)
                if updates and action in ("start", "complete", "fail", "cancel", "update"):
                    for key, value in updates.items():
                        if key == "status":
                            # status 字段必须是有效的 TodoStatus 类型，不允许通过 updates 覆盖，而是由 action 参数内部管理
                            raise build_error(
                                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                                reason=f"Cannot manually update 'status' field via updates. Status is managed internally by action '{action}'."
                            )
                        if hasattr(task, key):
                            setattr(task, key, value)

                # 自动激活下一个 pending 任务（仅 complete action）
                if action == "complete" and should_activate_next:
                    # 检查是否已有 IN_PROGRESS 任务（单一 IN_PROGRESS 约束）
                    has_in_progress = any(t.status == TodoStatus.IN_PROGRESS for t in todolist)
                    if not has_in_progress:
                        # 找到下一个 pending 任务并激活
                        for t in todolist:
                            if t.status == TodoStatus.PENDING:
                                t.status = TodoStatus.IN_PROGRESS
                                break

                await self._manager.save(session, todolist)

                # 返回完整任务列表，LLM 无需额外调用 query
                formatted_all = _format_todolist(todolist)

                return ToolOutput(
                    success=True,
                    data={
                        "task": task.model_dump(),
                        "all_tasks": [t.model_dump() for t in todolist],
                        "all_tasks_formatted": formatted_all,
                    },
                    error=None
                )

            else:
                return ToolOutput(
                    success=False,
                    data=None,
                    error=f"Unknown action: {action}"
                )

        except Exception as e:
            logger.error(f"[Todolist] Failed to modify task: {e}")
            return ToolOutput(
                success=False,
                data=None,
                error=str(e)
            )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[ToolOutput]:
        """流式输出(当前不支持)"""
        raise NotImplementedError("TodoModifyTool does not support streaming")


# 工具实例创建函数
def todolist_tools() -> list[Tool]:
    """创建所有 todolist 工具实例

    Returns:
        工具实例列表
    """
    return [
        TodoCreateTool(),
        TodoQueryTool(),
        TodoModifyTool(),
    ]


__all__ = [
    "TodoCreateTool",
    "TodoQueryTool",
    "TodoModifyTool",
    "todolist_tools",
    "TodoToolError",
]
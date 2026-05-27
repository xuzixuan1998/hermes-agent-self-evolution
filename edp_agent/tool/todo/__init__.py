# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist module - Task list management tools for agent workflow.

This module provides task list management capabilities through:
- TodoCreateTool: Create tasks
- TodoQueryTool: Query tasks
- TodoModifyTool: Modify tasks (including append/delete/update)
- TodoListManager: Session State management

Usage example:
    from openjiuwen.harness.tools.todolist import create_todolist_tools

    tools = create_todolist_tools()
    # tools = [TodoCreateTool(), TodoQueryTool(), TodoModifyTool()]
"""

from .manager import TodoListManager
from .models import TodoItem, TodoList, TodoStatus

from .todo import (
    TodoCreateTool,
    TodoModifyTool,
    TodoQueryTool,
    TodoToolError,
    todolist_tools,
)
from .tool_card import (
    TODOLIST_CARDS,
    TODO_CREATE_CARD,
    TODO_MODIFY_CARD,
    TODO_QUERY_CARD,
)

__all__ = [
    # Models
    "TodoStatus",
    "TodoItem",
    "TodoList",
    # Tool Cards
    "TODO_CREATE_CARD",
    "TODO_QUERY_CARD",
    "TODO_MODIFY_CARD",
    "TODOLIST_CARDS",
    # Tools
    "TodoCreateTool",
    "TodoQueryTool",
    "TodoModifyTool",
    "todolist_tools",
    "TodoToolError",
    # Manager
    "TodoListManager",
]
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Todolist manager for Session State operations."""

from typing import List, Optional

from openjiuwen.core.session.agent import Session
from .models import TodoItem, TodoList, TodoStatus


class TodoListManager:
    """任务清单管理器 - 无状态，每次方法调用需传入 session
    """

    STATE_KEY = "todolist"

    def __init__(self):
        """初始化 TodoListManager（无状态）"""
        pass

    async def load(self, session: Session) -> TodoList:
        """加载任务清单（从Session State）
        """
        data = session.get_state(self.STATE_KEY)
        if not data:
            return []
        # 从字典恢复为 TodoItem 对象列表
        return [TodoItem(**item) if isinstance(item, dict) else item for item in data]

    async def save(self, session: Session, todolist: TodoList) -> None:
        """保存任务清单（通过Session State接口）
        """
        # 显式转换为字典列表，确保与 Redis 存储兼容
        # 使用 mode='json' 强制将枚举转换为字符串值
        session.update_state({
            self.STATE_KEY: [item.model_dump(mode='json') if hasattr(item, 'model_dump') else item for item in todolist]
        })

    async def create_todolist(
        self,
        session: Session,
        contents: List[str],
        activate_first: bool = False
    ) -> List[TodoItem]:
        """批量创建任务
        """
        todolist = []
        # index 从 1 开始
        start_index = 1
        new_tasks = []

        for i, content in enumerate(contents):
            new_task = TodoItem(
                index=start_index + i,
                content=content,
                status=TodoStatus.PENDING
            )
            todolist.append(new_task)
            new_tasks.append(new_task)

        # 如果需要立即激活第一个新任务
        if activate_first and new_tasks:
            # 检查是否已有 IN_PROGRESS 任务
            has_in_progress = any(task.status == TodoStatus.IN_PROGRESS for task in todolist)
            if not has_in_progress:
                new_tasks[0].status = TodoStatus.IN_PROGRESS

        await self.save(session, todolist)
        return new_tasks

    async def get_task_by_index(self, session: Session, index: int) -> Optional[TodoItem]:
        """根据索引获取任务
        """
        todolist = await self.load(session)
        for task in todolist:
            if task.index == index:
                return task
        return None

    async def get_in_progress_task(self, session: Session) -> Optional[TodoItem]:
        """获取当前执行中的任务
        """
        todolist = await self.load(session)
        for task in todolist:
            if task.status == TodoStatus.IN_PROGRESS:
                return task
        return None

    async def get_tasks_by_status(
        self,
        session: Session,
        status: TodoStatus,
        include_completed: bool = True
    ) -> TodoList:
        """根据状态获取任务列表

        Args:
            session: Session 实例
            status: 任务状态
            include_completed: 是否包含已完成的任务

        Returns:
            符合条件的任务列表
        """
        todolist = await self.load(session)
        result = []
        for task in todolist:
            if task.status == status:
                result.append(task)
            elif status != TodoStatus.COMPLETED and task.status == TodoStatus.COMPLETED and include_completed:
                # include_completed 为 True 时，在查询其他状态时附带 completed 任务
                result.append(task)
        return result

    async def delete_task(self, session: Session, index: int) -> bool:
        """删除任务
        """
        todolist = await self.load(session)
        for i, task in enumerate(todolist):
            if task.index == index:
                todolist.pop(i)
                # 重新索引，从 1 开始
                for j, t in enumerate(todolist):
                    t.index = j + 1
                await self.save(session, todolist)
                return True
        return False

    async def delete(self, session: Session) -> None:
        """删除任务清单

        Args:
            session: Session 实例
        """
        session.update_state({self.STATE_KEY: None})


__all__ = [
    "TodoListManager",
]
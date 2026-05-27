"""LiteTodoManager — Session State persistence for lite_todo.

Stateless: every method takes a session. Stored under key `lite_todolist`
(distinct from legacy `todolist` key — coexistence by isolation).
"""
from __future__ import annotations

from openjiuwen.core.session.agent import Session

from .models import TodoItem, TodoList


class LiteTodoManager:
    STATE_KEY = "lite_todolist"

    async def load(self, session: Session) -> TodoList:
        data = session.get_state(self.STATE_KEY)
        if not data:
            return []
        return [TodoItem(**item) if isinstance(item, dict) else item for item in data]

    async def save(self, session: Session, todos: TodoList) -> None:
        session.update_state({
            self.STATE_KEY: [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in todos
            ]
        })

    async def clear(self, session: Session) -> None:
        session.update_state({self.STATE_KEY: []})

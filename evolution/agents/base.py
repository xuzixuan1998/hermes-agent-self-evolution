"""Pluggable agent inference backends for self-evolution."""

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Abstract base for pluggable inference backends.

    All backends return a unified dict::

        {"output": str, "messages": list[dict], "completed": bool}
    """

    @abstractmethod
    def run(self, system_prompt: str, task_input: str, config) -> dict:
        """Execute inference with the given system prompt and task input."""
        ...

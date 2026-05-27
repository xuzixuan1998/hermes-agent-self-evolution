"""EDPAgent inference backend via agent_stream()."""

import sys
import uuid
import asyncio
import logging

from evolution.agents.base import BaseAgent

logger = logging.getLogger(__name__)

_edp_initialized = False


async def _ensure_initialized() -> None:
    global _edp_initialized
    if _edp_initialized:
        return
    from edp_agent.agent import initialize_dpa
    await initialize_dpa()
    _edp_initialized = True


async def _collect_stream(conv_id: str, query: str) -> dict:
    from edp_agent.agent import agent_stream

    output_parts = []
    messages = []
    completed = False

    async for event in agent_stream(query=query, conv_id=conv_id):
        event_type = getattr(event, "type", None)
        content = getattr(event, "content", "") or ""

        if event_type == "think_chunk":
            messages.append({"role": "think", "content": content})
        elif event_type in ("summary", "final_answer_chunk"):
            output_parts.append(content)
        elif event_type == "tool_start":
            name = getattr(event, "plugin", "") or ""
            messages.append({"role": "tool", "name": name, "content": "start"})
        elif event_type == "tool_end":
            name = getattr(event, "plugin", "") or ""
            messages.append({"role": "tool", "name": name, "content": "end"})
        elif event_type == "conversation_end":
            completed = True

    return {
        "output": "".join(output_parts),
        "messages": messages,
        "completed": completed,
    }


async def _release_session(conv_id: str) -> None:
    from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerFactory
    await CheckpointerFactory.get_checkpointer().release(conv_id)


class EDPAgent(BaseAgent):
    """Execute a skill via EDPAgent (agent_stream)."""

    def run(self, system_prompt: str, task_input: str, config) -> dict:
        if config.agent_framework_path and str(config.agent_framework_path) not in sys.path:
            sys.path.insert(0, str(config.agent_framework_path))

        try:
            asyncio.run(_ensure_initialized())

            from edp_agent.agent import reload_agent_rule
            reload_agent_rule(system_prompt)

            conv_id = str(uuid.uuid4())
            try:
                result = asyncio.run(_collect_stream(conv_id, task_input))
            finally:
                try:
                    asyncio.run(_release_session(conv_id))
                except Exception:
                    logger.debug("Failed to release session %s", conv_id, exc_info=True)

            return result
        except Exception:
            logger.exception("EDPAgent.run failed")
            return {"output": "", "messages": [], "completed": False}

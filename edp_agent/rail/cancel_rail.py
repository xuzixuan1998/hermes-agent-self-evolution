"""
CancelRail：cancel_task 工具调用后强制终止 Agent 循环。

cancel_task 是终止操作，调用后不应再执行任何工具或 LLM 推理，
因此通过 request_force_finish 直接结束 ReAct 循环。
同时注入 response_template，确保前端收到固定取消话术。
取消时标记 checkpoint 待删除，由 agent.py 流末在 post_run 之后执行。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext

from ..agent_rule import ScriptsConfig

_CHECKPOINT_RELEASE_KEY = "checkpoint_to_release"


class CancelRail(AgentRail):
    """cancel_task 调用后强制终止 Agent 循环。"""

    priority = 10

    def __init__(self, scripts_config: Optional[ScriptsConfig] = None) -> None:
        super().__init__()
        self._scripts_config = scripts_config

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = getattr(ctx.inputs, "tool_name", "") or ""
        if tool_name == "cancel_task":
            logger.info("[CancelRail] cancel_task 已执行，强制终止 Agent 循环")

            if self._scripts_config:
                tool_args = getattr(ctx.inputs, "tool_args", None) or {}
                reason = tool_args.get("reason", "task_cancelled") if isinstance(tool_args, dict) else "task_cancelled"
                template_text = self._scripts_config.get_response_template(reason)
                if template_text:
                    ctx.session.update_state({"response_template": template_text})
                    logger.info("[CancelRail] response_template 已注入：key=%r", reason)
                else:
                    logger.warning("[CancelRail] 未找到 response_template：key=%r", reason)

            # base Session 没有公开 session_id property，只有私有 _session_id 才是
            # create_agent_session 传入的 conv_id；优先取 public，fallback 私有。
            session_id = (
                getattr(ctx.session, "session_id", None)
                or getattr(ctx.session, "_session_id", None)
            )
            if session_id:
                ctx.session.update_state({_CHECKPOINT_RELEASE_KEY: session_id})
                logger.info(f"[CancelRail] 已标记 checkpoint 待删除：session_id={session_id}")
            else:
                logger.warning("[CancelRail] session_id 为空，checkpoint 无法标记删除")

            ctx.request_force_finish({
                "type": "task_cancelled",
                "content": "用户取消任务",
            })

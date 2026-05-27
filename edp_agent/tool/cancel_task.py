"""
任务终止工具。

仅在用户确认取消后调用。调用前必须先通过 ask_user 确认用户意图，
用户明确回复确认后才能调用此工具。

禁止在以下场景调用：
  - 未经 ask_user 确认直接调用
  - 用户只是修改筛选条件（如"换一批"、"换个稳健型"）→ 应继续推荐流程
  - 用户询问产品详情（如"这个收益多少"）→ 应回答问题
  - 用户确认购买（如"买这个"）→ 应进入购买流程
  - 用户输入模糊（如"算了"）→ 应先用 ask_user 确认意图
"""
from __future__ import annotations

from typing import Any, Dict

from loguru import logger
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def cancel_task(
    reason: str = "task_cancelled",
) -> Dict[str, Any]:
    """终止当前任务并输出固定话术。仅在用户明确表达取消/终止意图时调用。"""
    logger.info(f"[cancel_task] reason={reason!r}")
    return {"status": "cancelled", "reason": reason}


cancel_task_tool = LocalFunction(
    card=ToolCard(
        id="cancel_task",
        name="cancel_task",
        description=(
            "任务终止工具。仅在用户通过 ask_user 确认取消后调用。"
            "调用前必须先调用 ask_user 并传入 response_template_status='cancel_confirm' 等待用户确认，"
            "用户明确回复确认后才能调用此工具。"
            "调用后 Agent 循环将自动终止，并输出固定话术。"
            "禁止未经 ask_user 确认直接调用此工具。"
        ),
        input_params={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "终止原因话术 key，对应 AgentRule.md 中 scripts 配置。"
                        "默认 'task_cancelled'。"
                    ),
                },
            },
            "required": [],
        },
    ),
    func=cancel_task,
)

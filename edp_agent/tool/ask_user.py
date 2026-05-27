from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def ask_user(
    question: str = "",
    response_template_keys: str = "",
    response_template_status: str = "",
    response_template_vars: str = "",
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """追问用户信息（默认直接执行；当传入 response_template_* 参数时由 AskUserTemplateRail 拦截改走话术中断路径）。"""
    logger.info(
        f"[ask_user] question={question!r:.200}, "
        f"status={response_template_status!r:.40}, "
        f"keys={response_template_keys!r:.120}, "
        f"vars={response_template_vars!r:.200}"
    )
    return {
        "status": "awaiting_user_response",
        "question": question,
        "user_response": None,
        "should_stop": True,
        "message": "问题已发送给用户，当前轮到此停止，等待用户下一轮回复。",
    }


ask_user_tool = LocalFunction(
    card=ToolCard(
        id="ask_user",
        name="ask_user",
        description="追问用户信息。当前轮只负责把问题发给用户，必须等待用户下一轮回复，不能将本次调用视为用户已确认。",
        input_params={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的问题（兜底文本，话术参数缺失时使用）"},
                "response_template_keys": {
                    "type": "string",
                    "description": (
                        "话术 key 映射的 JSON 字符串，格式："
                        '\'{"<status1>": "<key1>", "<status2>": "<key2>"}\'。'
                        "key 对应 AgentRule.md scripts 配置项。留空则按 question 文本兜底。"
                    ),
                },
                "response_template_status": {
                    "type": "string",
                    "description": (
                        "当前命中的话术状态名，必须在 response_template_keys 的 key 集合中。"
                        "示例：'confirm' / 'missing_product' / 'missing_amount'。留空则按 question 文本兜底。"
                    ),
                },
                "response_template_vars": {
                    "type": "string",
                    "description": (
                        "用于话术模板变量替换的 JSON 字符串，例如 "
                        '\'{"amount": "50000", "productName": "添利宝"}\'。模板未引用的变量会被忽略；'
                        "模板引用但未提供的变量会被替换为空字符串。"
                    ),
                },
            },
            "required": ["question"],
        },
    ),
    func=ask_user,
)

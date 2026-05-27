"""
通用脚本调用工具。

通过 MCPInterruptRail 拦截，执行 script_command 指定的 Python 脚本。

参数设计：
  - script_command  → 要执行的脚本命令（如 python xxx/scripts/run_mcp_recommend.py）
  - script_params   → LLM 生成的业务参数 JSON（包含脚本运行所需的全部业务入参）

  mcp_required_params（如 clientIP、userAgent 等客户端环境信息）由 MCPInterruptRail 从 session state 自动注入，
  不出现在 LLM 参数中，避免 LLM 篡改或泄露敏感信息。

Rail 拦截后：
  MCPInterruptRail 拦截 → 合并 script_params + mcp_required_params → 执行 script_command
  → session state 写入 mcp_products_data → reject(tool_result=)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


async def call_mcp(
    script_command: str = "",
    script_params: str = "",
    session: Optional[Any] = None,  # noqa: ARG001 — 框架约定签名，Rail 通过 ctx.session 访问
) -> Dict[str, Any]:
    """通用脚本调用（Rail 拦截并执行脚本）"""
    logger.info(
        f"[call_mcp] script_command={script_command!r:.80}, "
        f"script_params={script_params!r:.120}"
    )
    return {}


call_mcp_tool = LocalFunction(
    card=ToolCard(
        id="call_mcp",
        name="call_mcp",
        description=(
            "通用脚本调用工具。"
            "执行指定的 Python 脚本，并将结果返回。"
            "通过 script_command 指定要执行的脚本路径，"
            "通过 script_params 传入脚本所需的业务参数 JSON。"
            "首次推荐或多轮交互式推荐均可通过此工具获取数据。"
        ),
        input_params={
            "type": "object",
            "properties": {
                "script_command": {
                    "type": "string",
                    "description": (
                        "要执行的 Python 脚本命令。"
                        "示例：'python rebuild_interact_finance_rec_skill/scripts/run_mcp_recommend.py'。"
                        "脚本在 skills/ 目录下执行，通过 SKILL_INPUT 环境变量接收参数。"
                    ),
                },
                "script_params": {
                    "type": "string",
                    "description": (
                        "脚本业务参数 JSON 字符串，由 LLM 根据 SKILL.md 填写。"
                        "示例：'{\"mcp_params\": {\"filterRiskLevel\": \"2\"}, "
                        "\"history_product_codes\": [], \"current_sort_type\": 0}'。"
                        "该 JSON 会被解码后作为 SKILL_INPUT 的一部分传入脚本。"
                        "禁止传入 mcp_required_params（由系统自动注入）。"
                    ),
                },
            },
            "required": ["script_command", "script_params"],
        },
    ),
    func=call_mcp,
)

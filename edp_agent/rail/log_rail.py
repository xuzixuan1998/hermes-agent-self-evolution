"""log rail.

Hooks into ``before_model_call`` and ``after_model_call`` to log
LLM input and response.

Also includes a tool_call.arguments JSON auto-repair step in ``after_model_call``
to fix the common LLM streaming quirk where the trailing closing bracket is
missing (e.g. ``{"todos":[{...},{...}]`` without the outer ``}``).
"""

from __future__ import annotations
import datetime
import json
import uuid
from typing import Any
from loguru import logger

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ModelCallInputs,
)
from openjiuwen.core.single_agent.ability_manager import AbilityManager

from common.logger import Extra, Tag, to_logger, TagObservation, ObservationType, Level

from openjiuwen.core.foundation.tool import Tool


class LogRail(AgentRail):
    """log model calls.

    This rail hooks into the SDK rail framework via
    ``before_model_call`` and ``after_model_call`` and log LLM input / response.

    """
    def __init__(self, model_name: str, tools: list[Tool]) -> None:
        self.model_name = model_name
        self.tools = tools

    priority = 10  # low priority — run after other rails


    def init(self, agent):
        #此处无法记录tool加载的耗时。所以日志放在了agent.py中打印了。
        pass
        # now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        # agent_init_toollist = TagObservation(
        #     id=str(int(time.time() * 1000)),
        #     type=ObservationType.SPAN,
        #     name="agent初始化",
        #     start_time=now,
        #     end_time=now,
        #     input={"tool_list": list(map(lambda tool: tool.card.tool_info().model_dump(exclude_none=True), self.tools))},
        # )
        #
        # trace_id = str(int(time.time() * 1000))
        # with logger.contextualize(trace_id=trace_id, agent_id="default_agent_id", conversation_id=trace_id):
        #     to_logger(
        #         "INFO", agent_init_toollist, Extra(tag=Tag.TAG_AGENT_INIT_TOOLLIST, cost=2)
        #     )


    def _extract_model_text(
        self,
        inputs: ModelCallInputs,
    ) -> list[dict]:
        """Extract text from model inputs."""
        parts: list[dict] = []
        for msg in inputs.messages:
            parts.append(msg.model_dump())
        return parts

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """log model input."""
        call_id = str(uuid.uuid4())
        ctx.extra["_log_rail_call_id"] = call_id
        start_time = datetime.datetime.now()
        ctx.extra["_log_rail_start_time"] = start_time
        llm_call_start = TagObservation(
            id=call_id,
            type=ObservationType.GENERATION,
            name=self.model_name,
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            input={"messages": self._extract_model_text(ctx.inputs)},
        )
        to_logger(level=Level.INFO, message=llm_call_start, extra=Extra(tag=Tag.TAG_LLM_CALL_START, cost=0))

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """log model response."""

        response = getattr(ctx.inputs, "response", None)
        if response is None:
            return

        # ── tool_call.arguments JSON 自动修复 ────────────────────────────
        # LLM streaming 偶发会少生成 args 末尾的闭合 ``}``（reasoning 模型常见 quirk），
        # agent-core 在执行 tool 时通过 _repair_tool_arguments_json 自动补全栈底未闭合的
        # `{` `[`，但**不**在持久化 AssistantMessage 之前修复——这导致：
        #   1. 工具能正常执行
        #   2. 但 history 里存的还是坏 JSON
        #   3. 下一轮 LLM 调用 messages 带着坏 args → 严格网关 400 / 模型困惑返空
        #
        # 这里在 after_model_call 钩子里 in-place 修复 ai_message.tool_calls，
        # 由于 react_agent.py:1290 的 add_messages(...) 用的就是这个 ai_message
        # 引用，修好的 args 会被持久化进 Redis，根治 sticky 失败链。
        try:
            self._repair_tool_call_arguments(response)
        except Exception as e:
            logger.warning(f"[LogRail] _repair_tool_call_arguments raised, skipping: {e!r}")

        call_id = ctx.extra.get("_log_rail_call_id", "")
        start_time = ctx.extra.get("_log_rail_start_time", datetime.datetime.now())
        end_time = datetime.datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        llm_call_end = TagObservation(
            id=call_id,
            type=ObservationType.GENERATION,
            name=self.model_name,
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            output=response.model_dump(),
            status_message=0,
            # cost_details={"first_token": 1}, 这里取不到值，在agent.py流式那里取
            total_cost=duration_ms,
        )

        to_logger(level=Level.INFO, message=llm_call_end, extra=Extra(tag=Tag.TAG_LLM_CALL_END, cost=duration_ms))

    @staticmethod
    def _repair_tool_call_arguments(response: Any) -> None:
        """对 response.tool_calls 里 arguments 不合法的 JSON 做 in-place 自动修复。

        复用 agent-core 自带的 ``AbilityManager._repair_tool_arguments_json``——
        遍历未闭合的 ``{`` / ``[`` 栈底，按相反顺序补齐 ``}`` / ``]``。
        这个修复对"丢末尾 `}`"这种最常见的 LLM streaming quirk 100% 生效。

        命中时打 WARNING；修复后仍非法（极罕见）时打 ERROR。
        """
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return

        for tc in tool_calls:
            args = getattr(tc, "arguments", "") or ""
            if not isinstance(args, str) or not args.strip():
                continue

            # 合法直接跳过
            try:
                json.loads(args)
                continue
            except (ValueError, json.JSONDecodeError):
                pass

            # 调 agent-core 修复函数
            logger.info(
                f"[LogRail] before repair | name={getattr(tc, 'name', '?')} | "
                f"args_len={len(args)} | args={args!r}"
            )
            repaired = AbilityManager._repair_tool_arguments_json(args)
            if not repaired or repaired == args:
                logger.error(
                    f"[LogRail] FAILED to repair malformed tool_call arguments | "
                    f"name={getattr(tc, 'name', '?')} | "
                    f"args_len={len(args)} | "
                    f"args_tail={args[-60:]!r}"
                )
                continue

            try:
                json.loads(repaired)
            except (ValueError, json.JSONDecodeError) as e:
                logger.error(
                    f"[LogRail] repaired args still invalid | "
                    f"name={getattr(tc, 'name', '?')} | err={e} | "
                    f"repaired_tail={repaired[-60:]!r}"
                )
                continue

            tc.arguments = repaired
            logger.info(
                f"[LogRail] after repair | name={getattr(tc, 'name', '?')} | "
                f"repaired_len={len(repaired)} | repaired={repaired!r}"
            )
            logger.warning(
                f"[LogRail] auto-repaired malformed tool_call.arguments | "
                f"name={getattr(tc, 'name', '?')} | "
                f"diff={len(repaired) - len(args)} chars added | "
                f"original_tail={args[-40:]!r} | "
                f"repaired_tail={repaired[-40:]!r}"
            )
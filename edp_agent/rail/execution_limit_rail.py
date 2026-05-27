"""
ExecutionLimitRail - 按工具名的执行次数计数与上限控制。

规则 5（需求文档 §4.2）：按工具名配置上限（如 call_versatile: 10）。

同时承担在 Runner 的流里补发 tool_start / tool_end 事件的职责
（Runner 自身发的事件类型名就是 tool_start / tool_end，本 Rail 用
同名 OutputSchema 做话术层覆盖/补齐）。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from common.logger import Extra, Tag, to_logger, TagObservation, ObservationType, Level

from loguru import logger
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext

from .. import state_keys
from ..agent_rule import AgentRuleConfig


DEFAULT_LIMIT = 100


# query_intent → 单步话术映射（对齐 docs/prd/talking_points.md §四/§七）
# todo_start: skill 阶段开始时的"开始 X" 话术
# todo_end: skill 阶段完成时的"已 X" 话术
QUERY_INTENT_TO_TODO_TEXT: dict[str, dict[str, str]] = {
    "查询账户余额": {
        "todo_start": "开始查询账户余额",
        "todo_end": "已查询账户余额",
    },
    "快速转账": {
        "todo_start": "开始执行资金转账",
        "todo_end": "资金转账结束",
    },
    "理财购买": {
        "todo_start": "开始办理理财产品购买",
        "todo_end": "理财产品购买流程结束",
    },
    "理财选品购买": {
        "todo_start": "开始办理理财产品购买",
        "todo_end": "理财产品购买流程结束",
    },
    "理财推荐": {
        "todo_start": "开始获取理财产品列表",
        "todo_end": "已获取理财产品列表",
    },
}


class ExecutionLimitRail(AgentRail):
    """按工具名限流并补齐 tool_start/tool_end 事件。"""

    priority = 40

    # ── Skill 调用日志相关常量 ─────────────────────────────────────
    SKILL_STATE_KEY = "_current_skill"
    TIMING_KEY = "_tool_start_time"
    # 跟踪当前 query_intent 用于 todo_end 话术
    LAST_INTENT_KEY = "_last_query_intent"

    # ════════════════════════════════════════════════════════════════
    # 初始化
    # ════════════════════════════════════════════════════════════════

    def __init__(self, config: AgentRuleConfig) -> None:
        self.config = config
        self.task_limits = config.limits.tasks
        self.default_limit = DEFAULT_LIMIT
        self.scripts = config.scripts

    def _get_limit(self, tool_name: str) -> int:
        return self.task_limits.get(tool_name, self.default_limit)

    # ═══════════════════════════════════════════════════════════════
    # Skill 追踪
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_skill_from_path(file_path: str) -> str | None:
        """检测 read_file 是否读取 SKILL.md，是则返回所在 Skill 目录名。"""
        if not file_path or "SKILL.md" not in file_path:
            return None
        parts = Path(file_path).parts
        for i, part in enumerate(parts):
            if part == "SKILL.md" and i > 0:
                return parts[i - 1]
        return None

    def _log_tool_call(self, phase: str, conv_id: str, tool_name: str,
                       skill_name: str | None, **extra) -> None:
        """统一格式：[EXEC_LOG] START|END | conv=xxx | tool=xxx | skill=xxx | ..."""
        parts = [
            f"[EXEC_LOG] {phase}",
            f"conv={conv_id}",
            f"tool={tool_name}",
            f"skill={skill_name or '-'}",
        ]
        for k, v in extra.items():
            if v is not None:
                s = str(v)
                parts.append(f"{k}={s[:200]}")
        logger.info(" | ".join(parts))

    def _log_skill_call_start(self, ctx, tool_name: str, tool_args: dict) -> None:
        """记录工具调用开始：检测 Skill 切换并输出 EXEC_LOG START 日志。"""
        conv_id = getattr(ctx.session, "session_id", "-")
        skill_name = ctx.session.get_state(self.SKILL_STATE_KEY)

        # 设置初始化时间,打印SKILL执行日志
        start_time = time.time()
        ctx.session.update_state({f"_skill_start_time_{tool_name}": start_time})
        call_id = str(uuid.uuid4())
        ctx.session.update_state({f"_skill_call_id_{tool_name}": call_id})
        if tool_name == "read_file":
            detected = self._extract_skill_from_path(
                tool_args.get("file_path", "")
            )
            if detected:
                ctx.session.update_state({self.SKILL_STATE_KEY: detected})

        to_logger(
            level=Level.INFO,
            message={
                "id": call_id,
                "start_time": datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "type": "SKILL",
                "tool_name": tool_name if tool_name is not None else "",
                "name": skill_name if skill_name is not None else "",
                "input": tool_args if tool_args is not None else {},
                "metadata": {}
            },
            extra=Extra(tag=Tag.TAG_SKILL_EXECUTE_START, cost=0),
        )

        ctx.session.update_state({self.TIMING_KEY: time.time()})


    def _log_skill_call_end(self, ctx, tool_name: str) -> None:
        """记录工具调用结束：计算耗时并输出 EXEC_LOG END 日志。"""
        conv_id = getattr(ctx.session, "session_id", "-")
        skill_name = ctx.session.get_state(self.SKILL_STATE_KEY)
        start_time = (
                ctx.session.get_state(f"_skill_start_time_{tool_name}")
                or time.time()
        )
        call_id= ctx.session.get_state(f"_skill_call_id_{tool_name}")
        duration_ms = int((time.time() - start_time) * 1000) if start_time else -1
        ctx.session.update_state({self.TIMING_KEY: None})

        # 提取 result_keys（仅 dict 类型）
        tool_result = getattr(ctx.inputs, "tool_result", None)
        result_keys = list(tool_result.keys()) if isinstance(tool_result, dict) else None
        to_logger(
            level=Level.INFO,
            message={
                "id": call_id,
                "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "type": "SKILL",
                "tool_name": tool_name if tool_name is not None else "",
                "name": skill_name if skill_name is not None else "",
                "output": result_keys if result_keys is not None else {},
                "status_message": 0,
                "metadata": {},
                "total_cost": duration_ms
            },
            extra=Extra(tag=Tag.TAG_SKILL_EXECUTE_END, cost=duration_ms),
        )

    # ═══════════════════════════════════════════════════════════════
    # Rail 钩子
    # ═══════════════════════════════════════════════════════════════

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = getattr(ctx.inputs, "tool_name", "") or ""
        tool_args = getattr(ctx.inputs, "tool_args", {}) or {}
        start_time = time.time()
        call_id = str(uuid.uuid4())
        ctx.session.update_state({f"_tool_call_id_{tool_name}": call_id})
        ctx.session.update_state({f"_tool_start_time_{tool_name}": start_time})
        # 诊断日志：所有 LLM 触发的 tool_call 都在这里打 [EDP-LLM-TOOL]，含被
        # _StreamProcessor 上层（_StreamProcessor 上 tool_start 分支）拦不到的
        # ask_user / lite_todo_write / read_file。这是"LLM 真返了 tool_calls"
        # 的最早证据点（在事件 emit 前），args 全文不截断。
        if isinstance(tool_args, str):
            args_str = tool_args
        else:
            try:
                args_str = json.dumps(tool_args, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = repr(tool_args)

        counts = ctx.session.get_state(state_keys.EXEC_COUNTS) or {}

        # 兼容字符串形式的 tool_args
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args) if tool_args else {}
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[ExecutionLimitRail] tool_args JSON 解析失败 "
                    f"tool={tool_name}, err={e}, raw={tool_args!r:.120}"
                )
                tool_args = {}

        to_logger(
            level=Level.INFO,
            message={
                "id":call_id,
                "type": "TOOL",
                "start_time": datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "name": tool_name if tool_name is not None else "",
                "input": tool_args if tool_args is not None else {},
            },
            extra=Extra(tag=Tag.TAG_TOOL_EXECUTE_START, cost=0),
        )

        current = counts.get(tool_name, 0)
        limit = self._get_limit(tool_name)

        if current >= limit:
            logger.warning(
                f"[ExecutionLimitRail] 工具 {tool_name} 达到执行上限 "
                f"current={current}, limit={limit}，强制结束"
            )
            ctx.request_force_finish({
                "type": "execution_limit_exceeded",
                "content": f"工具 {tool_name} 已达到执行次数限制（{limit}），终止执行",
                "tool_name": tool_name,
                "count": current,
            })
            return

        counts[tool_name] = current + 1
        ctx.session.update_state({state_keys.EXEC_COUNTS: counts})

        # 补发 tool_start 事件（含业务话术）
        # 内部工具不暴露给前端：lite_todo_write 派生 todolist_*；read_file 读 SKILL.md
        # 是 LLM 内部机制；ask_user 由 AskUserRail 通过 interrupt_start 呈现。
        if tool_name not in ("lite_todo_write", "read_file", "ask_user"):
            # 业务工具（call_versatile / call_mcp 等）用 query_description 作为话术
            business_content = (
                tool_args.get("query_description")
                or tool_args.get("script_command")
                or self.scripts.tool_start.format(tool_name=tool_name)
            )
            # 先发 todo_start（单步话术，对齐 talking_points §四/§七）
            #
            # call_versatile 的 todo_start/end 由 VersatileInterruptRail 全权管理
            # （在 self.interrupt() 之前发 start，cascade resume 之后发 end），
            # 本 rail 完全跳过——避免在 interrupt 路径上 after_tool_call 提前发
            # todo_end + cascade resume 时 before_tool_call 重复发 todo_start。
            #
            # 其它业务工具（call_mcp 等）暂时不在 QUERY_INTENT_TO_TODO_TEXT 里，
            # 即便此处不跳过也无影响；但保留兜底逻辑以便未来扩展。
            if tool_name != "call_versatile":
                query_intent = tool_args.get("query_intent", "")
                todo_map = QUERY_INTENT_TO_TODO_TEXT.get(query_intent)
                if todo_map:
                    ctx.session.update_state({self.LAST_INTENT_KEY: query_intent})
                    await ctx.session.write_stream(OutputSchema(
                        type="todo_start",
                        index=0,
                        payload={
                            "content": todo_map["todo_start"],
                            "id": query_intent,
                            "title": todo_map["todo_start"],
                        },
                    ))
                    logger.info(f"[ExecutionLimitRail] todo_start: {todo_map['todo_start']}")

            # 再发 tool_start
            await ctx.session.write_stream(OutputSchema(
                type="tool_start",
                index=0,
                payload={
                    "content": business_content,
                    "plugin": tool_name,
                    "args": tool_args,
                },
            ))
            logger.info(f"[ExecutionLimitRail] tool_start: {tool_name} content={business_content!r:.80}")
        else:
            logger.info(f"[ExecutionLimitRail] tool_start (suppressed-internal): {tool_name}")

        if tool_name in ("read_file"):
            # 只有read_file的tool才记录skill的调用。因为skill都是同构readfile实现的
            self._log_skill_call_start(ctx, tool_name, tool_args)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = getattr(ctx.inputs, "tool_name", "") or ""
        tool_result = getattr(ctx.inputs, "tool_result", None)
        start_time = (
                ctx.session.get_state(f"_tool_start_time_{tool_name}")
                or time.time()
        )
        call_id = (ctx.session.get_state(f"_tool_call_id_{tool_name}") or "")
        duration_ms = int((time.time() - start_time) * 1000) if start_time else -1
        # 提取返回 data
        result_data: dict = {}
        if tool_result:
            if isinstance(tool_result, dict):
                result_data = tool_result.get("data", tool_result)
            elif hasattr(tool_result, "data"):
                result_data = tool_result.data if isinstance(tool_result.data, dict) else {}
            elif hasattr(tool_result, "model_dump"):
                dumped = tool_result.model_dump()
                result_data = dumped.get("data", dumped)

        # 仅给 lite_todo_write 发 raw tool_end —— 派生 todolist_* 的内部信号
        if tool_name == "lite_todo_write":
            await ctx.session.write_stream(OutputSchema(
                type="tool_end",
                index=0,
                payload={
                    "content": "",
                    "plugin": tool_name,
                    "data": result_data,
                },
            ))
            logger.info(f"[ExecutionLimitRail] tool_end (lite_todo internal): {tool_name}")
        else:
            logger.info(
                f"[ExecutionLimitRail] tool_end (suppressed-spec): {tool_name} "
                f"data_keys={list(result_data.keys()) if result_data else []}"
            )

        # 业务工具 done → 补 todo_end 单步话术（对齐 talking_points §四/§七）
        # 同 before_tool_call：call_versatile 由 VersatileInterruptRail 在
        # cascade resume 收尾时发 todo_end，本 rail 跳过——避免在 interrupt 路径
        # 上提前 emit（那时 cascade 尚未 resume，versatile 也还没真跑完）。
        if tool_name not in ("lite_todo_write", "read_file", "ask_user", "call_versatile"):
            last_intent = ctx.session.get_state(self.LAST_INTENT_KEY)
            todo_map = QUERY_INTENT_TO_TODO_TEXT.get(last_intent or "")
            if todo_map:
                await ctx.session.write_stream(OutputSchema(
                    type="todo_end",
                    index=0,
                    payload={
                        "content": todo_map["todo_end"],
                        "id": last_intent,
                        "status": "done",
                    },
                ))
                ctx.session.update_state({self.LAST_INTENT_KEY: None})
                logger.info(f"[ExecutionLimitRail] todo_end: {todo_map['todo_end']}")

        if tool_name in ("read_file"):
            # 只有read_file的tool才记录skill的调用。因为skill都是同构readfile实现的
            self._log_skill_call_end(ctx, tool_name)
        to_logger(
            level=Level.INFO,
            message={
                "id":call_id,
                "type": "TOOL",
                "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "name": tool_name if tool_name is not None else "",
                "output": getattr(tool_result, 'model_dump', lambda: getattr(tool_result, '__dict__',
                                                                             tool_result))() if tool_result is not None else {},
                "status_message": 0,
                "metadata": {},
                "total_cost": duration_ms
            },
            extra=Extra(tag=Tag.TAG_TOOL_EXECUTE_END, cost=duration_ms),
        )

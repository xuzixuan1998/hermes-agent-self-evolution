"""
VersatileInterruptRail：拦截业务工具调用，通过 DelegateRequest 委托给 Orchestrator。

当前仅保留 call_versatile 一条业务调用链：
    - 首次拦截时记录 pending_delegate + pending_tool_context
    - Cascade 续轮时从 workflow_result / End 节点提取 business_data
    - 如配置了 sys_operation_id，则执行沙箱归一化脚本
    - 如未配置 sys_operation_id 或脚本为空，则降级透传 business_data

query_description 自动注入：
    - 避免query_description过长，LLM输出Token太大，导致时延过大问题，建议通过session传递
    - 当 call_versatile 的 query_description 为空时，从 session 读取
      MCPInterruptRail 缓存的 versatile_query 作为 query_description
    - 其他参数（query_intent、query_response_analysis_scripts、response_template_keys）
      仍由 LLM 传入，不做改动

设计原则：
  - 零 A2A 依赖：不引用 EventQueue、A2AClient 等任何 A2A 对象
  - 不主动调用外部 Agent / 工作流：只记录委托意图，由 Orchestrator 执行
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Optional

from loguru import logger
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from ..config import get_settings
from ..agent_rule import ScriptsConfig

_SANDBOX_TIMEOUT = 60


def _get_skills_dir() -> Path:
    settings = get_settings()
    sandbox_url = (settings.sandbox_url or "").strip()
    if sandbox_url:
        target = (settings.skill_target_path or "/tmp").strip() or "/tmp"
        return Path(target) / "skills"
    return Path(__file__).resolve().parent.parent / "skills"


class VersatileInterruptRail(BaseInterruptRail):
    """
    拦截 call_versatile 工具调用的 Rail。

    LLM 直接传入 query_intent / query_description / query_response_analysis_scripts。

    两种执行路径：
      1. 首次拦截（cascade_result 不存在）：
         写入 pending_delegate + pending_tool_context → interrupt()

      2. Cascade 续轮（cascade_result 存在）：
         提取 business_data → 解析脚本路径 → 沙箱归一化 → reject(tool_result=normalized)
    """

    def __init__(self, sys_operation_id: Optional[str] = None, scripts_config: Optional[ScriptsConfig] = None) -> None:
        super().__init__(tool_names=["call_versatile"])
        self._sys_operation_id = sys_operation_id
        self._scripts_config = scripts_config

    async def resolve_interrupt(
        self,
        ctx,
        tool_call,
        user_input,
        auto_confirm_config=None,
    ):
        tool_args = ctx.inputs.tool_args or {}
        tool_name = tool_call.name if hasattr(tool_call, "name") else None
        tool_args = self._normalize_tool_args(tool_args, tool_name)

        # ── query_description 为空时，从 session 注入缓存的 mcp_to_versatile_information ──
        if not tool_args.get("query_description", "").strip():
            cached_query = ctx.session.get_state("mcp_to_versatile_information")
            if cached_query:
                tool_args["query_description"] = cached_query
                ctx.session.update_state({"mcp_to_versatile_information": None})
                logger.info(
                    f"[VersatileInterruptRail] query_description 从缓存注入：{cached_query}"
                )

        cascade_result = ctx.session.get_state("cascade_result")
        if cascade_result is not None:
            ctx.session.update_state({"cascade_result": None})
            return await self._handle_cascade_resume(ctx, tool_name, tool_args, cascade_result)

        delegate_info = self._build_delegate(tool_name, tool_args)

        logger.info(
            f"[VersatileInterruptRail] 拦截 {tool_name}："
            f"intent={delegate_info['intent']}, desc={delegate_info['task_description']!r:.60}"
        )

        guard_decision = self._apply_pre_delegate_guard(ctx, tool_args)
        if guard_decision is not None:
            return guard_decision

        # 方案 A：在 interrupt 之前先发 todo_start（铺垫话术），避免事件序倒挂。
        # 背景：本 rail priority=90，在 ExecutionLimitRail (priority=40) 之前跑；
        # 一旦 self.interrupt() 抛出 ToolInterruptException，hook 链立即结束，
        # ExecutionLimitRail 的 todo_start 永远没机会发，结果用户在 versatile
        # 真实执行完转账之后才看到 "开始执行资金转账" 话术。这里在首次拦截
        # 时主动 write_stream 一条 todo_start，并在 session state 写入
        # _last_query_intent 让 ExecutionLimitRail 在 cascade resume 后跳过重发。
        from .execution_limit_rail import QUERY_INTENT_TO_TODO_TEXT
        query_intent = tool_args.get("query_intent", "")
        todo_map = QUERY_INTENT_TO_TODO_TEXT.get(query_intent)
        if todo_map:
            ctx.session.update_state({"_last_query_intent": query_intent})
            try:
                await ctx.session.write_stream(OutputSchema(
                    type="todo_start",
                    index=0,
                    payload={
                        "content": todo_map["todo_start"],
                        "id": query_intent,
                        "title": todo_map["todo_start"],
                    },
                ))
                logger.info(
                    f"[VersatileInterruptRail] todo_start 已发送（首次拦截前）："
                    f"intent={query_intent}, content={todo_map['todo_start']!r}"
                )
            except Exception as exc:
                logger.warning(
                    f"[VersatileInterruptRail] todo_start write_stream 失败："
                    f"intent={query_intent}, err={exc!r}"
                )

        ctx.session.update_state({
            "pending_delegate": {
                "intent": delegate_info["intent"],
                "task_description": delegate_info["task_description"],
            },
            "pending_tool_context": {
                "tool_name": tool_name,
                "tool_args": tool_args,
            },
        })

        return self.interrupt(
            InterruptRequest(
                message=f"执行{delegate_info['intent']}，等待 Orchestrator Cascade 续轮"
            )
        )

    async def _handle_cascade_resume(self, ctx, tool_name: str, tool_args: dict, cascade_result):
        tool_context = ctx.session.get_state("pending_tool_context") or {}
        ctx.session.update_state({"pending_tool_context": None})

        tool_args = tool_context.get("tool_args", tool_args) or {}
        business_data = self._extract_business_data(cascade_result)
        command = tool_args.get("query_response_analysis_scripts", "")
        skill_input = self._build_skill_input(tool_args, business_data)
        logger.info(f"skill_input:{skill_input}")
        status, normalized = await self._sandbox_normalize(command, skill_input, business_data, ctx)

        response_template_keys_str = tool_args.get("response_template_keys", "")
        # 脚本主动返回 ui_notice 时，表示该轮话术由 ui_notice 接管，
        # 不再走 response_template_keys 二选一逻辑，避免两条话术同时北向输出。
        has_ui_notice = isinstance(normalized, dict) and isinstance(normalized.get("ui_notice"), dict)
        if response_template_keys_str and status and self._scripts_config and not has_ui_notice:
            try:
                response_template_keys = json.loads(response_template_keys_str) if isinstance(response_template_keys_str, str) else response_template_keys_str
                if isinstance(response_template_keys, list):
                    key_index = 0 if status == "success" else 1
                    if key_index < len(response_template_keys):
                        template_key = response_template_keys[key_index]
                        template_text = self._scripts_config.get_response_template(template_key)
                        if template_text:
                            ctx.session.update_state({"response_template": template_text})
                        else:
                            logger.warning("[VersatileInterruptRail] response_template 处理异常：key=%r, status=%r, keys=%r", template_key, status, response_template_keys_str[:120])
                    else:
                        logger.warning("[VersatileInterruptRail] response_template 处理异常：key=%r, status=%r, keys=%r", None, status, response_template_keys_str[:120])
                else:
                    logger.warning("[VersatileInterruptRail] response_template 处理异常：key=%r, status=%r, keys=%r", None, status, response_template_keys_str[:120])
            except (json.JSONDecodeError, ValueError):
                logger.warning("[VersatileInterruptRail] response_template 处理异常：key=%r, status=%r, keys=%r", None, status, response_template_keys_str[:120])


        # 非中断话术提示：脚本可在 normalized 中注入 ui_notice，
        # 形如 {"event": "tool_end"|"todo_end", "key": "<scripts_key>"}。
        # rail 负责解析 key → 话术文本，并直接通过 session.write_stream
        # 以 tool_end 事件发到前端 SSE；同时从 tool_result 中 pop 掉，
        # 避免 LLM 看到内部字段。
        # 备注：之所以直接 write_stream 而非走 ExecutionLimitRail.after_tool_call
        # 的 tool_end 事件，是因为后者目前被注释掉，且 cascade-resume 流程下
        # openjiuwen Runner 不会向 agent.stream() 派发 tool_end 原始事件。
        if isinstance(normalized, dict) and "ui_notice" in normalized:
            ui_notice = normalized.pop("ui_notice", None)
            if isinstance(ui_notice, dict) and self._scripts_config is not None:
                notice_event = str(ui_notice.get("event", "")).strip()
                notice_key = str(ui_notice.get("key", "")).strip()
                notice_text = self._scripts_config.get_response_template(notice_key) if notice_key else ""
                if notice_event and notice_text:
                    try:
                        await ctx.session.write_stream(OutputSchema(
                            type=notice_event,
                            index=0,
                            payload={
                                "content": notice_text,
                                "plugin": tool_name or "call_versatile",
                                "data": {},
                            },
                        ))
                        logger.info(
                            "[VersatileInterruptRail] ui_notice 直发：event=%r, key=%r, text=%r",
                            notice_event, notice_key, notice_text,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[VersatileInterruptRail] ui_notice write_stream 失败：%r",
                            exc,
                        )
                else:
                    logger.warning(
                        "[VersatileInterruptRail] ui_notice 丢弃：event=%r, key=%r, has_text=%s",
                        notice_event, notice_key, bool(notice_text),
                    )

        # 方案 A 配套：cascade resume 完成时设置 flag，让本 rail 的 after_tool_call
        # hook 在 ExecutionLimitRail 发完 tool_start 之后再发 todo_end，
        # 保证事件序：todo_start → tool_start → todo_end（标准生命周期）。
        # 之所以不在这里直接 write_stream(todo_end)，是因为本 hook（before_tool_call）
        # 比 ExecutionLimitRail.before_tool_call 先跑（priority 90 > 40），
        # 那样 todo_end 会出现在 tool_start 之前。
        ctx.session.update_state({"_versatile_cascade_just_resumed": True})

        logger.info(
            f"[VersatileInterruptRail] Cascade 续轮：command={command!r}, "
            f"status={status!r}, "
            f"result_keys={list(normalized.keys()) if isinstance(normalized, dict) else type(normalized)}"
        )
        return self.reject(tool_result=normalized)

    async def after_tool_call(self, ctx) -> None:
        """cascade resume 完成后发 todo_end 收尾（与 before_tool_call 的 todo_start 配对）。

        触发条件：``_versatile_cascade_just_resumed`` 为 True（仅 cascade resume 路径设置）。
        interrupt 路径上虽然 openjiuwen 也会调 after_tool_call，但那时 flag 不存在，
        本方法直接 no-op，避免提前发 todo_end。
        """
        if not ctx.session.get_state("_versatile_cascade_just_resumed"):
            return
        ctx.session.update_state({"_versatile_cascade_just_resumed": None})

        last_intent = ctx.session.get_state("_last_query_intent")
        if not last_intent:
            return
        ctx.session.update_state({"_last_query_intent": None})

        from .execution_limit_rail import QUERY_INTENT_TO_TODO_TEXT
        todo_map = QUERY_INTENT_TO_TODO_TEXT.get(last_intent)
        if not todo_map:
            return

        try:
            await ctx.session.write_stream(OutputSchema(
                type="todo_end",
                index=0,
                payload={
                    "content": todo_map["todo_end"],
                    "id": last_intent,
                    "status": "done",
                },
            ))
            logger.info(
                f"[VersatileInterruptRail] todo_end 已发送（after_tool_call 收尾）："
                f"intent={last_intent}, content={todo_map['todo_end']!r}"
            )
        except Exception as exc:
            logger.warning(
                f"[VersatileInterruptRail] todo_end write_stream 失败："
                f"intent={last_intent}, err={exc!r}"
            )

    @staticmethod
    def _normalize_tool_args(tool_args, tool_name: Optional[str]) -> dict:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _apply_pre_delegate_guard(self, ctx, tool_args: dict):
        # 在真正写入 pending_delegate 之前先检查 skill 声明的前置规则。
        # 这样超限时可以直接终止当前 ReAct 流程，避免 Orchestrator 继续执行真实转账。
        command = tool_args.get("query_response_analysis_scripts", "")
        guard = self._load_pre_delegate_guard(command)

        for rule in guard.get("rules", []):
            match = rule.get("match", {})
            # match 表示这条规则只对哪些 tool_args 生效，例如 {"query_intent": "快速转账"}。
            if not match or any(tool_args.get(key) != value for key, value in match.items()):
                continue

            rule_id = rule.get("id", "default")
            state_key = f"_pre_delegate_guard:{command}:{rule_id}"
            count = int(ctx.session.get_state(state_key) or 0) + 1
            ctx.session.update_state({state_key: count})

            max_calls = int(rule.get("max_calls") or 0)
            logger.info(
                f"[VersatileInterruptRail] pre-delegate guard matched: "
                f"rule={rule_id!r}, count={count}, limit={max_calls}, match={match!r}"
            )
            if max_calls and count > max_calls:
                template_key = rule.get("response_template_key", "")
                text = (
                    self._scripts_config.get_response_template(template_key)
                    if self._scripts_config is not None and template_key
                    else ""
                ) or str(rule.get("fallback_message", "") or "")
                # response_template 由 agent.py 原有流结束逻辑北向输出；
                # request_force_finish 用于让 ReAct 循环停止，reject 用于跳过本次工具调用。
                ctx.session.update_state({
                    "response_template": text,
                    "pending_delegate": None,
                    "pending_tool_context": None,
                    "_last_query_intent": None,
                })
                if hasattr(ctx, "request_force_finish"):
                    ctx.request_force_finish({
                        "result_type": "interrupt",
                        "state": [],
                        "interrupt_ids": [],
                    })
                logger.warning(
                    f"[VersatileInterruptRail] pre-delegate guard exceeded: "
                    f"rule={rule_id!r}, count={count}, limit={max_calls}, message={text!r}"
                )
                return self.reject(tool_result={"status": "failed", "message": text})

            logger.info(
                f"[VersatileInterruptRail] pre-delegate guard passed: "
                f"rule={rule_id!r}, count={count}, limit={max_calls}"
            )

        return None

    def _load_pre_delegate_guard(self, command: str) -> dict:
        # query_response_analysis_scripts 是实际沙箱执行命令，这里只从中取出脚本文件名，
        # 静态读取 PRE_DELEGATE_GUARD 配置，不 import、不执行脚本。
        script = next((part for part in command.split() if part.endswith(".py")), "")
        if not script:
            logger.info(
                f"[VersatileInterruptRail] pre-delegate guard skipped: no script in command={command!r}"
            )
            return {}

        skills_dir = (Path(__file__).resolve().parent.parent / "skills").resolve()
        script_path = (skills_dir / script).resolve()
        try:
            script_path.relative_to(skills_dir)
        except ValueError:
            logger.warning(
                f"[VersatileInterruptRail] pre-delegate guard skipped: script outside skills dir, "
                f"script={script!r}, path={str(script_path)!r}"
            )
            return {}
        if not script_path.is_file():
            logger.info(
                f"[VersatileInterruptRail] pre-delegate guard skipped: script not found, path={str(script_path)!r}"
            )
            return {}

        tree = ast.parse(script_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PRE_DELEGATE_GUARD":
                        guard = ast.literal_eval(node.value)
                        if isinstance(guard, dict):
                            logger.info(
                                f"[VersatileInterruptRail] pre-delegate guard loaded: "
                                f"path={str(script_path)!r}, rules={len(guard.get('rules', []))}"
                            )
                            return guard
                        logger.warning(
                            f"[VersatileInterruptRail] pre-delegate guard ignored: "
                            f"PRE_DELEGATE_GUARD is not dict, path={str(script_path)!r}"
                        )
                        return {}

        logger.info(
            f"[VersatileInterruptRail] pre-delegate guard skipped: PRE_DELEGATE_GUARD not found, "
            f"path={str(script_path)!r}"
        )
        return {}

    @staticmethod
    def _build_skill_input(tool_args: dict, business_data: dict) -> dict:
        """构造传给沙箱脚本的 SKILL_INPUT JSON。

        基础字段：query_intent, query_description, business_data。
        skill_context：从 tool_args 中提取，解码后合并到 SKILL_INPUT（不覆盖基础字段）。
        mcp_products_data：从 session state 读取的 MCP 推荐结果（读一次即消费）。
        is_first_recommend：兜底场景判定标志，优先使用 skill_context 中的显式值，否则自动推断。
        """
        base = {
            "query_intent": tool_args.get("query_intent", ""),
            "query_description": tool_args.get("query_description", ""),
            "business_data": business_data,
        }

        notice_context_str = tool_args.get("notice_context", "")
        if notice_context_str:
            # 透传到脚本，由脚本解析 JSON；不在 rail 层解析以保证双向透明。
            base["notice_context"] = notice_context_str

        return base

    async def _sandbox_normalize(
        self, command: str, skill_input: dict, fallback: dict, ctx=None,
    ):
        if not command:
            return None, fallback

        if not self._sys_operation_id:
            logger.warning(
                "[VersatileInterruptRail] 未配置 sys_operation_id，跳过沙箱归一化：command={!r}",
                command,
            )
            return None, fallback

        from openjiuwen.core.runner import Runner

        sys_op = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        if sys_op is None:
            logger.warning(
                "[VersatileInterruptRail] 未找到 SysOperationCard，跳过沙箱归一化：sys_operation_id={!r}",
                self._sys_operation_id,
            )
            return None, fallback

        skills_dir = _get_skills_dir()

        exec_result = await sys_op.shell().execute_cmd(
            command=f'cd "{skills_dir}" && {command}',
            timeout=_SANDBOX_TIMEOUT,
            environment={"SKILL_INPUT": json.dumps(skill_input, ensure_ascii=False)},
        )

        stdout = getattr(getattr(exec_result, "data", None), "stdout", "") or ""
        stderr = getattr(getattr(exec_result, "data", None), "stderr", "") or ""
        exit_code = getattr(getattr(exec_result, "data", None), "exit_code", None)

        try:
            parsed = json.loads(stdout.strip())
            if isinstance(parsed, list) and len(parsed) == 2 and isinstance(parsed[1], dict):
                return str(parsed[0]), parsed[1]
            if isinstance(parsed, dict):
                return None, parsed
            return None, fallback
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error(
                "[VersatileInterruptRail] 沙箱脚本输出解析失败：{}，exit_code={}, stdout={!r}, stderr={!r}",
                e,
                exit_code,
                stdout[:500],
                stderr[:500],
            )
            conv_id = getattr(getattr(ctx, "session", None), "session_id", "-") if ctx else "-"
            logger.error(
                f"[SCRIPT_FALLBACK] conv={conv_id} | "
                f"command={command!r:.80} | "
                f"exit_code={exit_code} | "
                f"error={e} | "
                f"stdout={stdout[:200]!r} | "
                f"stderr={stderr[:200]!r}"
            )
            return None, fallback

    @staticmethod
    def _extract_business_data(cascade_result) -> dict:
        if not isinstance(cascade_result, dict):
            return {}

        workflow_result = cascade_result.get("workflow_result")
        if workflow_result is not None:
            if isinstance(workflow_result, dict):
                return workflow_result
            if isinstance(workflow_result, str):
                try:
                    parsed = json.loads(workflow_result)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                return {"workflow_result": workflow_result}
            return cascade_result

        return {
            key: value
            for key, value in cascade_result.items()
            if key not in ("node_type", "node_name")
        }

    @staticmethod
    def _build_delegate(tool_name: Optional[str], tool_args: dict) -> dict:
        return {
             "intent": tool_args.get("query_intent", ""),
             "task_description": tool_args.get("query_description", ""),
        }

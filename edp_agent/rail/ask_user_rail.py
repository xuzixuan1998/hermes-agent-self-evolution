"""
AskUserRail：拦截 ask_user 工具调用，将命中话术写入 session.response_template
并以 InterruptRequest 收尾（彻底跳过 LLM 续写）。

执行路径：
  - LLM 调用 ask_user(question=..., response_template_keys=..., response_template_status=...,
                       response_template_vars=...)
  - 本 Rail 解析 keys（dict）→ 用 status 命中 key → 取话术模板 → 用 vars 做安全 format
  - 写 session.response_template；命中 confirm 时把 vars 落到 session.selected_product
  - return self.interrupt(...) 触发外层中断；agent.py 流末读 response_template 发 InterruptStartEvent

降级路径：
  - 若 response_template_keys 或 response_template_status 缺失，直接 return self.approve()
    让 ask_user 工具按原行为执行（返回 should_stop=True，由框架等待用户回复）。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from ..agent_rule import ScriptsConfig


class _SafeDict(dict):
    """str.format_map 兜底：缺失变量替换为空串。"""

    def __missing__(self, key):  # noqa: D401
        return ""


def _strip_wrapping_quotes(raw: str) -> str:
    """去掉 LLM 贴示例时残留的最外层成对单/双引号。"""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1].strip()
        if inner.startswith(("{", "[")):
            return inner
    return raw


def _coerce_text(raw: object) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if raw is None:
        return ""
    return str(raw).strip()


def _normalize_json_like_text(raw: str) -> str:
    return (
        raw.replace("“", '"')
        .replace("”", '"')
        .replace("＂", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("：", ":")
    )


def _extract_object_like_pairs(raw: str) -> dict[str, str]:
    """从非法 JSON 字符串中按 "key":"value" 形式抓键值对。

    值的结束以「引号 + 紧邻 , 或 }」为锚（lookahead），从而允许值内部含有
    未转义的引号（典型场景：商品名包含 " 或 '，导致整串非法 JSON）。
    """
    pairs: dict[str, str] = {}
    pattern = re.compile(
        r'["\']([A-Za-z0-9_]+)["\']\s*[:：]\s*["\'](.*?)["\']\s*(?=[,}]|$)',
        re.DOTALL,
    )
    for key, value in pattern.findall(raw):
        pairs[key] = value
    return pairs


def _coerce_dict_arg(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}

    text = _strip_wrapping_quotes(raw.strip())
    if not text:
        return {}

    candidates = [text]
    normalized = _normalize_json_like_text(text)
    if normalized != text:
        candidates.append(normalized)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    return _extract_object_like_pairs(normalized)


class AskUserRail(BaseInterruptRail):
    """拦截 ask_user：当 LLM 提供话术参数时改走话术中断路径，否则放行原工具。"""

    def __init__(self, scripts_config: Optional[ScriptsConfig] = None) -> None:
        super().__init__(tool_names=["ask_user"])
        self._scripts_config = scripts_config

    async def resolve_interrupt(
        self,
        ctx,
        tool_call,
        user_input,
        auto_confirm_config=None,
    ):
        # ── Resume 路径：用户回复了上一轮中断 ────────────────────────────
        # 框架在 resume 时重放原 tool_call（参数仍是上次的 status/vars），
        # 并把用户输入塞进 user_input。这里必须 reject(tool_result=...)
        # 把用户回复作为 ask_user 的执行结果回给 LLM，LLM 才能在下一步
        # ReAct 中基于「上文产品 + 本次回复」重新规划，调出新的 ask_user
        # （比如从 missing_amount → confirm）。
        # 否则 rail 会拿同一份 tool_args 反复走模板逻辑 → 死循环重发同一句话术。
        if user_input is not None:
            logger.info(
                "[AskUserRail] Resume：把用户回复作为 tool_result 回给 LLM：user_input={!r:.120}",
                user_input,
            )
            return self.reject(
                tool_result={
                    "status": "user_responded",
                    "user_response": user_input,
                }
            )

        # ── 首次拦截：按话术参数生成中断 ────────────────────────────────
        tool_args = ctx.inputs.tool_args or {}
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(_strip_wrapping_quotes(tool_args.strip()))
            except (json.JSONDecodeError, ValueError):
                tool_args = {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        keys_raw = tool_args.get("response_template_keys")
        status = _coerce_text(tool_args.get("response_template_status"))
        vars_raw = tool_args.get("response_template_vars")
        keys_map = _coerce_dict_arg(keys_raw)

        # 降级：未提供话术参数 → 放行原 ask_user 行为
        if not keys_map or not status or self._scripts_config is None:
            logger.debug(
                "[AskUserRail] 降级放行：keys_count={}, status={!r}, has_scripts_config={}",
                len(keys_map),
                status,
                self._scripts_config is not None,
            )
            return self.approve()

        if not isinstance(keys_map, dict):
            logger.warning(
                "[AskUserRail] response_template_keys 类型异常（期望 dict）：type={}, raw={!r}",
                type(keys_raw).__name__,
                _coerce_text(keys_raw)[:200],
            )
            return self.approve()

        template_key = keys_map.get(status)
        if not template_key:
            logger.warning(
                "[AskUserRail] status={!r} 未在 keys_map 命中：keys={!r}",
                status,
                list(keys_map.keys()),
            )
            return self.approve()

        template_text = self._scripts_config.get_response_template(template_key, "")
        if not template_text:
            logger.warning(
                "[AskUserRail] 未找到话术模板：key={!r}",
                template_key,
            )
            return self.approve()

        # 解析 vars 并 safe-format
        vars_dict = _coerce_dict_arg(vars_raw)
        if vars_raw and not vars_dict:
            logger.warning(
                "[AskUserRail] response_template_vars 解析失败：raw={!r}",
                _coerce_text(vars_raw)[:200],
            )

        try:
            text = template_text.format_map(_SafeDict(vars_dict))
        except Exception as e:  # 模板里出现非法 format 字段时兜底
            logger.warning(
                "[AskUserRail] 话术 format 失败：err={}, template={!r}, vars={!r}",
                e,
                template_text[:200],
                vars_dict,
            )
            text = template_text

        # 写入 session 状态：response_template 由 agent.py 流末发 InterruptStartEvent
        state_update = {"response_template": text}
        if status == "confirm":
            state_update["selected_product"] = vars_dict
        ctx.session.update_state(state_update)

        logger.info(
            "[AskUserRail] 命中话术：status={!r}, key={!r}, text={!r:.120}, vars_keys={!r}",
            status,
            template_key,
            text,
            list(vars_dict.keys()),
        )

        return self.interrupt(
            InterruptRequest(message=f"ask_user 话术中断（status={status}）")
        )

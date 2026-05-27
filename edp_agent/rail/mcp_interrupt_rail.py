"""
MCPInterruptRail：拦截 call_mcp 工具调用，执行脚本，存储结果到 session state。

通用化设计：
  - 脚本命令由 LLM 通过 script_command 参数动态传入（不再硬编码）
  - 业务参数由 LLM 通过 script_params 参数传入（JSON 字符串）
  - mcp_required_params 由 Rail 从 session.state["original_body"] 自动读取（不经过 LLM，避免篡改）
  - env_vars 由 LLM 通过 script_params 传入需要的环境变量 key 列表，
    Rail 根据这些 key 从 os.environ 读取实际值注入环境变量

设计原则：
  - 零 A2A 依赖：不引用 EventQueue、A2AClient 等任何 A2A 对象
  - 不走 interrupt/cascade 路径：脚本调用不需要经过低码平台
  - 执行后直接 reject(tool_result=...)：简化执行路径

数据流：
  resolve_interrupt()
    → 从 tool_args 提取 script_command, script_params
    → 解码 script_params 为 dict
    → 合并 script_params + mcp_required_params（Rail 从 session state 读取） → 构造 SKILL_INPUT JSON
    → 从 script_params 中提取 env_vars key 列表 → 从 os.environ 读取值 → 构造环境变量
    → sys_op.shell().execute_cmd() 执行 script_command
    → 解析结果 → session.update_state({"mcp_products_data": <parsed_json>})
    → reject(tool_result=<parsed_json>)

session state 写入时序：
  update_state() → reject()，与 VersatileInterruptRail 模式一致。
  reject() 不会回滚 session state。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail

from ..config import get_settings
from ..agent_rule import ScriptsConfig

_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(_ENV_FILE)

_SANDBOX_TIMEOUT = 60  # 外层超时：兜底整个执行进程


def _get_skills_dir() -> Path:
    """根据运行模式返回 skills 目录路径（沙箱模式用容器内路径，本地模式用项目路径）。"""
    settings = get_settings()
    sandbox_url = (settings.sandbox_url or "").strip()
    if sandbox_url:
        target = (settings.skill_target_path or "/tmp").strip() or "/tmp"
        return Path(target) / "skills"
    return Path(__file__).resolve().parent.parent / "skills"


def _resolve_env_vars(env_var_keys: Optional[List[str]] = None) -> Dict[str, str]:
    """根据 LLM 传入的环境变量 key 列表，从 os.environ 读取实际值。
    LLM 只传入 key 名称（如 ["MCP_MASTER_URL", "MCP_ACCESS_TOKEN"]），
    Rail 负责从环境变量中读取对应的值注入沙箱。
    """
    logger.info(f"[MCPInterruptRail] env_var_keys: {env_var_keys}")
    if not env_var_keys:
        return {}
    result = {}
    for key in env_var_keys:
        if isinstance(key, str) and key.strip():
            result[key.strip()] = os.environ.get(key.strip(), "")
            logger.info(f"[MCPInterruptRail] env_var: {key.strip()}={result[key.strip()]}")
    return result


class MCPInterruptRail(BaseInterruptRail):
    """拦截 call_mcp 工具调用，执行脚本，结果写入 session state。

    参数来源：
      - script_command：LLM 传入，指定要执行的脚本命令
      - script_params：LLM 传入，业务参数 JSON（含 env_vars key 列表）
      - mcp_required_params：Rail 从 session.state["original_body"] 读取，不经过 LLM
    """

    def __init__(self, sys_operation_id: Optional[str] = None, scripts_config: Optional[ScriptsConfig] = None) -> None:
        super().__init__(tool_names=["call_mcp"])
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
        logger.info(f"[MCPInterruptRail] tool_args: {tool_args}")

        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {}

        script_command = tool_args.get("script_command", "")
        script_params_str = tool_args.get("script_params", "")
        logger.info(f"[MCPInterruptRail] script_command: {script_command}")
        logger.info(f"[MCPInterruptRail] script_params: {script_params_str}")


        if not script_command:
            error_result = self._build_error_result({}, "script_command is empty")
            ctx.session.update_state({"mcp_products_data": error_result})
            return self.reject(tool_result=error_result)

        # 分离 env_vars、empty_result_template_key 和业务参数
        script_params, env_var_keys, empty_result_template_key = self._parse_script_params(script_params_str)
        skill_input = self._build_skill_input(script_params, ctx)
        sandbox_env = self._build_sandbox_env(skill_input, env_var_keys)
        mcp_result = await self._execute_sandbox(script_command, sandbox_env)

        no_result_tip = True
        if mcp_result is not None:
            if isinstance(mcp_result, dict) and mcp_result.get('total', 0) != 0:
                no_result_tip = False

            # ── 缓存 versatile_query 到 session，供后续 call_versatile 的 mcp_to_versatile_information 使用──
            if isinstance(mcp_result, dict) and mcp_result.get("versatile_query"):
                mcp_to_versatile_information = mcp_result["versatile_query"]
                ctx.session.update_state({"mcp_to_versatile_information": mcp_to_versatile_information})
                logger.info(
                    f"[MCPInterruptRail] versatile_query 已缓存到 session：{mcp_to_versatile_information}"
                )

            logger.info(
                f"[MCPInterruptRail] 脚本结果已写入 session state："
                f"products={mcp_result.get('total', 0) if isinstance(mcp_result, dict) else 'N/A'}, "
                f"mcp_error={mcp_result.get('mcp_error') if isinstance(mcp_result, dict) else None}"
            )
        else:
            error_result = self._build_error_result(skill_input, "sandbox execution failed")
            logger.warning("[MCPInterruptRail] 沙箱执行失败，写入空结果到 session state")

        if no_result_tip:
            tip_key = empty_result_template_key
            tip_text = self._scripts_config.get_response_template(tip_key)
            logger.info(f"[MCPInterruptRail] tip_key:{tip_key}, tip_text:{tip_text}")
            if tip_text:
                ctx.session.update_state({"response_template": tip_text})

        return self.reject(tool_result=mcp_result if mcp_result is not None else error_result)

    @staticmethod
    def _parse_script_params(script_params_str: str) -> tuple:
        """解码 script_params，分离 env_vars、empty_result_template_key 和业务参数。

        Returns:
            (script_params_dict, env_var_keys_list, empty_result_template_key_str)
        """
        params = {}
        env_var_keys = []
        empty_result_template_key = ""

        if script_params_str:
            try:
                parsed = json.loads(script_params_str)
                if isinstance(parsed, dict):
                    params = parsed
                else:
                    logger.warning(
                        f"[MCPInterruptRail] script_params 解码后非 dict（type={type(parsed).__name__}），使用空 dict"
                    )
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.warning("[MCPInterruptRail] script_params JSON 解码失败，使用空 dict")

        # 提取 env_vars key 列表，从 params 中移除（不传入 SKILL_INPUT）
        env_var_keys = params.pop("env_vars", []) or []
        if not isinstance(env_var_keys, list):
            logger.warning(
                f"[MCPInterruptRail] env_vars 应为 list，实际为 {type(env_var_keys).__name__}，忽略"
            )
            env_var_keys = []

        # 提取 empty_result_template_key，从 params 中移除（不传入 SKILL_INPUT）
        empty_result_template_key = params.pop("empty_result_template_key", "") or ""
        if not isinstance(empty_result_template_key, str):
            logger.warning(
                f"[MCPInterruptRail] empty_result_template_key 应为 str，实际为 {type(empty_result_template_key).__name__}，忽略"
            )
            empty_result_template_key = ""

        return params, env_var_keys, empty_result_template_key

    @staticmethod
    def _build_skill_input(script_params: dict, ctx) -> dict:
        """合并业务参数和 Rail 自动读取的 mcp_required_params。

        mcp_required_params 从 session.state["original_body"] 读取，
        作为 string 直接注入 SKILL_INPUT，不经过 LLM。
        """
        base = dict(script_params)

        # mcp_required_params 从 session state 读取，LLM 无法获取或篡改
        auth_info = ctx.session.get_state("original_body")
        logger.info(f"[MCPInterruptRail] auth_info: {auth_info}")

        if auth_info is not None:
            base["mcp_required_params"] = str(auth_info)

        return base

    @staticmethod
    def _build_sandbox_env(skill_input: dict, env_var_keys: List[str]) -> Dict[str, str]:
        """构造沙箱环境变量：SKILL_INPUT 包含 env_vars，LLM 指定的 env_vars 从 os.environ 读取后注入 SKILL_INPUT。"""
        # 根据 LLM 传入的 key 列表，从 os.environ 读取实际值
        resolved = _resolve_env_vars(env_var_keys)

        # 将环境变量放入 skill_input 的 env_vars 中
        skill_input_with_env = dict(skill_input)
        if resolved:
            skill_input_with_env["env_vars"] = resolved

        sandbox_env = {
            "SKILL_INPUT": json.dumps(skill_input_with_env, ensure_ascii=False),
        }
        logger.info(f"[MCPInterruptRail] sandbox_env: {sandbox_env}")
        return sandbox_env

    @staticmethod
    def _build_error_result(skill_input: dict, reason: str) -> dict:
        """构造统一格式的错误结果。

        MCP 调用失败时清空 history_product_codes 和 updated_recommend_params，
        使下次调用不继承上次条件。
        """
        return {
            "products": [],
            "total": 0,
            "next_sort_type": 0,
            "updated_recommend_params": {},
            "history_product_codes": [],
            "mcp_error": reason,
        }

    async def _execute_sandbox(self, script_command: str, sandbox_env: Dict[str, str]) -> Optional[dict]:
        """在沙箱中执行脚本，返回 JSON 结果。"""
        from openjiuwen.core.runner import Runner

        sys_op = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        if sys_op is None:
            logger.warning(
                "[MCPInterruptRail] 未找到 SysOperationCard，跳过沙箱执行：sys_operation_id={!r}",
                self._sys_operation_id,
            )
            return None

        skills_dir = _get_skills_dir()

        logger.info(
            f"[MCPInterruptRail] 执行脚本：cd {skills_dir} && {script_command}，"
            f"env_keys={list(sandbox_env.keys())}"
        )

        try:
            exec_result = await sys_op.shell().execute_cmd(
                command=f'cd "{skills_dir}" && {script_command}',
                timeout=_SANDBOX_TIMEOUT,
                environment=sandbox_env,
            )
            logger.info(f"[MCPInterruptRail] 沙箱执行结果：{exec_result}")

            stdout = getattr(getattr(exec_result, "data", None), "stdout", "") or ""
            stderr = getattr(getattr(exec_result, "data", None), "stderr", "") or ""
            exit_code = getattr(getattr(exec_result, "data", None), "exit_code", None)

            stdout = stdout.strip()
            if not stdout:
                logger.error(
                    f"[MCPInterruptRail] 沙箱脚本输出为空，exit_code={exit_code}, stderr={stderr[:200]}"
                )
                return None
            return json.loads(stdout)
        except json.JSONDecodeError as e:
            logger.error(f"[MCPInterruptRail] 沙箱脚本输出解析失败：{e}")
            return None
        except Exception as e:
            logger.error(f"[MCPInterruptRail] 沙箱脚本执行异常：{e}")
            return None

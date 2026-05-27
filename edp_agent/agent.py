"""
EDPAgent 唯一公开入口（对齐需求文档 §4.5 的 17 种事件序列）。

公开接口：
  - initialize_dpa()  — 应用启动时调用一次，配置 Runner 和 ReActAgent
  - agent_stream()    — 每次用户请求时调用，流式返回 AgentEvent

零 A2A 依赖。
"""
from __future__ import annotations

import json
from cmath import cos
from pathlib import Path
import tempfile
import zipfile
from typing import Any, AsyncGenerator, Optional

from loguru import logger
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
import uuid
from datetime import datetime
import time
import httpx

from .agent_rule import AgentRuleConfig, load_agent_rule
from .config import get_settings
from .prompt import build_system_prompt
from common.events import (
    AgentEvent,
    ConversationStartEvent, ConversationEndEvent,
    ThinkStartEvent, ThinkChunkEvent, ThinkEndEvent,
    TodoListStartEvent, TodoListItemEvent, TodoListEndEvent,
    TodoStartEvent, TodoStatusEvent, TodoEndEvent,
    ToolStartEvent, ToolStatusEvent, ToolEndEvent,
    InterruptStartEvent, InterruptEndEvent,
    FinalAnswerStartEvent, SummaryEvent, FinalAnswerChunkEvent, FinalAnswerEndEvent,
    DelegateRequest,
)
from common.logger import (
    Extra,
    Tag,
    bind_context,
    build_http_request_tag_context,
    build_http_trace,
    to_logger,
    get_real_ip, Level, ResultEnum,TagObservation,ObservationType
)

def _log_stream_payload(evt: AgentEvent) -> None:
    """在每次 yield AgentEvent 前打一条日志（对齐抓包的 stream payload 行）。"""
    event_type = getattr(evt, "type", "<unknown>")
    content = getattr(evt, "content", "") or ""
    # 截断过长 content 避免刷屏
    preview = content if len(content) <= 120 else content[:117] + "..."
    logger.info(f"[EDPAgent] stream payload [{event_type}]: {preview}")


# ── LLM sampling 默认覆盖（修复 glm-5 偶发空响应）────────────────────────
# openjiuwen 0.1.11 ModelRequestConfig 默认 temperature=0.95 / top_p=0.1，对
# reasoning 模型（glm-5/DeepSeek-R1/Qwen3-Thinking）会偶发只产 reasoning_content
# 不产 content（finish_reason 异常、content 为空）。这里硬编码到对 reasoning
# 模型友好的值；不走 env 配置以保证容器内任何部署都拿到一致行为。
# 排查时：grep "[EDP-LLM-CONFIG]" 验证启动覆盖；grep "[EDP-LLM-EMPTY]" 抓空响应。
_LLM_TEMPERATURE_OVERRIDE = 0.3
_LLM_TOP_P_OVERRIDE = 0.95
_LLM_MAX_RETRIES = 0

def _apply_sampling_overrides(config: Any) -> None:
    """把硬编码的 sampling 值落到 ReActAgentConfig.model_config_obj 上。

    必须在 configure_model_client(...) 之后、agent.configure(config) 之前调用。
    """
    obj = getattr(config, "model_config_obj", None)
    if obj is None:
        logger.warning(
            "[EDP-LLM-CONFIG] model_config_obj is None; "
            "sampling override SKIPPED (configure_model_client 可能未先调用)"
        )
        return
    obj.temperature = _LLM_TEMPERATURE_OVERRIDE
    obj.top_p = _LLM_TOP_P_OVERRIDE
    logger.info(
        f"[EDP-LLM-CONFIG] applied sampling override: "
        f"temperature={obj.temperature} top_p={obj.top_p}"
    )

    mcc = getattr(config, "model_client_config", None)
    if mcc is None:
        logger.warning(
            "[EDP-LLM-CONFIG] model_config_obj is None; "
            "sampling override SKIPPED (model_client_config 可能未先调用)"
        )
        return    
    mcc.max_retries = _LLM_MAX_RETRIES
    logger.info(
        f"[EDP-LLM-CONFIG] model_client_config override: "
        f"max_retries={mcc.max_retries}"
    )

# ── 模块级单例 ──────────────────────────────────────────────────────────
_agent = None
_agent_rule: Optional[AgentRuleConfig] = None

_AGENT_RULE_PATH = Path(__file__).parent / "AgentRule.md"


def _zip_skills_dir_to_file(skills_root: Path, out_zip: Path) -> None:
    if not skills_root.exists():
        raise FileNotFoundError(f"skills 目录不存在：{skills_root}")
    if not skills_root.is_dir():
        raise NotADirectoryError(f"skills 不是目录：{skills_root}")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()

    # 让解压后得到 <target>/skills/... 结构
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in skills_root.rglob("*"):
            if p.is_dir():
                continue
            rel = p.relative_to(skills_root)
            zf.write(p, arcname=str(Path("skills") / rel))


# ════════════════════════════════════════════════════════════════════
# 公开接口
# ════════════════════════════════════════════════════════════════════


async def initialize_dpa() -> None:
    """应用启动时调用一次。"""
    global _agent, _agent_rule
    if _agent is not None:
        logger.debug("[DPA] 已初始化，跳过重复初始化")
        return

    settings = get_settings()

    import openjiuwen.extensions.checkpointer.redis.checkpointer  # noqa: F401

    from openjiuwen.core.runner import Runner
    from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG
    from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig
    from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
    from openjiuwen.core.sys_operation import (
        LocalWorkConfig,
        OperationMode,
        SandboxGatewayConfig,
        SysOperationCard,
    )
    from openjiuwen.core.sys_operation.config import (
        ContainerScope,
        PreDeployLauncherConfig,
        SandboxIsolationConfig,
    )

    from .rail import (
        VersatileInterruptRail,
        IterationLimitRail,
        ExecutionLimitRail,
        MCPInterruptRail,
        AskUserRail,
        CancelRail,
        LogRail,
    )

    # ── 加载 AgentRule.md ────────────────────────────────────────────
    try:
        _agent_rule = load_agent_rule(_AGENT_RULE_PATH)
        system_prompt = _agent_rule.markdown_body
        logger.info(
            f"[DPA] AgentRule 加载成功：body={len(system_prompt)} 字符, "
            f"scope='{_agent_rule.scope.allowed[:30]}...', "
            f"max_iter={_agent_rule.limits.max_iterations}, "
            f"task_limits={_agent_rule.limits.tasks}"
        )
    except FileNotFoundError:
        _agent_rule = AgentRuleConfig()
        system_prompt = "你是一个基金理财智能体。"
        logger.warning("[DPA] AgentRule.md 未找到，使用默认配置")

    system_prompt = f"{system_prompt.strip()}\n\n{build_system_prompt().strip()}"

    # ── 配置 lite_todo todolist_steps（必须在 build_tools() 之前）─────
    from .tool.lite_todo.models import configure_steps
    configure_steps(_agent_rule.todolist_steps)
    logger.info(
        f"[DPA] todolist_steps 已配置：{len(_agent_rule.todolist_steps)} 项 "
        f"step_ids={[s.step_id for s in _agent_rule.todolist_steps]}"
    )

    # 现在 lite_todo schema 已就绪，可安全构建 TOOLS
    from .tool import build_tools
    TOOLS = build_tools()

    # ── 配置 Redis Checkpointer ──────────────────────────────────────
    runner_config = DEFAULT_RUNNER_CONFIG.model_copy(deep=True)
    runner_config.checkpointer_config = CheckpointerConfig(
        type="redis",
        conf={
            "connection": {
                "url": settings.redis_url,
                "connection_args": {"protocol": 2}, # 兼容 Redis < 6.0
            },
            "ttl": {
                "default_ttl": settings.redis_checkpointer_ttl_minutes,
                "refresh_on_read": True,
            },
        },
    )
    Runner.set_config(runner_config)
    await Runner.start()
    logger.info("[DPA] Runner 已启动，Checkpointer=redis")

    # ── 注册 SysOperationCard（local / sandbox）────────────────────────────
    # 判定规则：只要配置了 SANDBOX_URL 就走沙箱；未配置则默认 local
    sandbox_url = (settings.sandbox_url or "").strip()
    run_mode = "sandbox" if sandbox_url else "local"

    local_zip_for_upload: str | None = None
    if run_mode == "sandbox":
        # 沙箱模式固定从本地 skills/ 打包一个 zip 上传（不再支持 SKILL_PACKAGE_PATH）
        skills_root = Path(__file__).resolve().parent / "skills"
        tmpdir = Path(tempfile.mkdtemp(prefix="edpagent-skills-"))
        auto_zip = tmpdir / "skills.zip"
        _zip_skills_dir_to_file(skills_root, auto_zip)
        local_zip_for_upload = str(auto_zip)
        logger.info(f"[DPA] 已自动打包 skills.zip：{local_zip_for_upload}")
        target = (settings.skill_target_path or "/tmp").strip() or "/tmp"

        # 1) 创建沙箱
        async with httpx.AsyncClient(base_url=sandbox_url, timeout=30.0) as client:
            create_resp = await client.post("/api/v1/sandboxes", json={})
            create_resp.raise_for_status()
            sandbox_id = create_resp.json()["id"]

        sys_op_id = f"jiuwenbox_fs_op_{uuid.uuid4().hex[:8]}"
        sysop_card = SysOperationCard(
            id=sys_op_id,
            mode=OperationMode.SANDBOX,
            gateway_config=SandboxGatewayConfig(
                isolation=SandboxIsolationConfig(
                    container_scope=ContainerScope.CUSTOM,
                    custom_id=sandbox_id,
                ),
                launcher_config=PreDeployLauncherConfig(
                    base_url=sandbox_url,
                    sandbox_type="jiuwenbox",
                    extra_params={"sandbox_id": sandbox_id},
                ),
                timeout_seconds=30,
            ),
        )
        Runner.resource_mgr.add_sys_operation(sysop_card)
        logger.info(
            f"[DPA] SysOperationCard 已注册：id={sysop_card.id}, sandbox_id={sandbox_id}"
        )
    else:
        sysop_card = SysOperationCard(
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir=None),
        )
        Runner.resource_mgr.add_sys_operation(sysop_card)
        logger.info(f"[DPA] SysOperationCard 已注册：id={sysop_card.id}, mode=local")

    # ── 创建 ReActAgent ──────────────────────────────────────────────
    card = AgentCard(id=settings.dpa_agent_id, name=settings.dpa_agent_name)
    agent = ReActAgent(card=card)
    config = ReActAgentConfig()

    # 自定义 header
    if settings.custom_headers:
        if hasattr(config, "configure_custom_headers"):
            config.configure_custom_headers(settings.custom_headers)
            logger.info(f"[DPA] 自定义请求头已配置：{list(settings.custom_headers.keys())}")
        else:
            logger.warning("[DPA] SDK 不支持 configure_custom_headers，header 未生效")

    config = (
        config.configure_model_client(
            provider=settings.llm_provider,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            model_name=settings.llm_model_name,
            verify_ssl=settings.llm_verify_ssl,
        )
        .configure_prompt_template([{"role": "system", "content": system_prompt}])
        .configure_max_iterations(_agent_rule.limits.max_iterations)
    )
    config.sys_operation_id = sysop_card.id

    # 覆盖 openjiuwen 0.1.11 不健康的 sampling 默认值（详见 _apply_sampling_overrides）
    _apply_sampling_overrides(config)

    agent.configure(config)

    if hasattr(config, "model_client_config") and config.model_client_config is not None:
        config.model_client_config.timeout = settings.llm_timeout

    # ── 开始注册能力组件（记录耗时）
    capability_start_time = datetime.now()
    capability_start_time_ms = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    registered_tools = []
    registered_sys_tools = []
    # ── 注册 read_file（Skill 按需读取 SKILL.md）──────────────────────
    read_file_card = Runner.resource_mgr.get_sys_op_tool_cards(
        sys_operation_id=sysop_card.id,
        operation_name="fs",
        tool_name="read_file",
    )
    if read_file_card is not None:
        agent.ability_manager.add(read_file_card)
        registered_sys_tools.append("read_file")
        logger.info("[DPA] read_file 已加入 Agent 能力集")
    else:
        logger.warning("[DPA] 未获取到 read_file 能力卡，Skill 将无法按需读取 SKILL.md")

    # ── 注册 shell_tool（Skill 按需执行脚本命令）──────────────────────
    shell_tool_card = Runner.resource_mgr.get_sys_op_tool_cards(
        sys_operation_id=sysop_card.id,
        operation_name="shell",
        tool_name="execute_cmd",
    )
    if shell_tool_card is not None:
        agent.ability_manager.add(shell_tool_card)
        registered_sys_tools.append("execute_cmd")
        logger.info("[DPA] shell_tool 已加入 Agent 能力集")
    else:
        logger.warning("[DPA] 未获取到 shell_tool 能力卡，Skill 将无法按需执行 shell 命令")

    # ── 注册工具 ─────────────────────────────────────────────────────
    for tool in TOOLS:
        Runner.resource_mgr.add_tool(tool)
        agent.ability_manager.add(tool.card)
        registered_tools.append(tool.card.name if hasattr(tool.card, 'name') else str(tool.card))

    # 记录工具初始化耗时
    capability_end_time = datetime.now()
    total_capability_duration_ms = int((capability_end_time - capability_start_time).total_seconds() * 1000)
    capability_end_time_ms = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    to_logger(
        level=Level.INFO, message=TagObservation(
            id=str(int(time.time() * 1000)),
            type=ObservationType.SPAN,
            name="agent初始化",
            start_time=capability_start_time_ms,
            end_time=capability_end_time_ms,
            input={"tool_list": registered_sys_tools + registered_tools},
        ),
        extra=Extra(tag=Tag.TAG_AGENT_INIT_TOOLLIST, cost=total_capability_duration_ms)
    )

    # ── 注册 Rails（顺序决定优先级影响）──────────────────────────────
    await agent.register_rail(IterationLimitRail(_agent_rule))
    await agent.register_rail(ExecutionLimitRail(_agent_rule))
    await agent.register_rail(CancelRail(scripts_config=_agent_rule.scripts))
    await agent.register_rail(MCPInterruptRail(sys_operation_id=sysop_card.id, scripts_config=_agent_rule.scripts))
    await agent.register_rail(VersatileInterruptRail(sys_operation_id=sysop_card.id, scripts_config=_agent_rule.scripts))
    await agent.register_rail(AskUserRail(scripts_config=_agent_rule.scripts))
    await agent.register_rail(LogRail(model_name=settings.llm_model_name, tools=TOOLS))

    # ── 注册 Skill（local: 本地；sandbox: 上传 zip → 解压 → register）─────
    skill_count = 0
    if run_mode == "sandbox":
        import openjiuwen.extensions.sys_operation.sandbox.providers
        target = (settings.skill_target_path or "/tmp").strip() or "/tmp"
        remote_zip = f"{target.rstrip('/')}/skills.zip"
        remote_skills_root = f"{target.rstrip('/')}/skills"

        sys_op = Runner.resource_mgr.get_sys_operation(sysop_card.id)
        if sys_op is None:
            raise RuntimeError(f"[DPA] 未找到 sys_operation: {sysop_card.id}")

        if not local_zip_for_upload:
            raise RuntimeError("[DPA] sandbox 模式未生成本地 skills.zip")
        upload_res = await sys_op.fs().upload_file(local_zip_for_upload, remote_zip)
        logger.info(f"[DPA] sandbox 上传 skill 包完成：{upload_res}")

        unzip_res = await sys_op.shell().execute_cmd(
            f"unzip -o {remote_zip} -d {target}"
        )
        logger.info(f"[DPA] sandbox 解压 skill 包完成：{unzip_res}")

        await agent.register_skill(remote_skills_root)
        skill_count = 1
        logger.info(f"[DPA] sandbox skills 已注册：{remote_skills_root}")
    else:
        skills_root = Path(__file__).resolve().parent / "skills"
        if skills_root.exists():
            for skill_dir in sorted(skills_root.iterdir()):
                if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                    await agent.register_skill(str(skill_dir))
                    skill_count += 1
        else:
            logger.warning(f"[DPA] 技能目录不存在：{skills_root}")

    _agent = agent
    logger.info(
        f"[DPA] 初始化完成：agent_id={settings.dpa_agent_id}，"
        f"已注册 4 个 Rail，skills={skill_count}"
    )


async def agent_stream(
    query: str,
    conv_id: str,
    cascade_result: Optional[dict] = None,
    context: Optional[dict] = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    EDPAgent 唯一请求入口。yield 17 种细粒度事件。

    事件顺序（典型）：
      conversation_start
        → think_start → think_chunk* → [todolist_*] → [todo_status*] → think_end
        → tool_start → tool_end
        → think_start → think_chunk* → think_end
        → ...
        → final_answer_start → final_answer_chunk* → final_answer_end
      conversation_end
    """
    agent = _get_agent()
    original_body = (context or {}).get("body", {})

    from openjiuwen.core.session.agent import create_agent_session
    from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerFactory

    session = create_agent_session(session_id=conv_id, card=agent.card)

    # ── 提前 pre_run：从 Redis 恢复 session state，以便检测取消标记 ──────
    # 必须在判断 is_resume / 检查 checkpoint_to_release 之前执行 pre_run，
    # 否则 session.get_state("checkpoint_to_release") 拿到的是空值（内存
    # session 还未从 Redis 恢复）。
    pre_run_inputs = (
        {"query": "continue", "conversation_id": conv_id}
        if cascade_result is not None
        else {"query": query, "conversation_id": conv_id}
    )
    await session.pre_run(inputs=pre_run_inputs)

    # ── 取消检测：上一轮 CancelRail 标记了 checkpoint_to_release ─────────
    # 此时 post_run() 一定已完成（上一轮请求已经完全结束），不存在竞态。
    checkpoint_to_release = session.get_state("checkpoint_to_release")
    if checkpoint_to_release:
        await _reset_session_after_cancel(agent, session, conv_id, pre_run_inputs)

    # 仅在"外部用户请求首轮"发会话开始事件；cascade 续轮不发
    is_external_turn = cascade_result is None
    try:
        checkpoint_exists = await CheckpointerFactory.get_checkpointer().session_exists(conv_id)
    except Exception:
        logger.exception("[DPA] checkpoint_exists 查询异常，默认为 True")
        checkpoint_exists = True
    is_resume = cascade_result is not None or checkpoint_exists
    if is_external_turn:
        yield ConversationStartEvent(content="本轮对话开始")

    # ── 首轮 / 续轮路径 ───────────────────────────────────────────────
    if cascade_result is not None:
        logger.info(f"[DPA] Cascade 续轮：conv_id={conv_id}")
        yield InterruptEndEvent(content="本次输入全部结束")
        session.update_state({
            "cascade_result": cascade_result,
            "original_body": original_body,
            "pending_delegate": None,
            "response_template": None,
            "ui_notices": None,
        })
        stream_inputs = {"query": "continue", "conversation_id": conv_id}
    else:
        if not is_resume:
            logger.info(f"[DPA] 确认是首轮请求：conv_id={conv_id}, is_resume={is_resume}")
            yield InterruptStartEvent(
                interrupt_id="response_template",
                content=_agent_rule.scripts.get_response_template("request_start"),
            )
            yield InterruptStartEvent(
                interrupt_id="response_template",
                content=_agent_rule.scripts.get_response_template("planning_start"),
            )
        logger.info(f"[DPA] 首轮：conv_id={conv_id}, query={query!r:.60}")
        session.update_state({"original_body": original_body,"response_template": None,"ui_notices": None,})
        stream_inputs = {"query": query, "conversation_id": conv_id}

    # ── 状态机处理 Runner 流 ─────────────────────────────────────────
    processor = _StreamProcessor(scripts=_agent_rule.scripts if _agent_rule else None)
    raw_event_count = 0
    try:
        async for raw_event in agent.stream(inputs=stream_inputs, session=session):
            raw_event_count += 1
            logger.debug(
                f"[DPA] raw event #{raw_event_count}: type={getattr(raw_event, 'type', None)}"
            )
            for evt in processor.process(raw_event):
                _log_stream_payload(evt)
                yield evt

            # 在 tool_end 边界 drain 脚本/Rail 注入的非中断话术（ui_notices）。
            # 仅在 raw event 为 tool_end 时检查，避免频繁查询 session。
            if getattr(raw_event, "type", None) == "tool_end":
                raw_payload = getattr(raw_event, "payload", None) or {}
                raw_plugin = raw_payload.get("plugin", "") if isinstance(raw_payload, dict) else ""
                for evt in _drain_ui_notices(session, "tool_end", plugin=raw_plugin):
                    _log_stream_payload(evt)
                    yield evt
                if raw_plugin == "todolist_modify":
                    for evt in _drain_ui_notices(session, "todo_end", plugin=raw_plugin):
                        _log_stream_payload(evt)
                        yield evt
    except BaseException as e:
        logger.exception(
            f"[DPA] agent.stream 抛出异常: conv_id={conv_id}, "
            f"err_msg={e}"
        )
        raise

    # 流结束：flush 尾部事件（think_end / final_answer_end 等）
    for evt in processor.finalize():
        _log_stream_payload(evt)
        yield evt
    logger.debug(
        f"[DPA] agent.stream() 结束：共处理 {raw_event_count} 个 raw event"
    )

    # ── 话术后处理：Rail 写入的 response_template → yield 给前端 ──────────
    response_template = session.get_state("response_template")
    if response_template:
        yield InterruptStartEvent(
            interrupt_id="response_template",
            content=response_template,
        )

    # ── 中断检测：VA 委托 ─────────────────────────────────────────────
    pending_delegate = session.get_state("pending_delegate")
    if not pending_delegate:
        # 兜底：openjiuwen 在 commit_interrupt 收尾路径会把 rail 在 raise 前
        # 的最后一帧 state 写入回滚（详见 docs/issues/2026-04-28-versatile-
        # rail-state-lost-on-interrupt.md）。从框架自己持久化的 INTERRUPTION_KEY
        # 中重建 delegate。
        interruption = session.get_state(INTERRUPTION_KEY)
        interrupted_tools = (
            getattr(interruption, "interrupted_tools", None) if interruption else None
        )
        if interrupted_tools:
            entry = next(iter(interrupted_tools.values()))
            tool_call = getattr(entry, "tool_call", None)
            if tool_call is not None and getattr(tool_call, "name", "") == "call_versatile":
                args = tool_call.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning(
                            f"[DPA] pending_delegate 解析 tool_call.arguments 失败，"
                            f"使用空 dict 兜底：err={e}, raw={args!r:.120}"
                        )
                        args = {}
                if isinstance(args, dict):
                    pending_delegate = {
                        "intent": args.get("query_intent", ""),
                        "task_description": args.get("query_description", ""),
                    }
                    logger.info(
                        f"[DPA] pending_delegate 兜底命中 INTERRUPTION_KEY: "
                        f"intent={pending_delegate['intent']}"
                    )

    if pending_delegate:
        logger.info(
            f"[DPA] 检测到 VA 委托请求：intent={pending_delegate.get('intent')}"
        )
        yield DelegateRequest.model_validate(pending_delegate)
        session.update_state({"pending_delegate": None, INTERRUPTION_KEY: None})
        return


    # 会话正常结束（只在外部请求首轮入口侧发；cascade 续轮是中间态不发）
    if is_external_turn:
        yield ConversationEndEvent(content="本轮对话结束")


# ════════════════════════════════════════════════════════════════════
# 内部辅助
# ════════════════════════════════════════════════════════════════════


async def _reset_session_after_cancel(
    agent,
    session,
    conv_id: str,
    pre_run_inputs: dict,
) -> None:
    """上一轮 CancelRail 标记了 checkpoint_to_release 后，在下一轮请求开头彻底重置会话。

    时序保证：此函数在 agent_stream() 的 pre_run() 之后调用，
    此时上一轮的 post_run()（含 checkpointer.save）一定已完成，
    不存在 release() 与 save() 的竞态。

    三层清理：
      1. Redis：release() 删除所有 {conv_id}:* key（checkpoint / agent state / workflow state / graph state）
      2. 内存 session state：清空所有业务字段（context / INTERRUPTION_KEY 等）
      3. context_engine 内存池：清除该 session 的上下文对象
         （context_engine 是 agent 单例上的跨请求复用池，不清理会导致
         create_context 返回旧对象，旧对话历史仍在内存中）

    清理完后重新 pre_run，让 checkpointer 在空 state 上重建干净的状态。
    """
    from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerFactory

    logger.info(f"[DPA] 检测到取消标记，重置会话：conv_id={conv_id}")

    try:
        # 1. 删除 Redis 所有 checkpoint key
        try:
            await CheckpointerFactory.get_checkpointer().release(conv_id)
            logger.info(f"[DPA] checkpoint 已释放：conv_id={conv_id}")
        except Exception as e:
            logger.warning(f"[DPA] checkpoint 释放失败（忽略）：conv_id={conv_id}, err={e}")

        # 2. 清空内存 session state
        session.update_state({
            "checkpoint_to_release": None,
            "response_template": None,
            "pending_delegate": None,
            "cascade_result": None,
            "ui_notices": None,
            "original_body": None,
            "context": None,
            INTERRUPTION_KEY: None,
        })

        # 3. 清空 context_engine 内存池
        await agent.context_engine.clear_context(session_id=conv_id)
        logger.info(f"[DPA] context_engine 已清理：conv_id={conv_id}")

        # 重新 pre_run：checkpointer 在空 state 上 recover，session 干净启动
        session._pre_run_done = False
        await session.pre_run(inputs=pre_run_inputs)

    except Exception as e:
        # 重置是补偿性操作，失败时降级为继续使用现有 session（最坏情况残留旧上下文），
        # 不应阻断 agent_stream 主流程。
        logger.exception(
            f"[DPA] 会话重置异常，降级继续（可能残留旧上下文）：conv_id={conv_id}, err={e}"
        )


def _get_agent():
    if _agent is None:
        raise RuntimeError("EDPAgent 未初始化，请先调用 await initialize_dpa()")
    return _agent


def reload_agent_rule(new_body: str) -> None:
    """动态替换 AgentRule 的 markdown body，更新 system prompt。

    保持现有 frontmatter（scope/limits/scripts/todolist_steps）不变，
    仅替换注入 LLM 的业务逻辑部分。
    """
    global _agent, _agent_rule

    if _agent_rule is None:
        logger.warning("[DPA] reload_agent_rule: _agent_rule 未初始化，跳过")
        return
    if _agent is None:
        logger.warning("[DPA] reload_agent_rule: _agent 未初始化，跳过")
        return

    _agent_rule.markdown_body = new_body
    system_prompt = new_body.strip() + "\n\n" + build_system_prompt().strip()

    from openjiuwen.core.single_agent import ReActAgentConfig

    config = ReActAgentConfig()
    config = config.configure_prompt_template(
        [{"role": "system", "content": system_prompt}]
    )
    config = config.configure_max_iterations(_agent_rule.limits.max_iterations)
    _agent.configure(config)

    logger.info(
        f"[DPA] reload_agent_rule: body 已更新 ({len(new_body)} chars), "
        f"max_iterations={_agent_rule.limits.max_iterations}"
    )

def _drain_ui_notices(session, event_type: str, plugin: str = "") -> list[AgentEvent]:
    """从 session.ui_notices 中 drain 指定 event 类型的提示话术，yield 对应事件。

    调用者负责在原生 tool_end 边界后调用；剩余 notice 会被写回 session。
    未明文化 notice 仅 yield 带 content 的轻量事件实例，data/args 保持为空。
    """
    notices = session.get_state("ui_notices") or []
    if not isinstance(notices, list) or not notices:
        return []
    matched: list[AgentEvent] = []
    remaining: list[dict] = []
    for item in notices:
        if not isinstance(item, dict):
            continue
        if item.get("event") == event_type:
            content = str(item.get("content", "") or "")
            if not content:
                continue
            if event_type == "tool_end":
                matched.append(ToolEndEvent(content=content, plugin=plugin or "call_versatile", data={}))
            elif event_type == "todo_end":
                matched.append(TodoEndEvent(id="", status="done", content=content))
        else:
            remaining.append(item)
    session.update_state({"ui_notices": remaining or None})
    return matched


class _StreamProcessor:
    """
    把 Runner 原始事件流转换为细粒度 AgentEvent 的状态机。

    原始事件类型：
      llm_reasoning   → think_start / think_chunk / think_end + todolist 解析
    llm_output      → final_answer_start / final_answer_chunk
    answer          → final_answer_end（可能跟在 llm_output 后，也可能独立）
      tool_start      → ToolStartEvent
      tool_end        → ToolEndEvent

    TodoList 任务通过 lite_todo_write 工具管理（覆盖式），工具结果在 tool_end 时解析为 TodoList* 事件，
    话术 content 字段使用 ScriptsConfig.todolist_start / .todolist_end + 中文状态映射拼装。
    """

    STATE_IDLE = "idle"
    STATE_THINKING = "thinking"
    STATE_ANSWERING = "answering"

    def __init__(self, scripts: Any = None) -> None:
        """
        Args:
            scripts: ScriptsConfig 实例，提供 todolist_start / todolist_end 文案。
                     None 时退化到内置默认（"已生成任务规划" / "任务规划完成"）。
        """
        self.state = self.STATE_IDLE
        self._think_buffer = ""
        self._answer_buffer = ""
        self._scripts = scripts

        # 【新增】性能监控相关属性定义
        self._start_time = time.time()  # 记录模型调用的开始时间
        self._first_token_logged = False  # 记录首token是否被log打印了
        self._last_token_time = None  # 记录上一个token的时间
        self._event_count = 0  # 记录raw_event的序号

    def process(self, raw_event) -> list[AgentEvent]:
        """把一个原始 event 转为零或多个 AgentEvent。"""
        if raw_event is None:
            return []

        event_type = getattr(raw_event, "type", None)
        payload = getattr(raw_event, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        content = payload.get("output") or payload.get("content") or ""

        # 【新增】性能监控逻辑
        self._event_count += 1
        self._log_performance(event_type, payload, self._event_count)

        events: list[AgentEvent] = []

        # ── 反思流（llm_reasoning）────────────────────────────────────
        # 话术对齐 docs/prd/talking_points.md §二
        # think_start "准备进行步骤规划" / think_chunk 首块加 "开始进行步骤规划：" 前缀
        if event_type == "llm_reasoning":
            if self.state != self.STATE_THINKING:
                events.extend(self._flush_answer_if_needed())
                events.append(ThinkStartEvent(content="准备进行步骤规划"))
                self.state = self.STATE_THINKING
                self._think_buffer = ""
                # 首 chunk 前缀
                chunk_content = f"开始进行步骤规划：{content}"
            else:
                chunk_content = content
            events.append(ThinkChunkEvent(content=chunk_content))
            self._think_buffer += content
            return events

        # ── 最终答案流（llm_output）──────────────────────────────────
        # 规范定义（feat-north-api-sse.md §4.5.9）：
        #   流式片段走 SummaryEvent（token by token）
        #   全量一次性帧走 FinalAnswerChunkEvent（由 answer 事件触发补发）
        if event_type == "llm_output":
            # 离开 thinking 状态
            events.extend(self._flush_thinking_if_needed())
            if self.state != self.STATE_ANSWERING:
                events.append(FinalAnswerStartEvent())
                self.state = self.STATE_ANSWERING
                self._answer_buffer = ""
            events.append(SummaryEvent(content=content))
            self._answer_buffer += content
            return events

        # ── 最终答案完成（answer）────────────────────────────────────
        if event_type == "answer":
            events.extend(self._flush_thinking_if_needed())
            # 诊断日志：每次 answer 事件都打 [EDP-LLM-RAW]（INFO，全文不截断），
            # 提供 grep 时间线；当 answer_buffer 与本帧 content 都为空时，再补一条
            # [EDP-LLM-EMPTY]（WARNING）用于快速过滤。
            logger.info(
                f"[EDP-LLM-RAW] answer event: "
                f"think_buffer_len={len(self._think_buffer)} "
                f"answer_buffer_len={len(self._answer_buffer)} "
                f"raw_answer_content_len={len(content)} "
                f"answer_buffer={self._answer_buffer!r} "
                f"raw_content={content!r}"
            )
            if not content and not self._answer_buffer:
                logger.warning(
                    f"[EDP-LLM-EMPTY] empty final answer detected: "
                    f"think_buffer_len={len(self._think_buffer)} "
                    f"answer_buffer_len={len(self._answer_buffer)} "
                    f"raw_answer_content_len={len(content)}"
                )
            if self.state == self.STATE_ANSWERING:
                # 流式已给过 summary × N，这里补一条全量 final_answer_chunk + end
                events.append(FinalAnswerChunkEvent(content=self._answer_buffer))
                events.append(FinalAnswerEndEvent())
            else:
                # 没有流式 output，直接 start + chunk(全量) + end
                events.append(FinalAnswerStartEvent())
                events.append(FinalAnswerChunkEvent(content=content))
                events.append(FinalAnswerEndEvent())
            self.state = self.STATE_IDLE
            self._answer_buffer = ""
            return events

        # ── 工具调用 ─────────────────────────────────────────────────
        if event_type == "tool_start":
            events.extend(self._flush_thinking_if_needed())
            events.extend(self._flush_answer_if_needed())
            plugin = payload.get("plugin", "")
            args = payload.get("args", {}) if isinstance(payload.get("args"), dict) else {}
            # 注：[EDP-LLM-TOOL] 已迁移到 ExecutionLimitRail.before_tool_call 入口
            # 处统一打印（覆盖 ask_user / lite_todo_write / read_file 等被 suppress
            # 不发 tool_start 事件的工具）。本分支只保留 ToolStartEvent 派发。
            events.append(ToolStartEvent(
                content=content,
                plugin=plugin,
                args=args,
            ))
            # 跟一个 tool_status（对齐抓包；前端把它当"运行中"提示）
            # content 与 tool_start 同步，简化实现；如需"正在…"措辞，可在话术层定制
            events.append(ToolStatusEvent(
                plugin=plugin,
                content=content,
            ))
            return events

        if event_type == "tool_end":
            events.extend(self._flush_thinking_if_needed())
            events.extend(self._flush_answer_if_needed())
            plugin = payload.get("plugin", "")
            tool_data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            events.append(ToolEndEvent(
                content=content,
                plugin=plugin,
                data=tool_data,
            ))
            # 解析 lite_todo_write 工具结果 → 派生 TodoList* 事件（含话术）
            if plugin == "lite_todo_write":
                events.extend(self._parse_lite_todo_tool_result(tool_data))
            return events

        # ── 单步执行话术 todo_start / todo_end（rail 补发，对齐 talking_points §四/§七）──
        if event_type == "todo_start":
            events.append(TodoStartEvent(
                id=payload.get("id", ""),
                title=payload.get("title", ""),
                content=content,
            ))
            return events

        if event_type == "todo_end":
            events.append(TodoEndEvent(
                id=payload.get("id", ""),
                status=payload.get("status", "done"),
                content=content,
            ))
            return events

        # 未识别的事件忽略
        return events

    def finalize(self) -> list[AgentEvent]:
        """流结束时 flush 尚未闭合的状态。"""
        events: list[AgentEvent] = []
        events.extend(self._flush_thinking_if_needed())
        events.extend(self._flush_answer_if_needed())
        return events

    # ── 内部 flush 辅助 ─────────────────────────────────────────────

    def _flush_thinking_if_needed(self) -> list[AgentEvent]:
        if self.state != self.STATE_THINKING:
            return []
        events = []
        events.append(ThinkEndEvent(content="本次规划结束"))
        self.state = self.STATE_IDLE
        self._think_buffer = ""
        return events

    def _flush_answer_if_needed(self) -> list[AgentEvent]:
        if self.state != self.STATE_ANSWERING:
            return []
        # 被其他事件打断时，补全量 final_answer_chunk + end（保证前端拿到权威文本）
        events: list[AgentEvent] = [
            FinalAnswerChunkEvent(content=self._answer_buffer),
            FinalAnswerEndEvent(),
        ]
        self.state = self.STATE_IDLE
        self._answer_buffer = ""
        return events

    def _parse_lite_todo_tool_result(self, tool_data: dict) -> list[AgentEvent]:
        """解析 lite_todo_write 工具结果，派生 TodoListStartEvent + N×Item + EndEvent。

        v2 step_id schema：每条 todo 是 ``{"step_id": <1..4>, "status": "pending"|"done"}``，
        content 由框架按 ``CANONICAL_STEPS[step_id]`` 反查（防止 LLM 自创步骤名漂移）。
        不打算做的步骤 LLM 直接不放进 todos——列表里出现的就是即将做或已完成的项。

        Item.content 渲染格式（对齐 docs/prd/talking_points.md L11）：
            "{visible_pos}.{canonical_name}（{status_cn}）<br/>"
        其中 ``visible_pos`` 是**1-based 数组下标**（连续编号 1 / 2 / 3 / ...），
        而 ``Item.id`` 字段仍是 canonical ``step_id``（1 / 2 / 3 / 4）。
        这样前端只渲染 content 时看到的是连续编号（不会出现 1 → 3 → 4 跳号），
        后端用 ``id`` 字段做 step_id ↔ skill 路由 / 状态匹配仍然准确。
        例如 LLM 选 ``[step_id=1, step_id=3, step_id=4]`` 时：
            content: "1.推荐... / 2.确定购买... / 3.查询余额..."
            id:           1            3                4

        TodoListStartEvent.content / TodoListEndEvent.content 取自 ScriptsConfig
        （todolist_start / todolist_end 字段），未配置时退化为内置默认。

        若 todos 为空（全部 done 自动清空场景），不发任何事件——
        避免空清单污染前端。
        """
        from .tool.lite_todo.models import TODO_STATUS_CN, get_canonical_steps

        events: list[AgentEvent] = []
        todos = tool_data.get("todos", [])
        if not todos:
            return events

        start_text = getattr(self._scripts, "todolist_start", None) or "已生成任务规划"
        end_text = getattr(self._scripts, "todolist_end", None) or "任务规划完成"

        # 一次性取当前激活的 step_id → content 映射（运行时来自 AgentRule.md）
        from .tool.lite_todo.models import TODO_STATUS_CN, get_canonical_steps, get_step_to_skill
        canonical_map = get_canonical_steps()
        skill_map = get_step_to_skill()

        events.append(TodoListStartEvent(content=start_text))
        for visible_pos, t in enumerate(todos, start=1):
            step_id = t.get("step_id")
            status = t.get("status", "pending")
            # content 始终从 _active_steps 反查（不信任 LLM 或持久化层传入的 content 串）
            canonical = canonical_map.get(step_id, "<未知步骤>") if isinstance(step_id, int) else "<未知步骤>"
            status_cn = TODO_STATUS_CN.get(status, status)
            events.append(TodoListItemEvent(
                id=step_id if isinstance(step_id, int) else 0,
                title=canonical,
                status=status,
                # 视觉编号用 1-based 下标，避免前端看到 "1.xxx 3.xxx 4.xxx" 跳号
                content=f"{visible_pos}.{canonical}（{status_cn}）<br/>",
            ))
        events.append(TodoListEndEvent(count=len(todos), content=end_text))
        logger.info(f"[DPA] lite_todo_write → {len(todos)} TodoList* events")
        # 记录详细的TodoList信息用于监控和调试
        to_logger(
            level=Level.INFO,
            message={
                "type": str(ObservationType.EVENT),
                "todo_list": [
                    {
                        "step_id": t.get("step_id"),
                        "status": t.get("status", "pending"),
                        "tool_name": "lite_todo_write",
                        "skill_name": skill_map.get(t.get("step_id"), "<未知技能>"),
                        "content": f"{visible_pos}.{canonical_map.get(t.get('step_id'), '<未知步骤>')}（{TODO_STATUS_CN.get(t.get('status', 'pending'), t.get('status', 'pending'))}）"
                    }
                    for visible_pos, t in enumerate(todos, start=1)
                ]
            },
            extra=Extra(tag=Tag.TAG_PLANNING_DECISION, cost=0),
        )
        return events

    def _log_performance(self, event_type: str, payload: dict, event_counter: int = 0) -> None:
        """内部方法：记录LLM调用的首字时延、Token间隔及消耗统计"""
        """JiuWen SDK没有chunk的钩子，session级别的write记录在这里实现"""
        current_time = time.time()
        # logger.debug(f"[DPA] raw event #{self._event_count}: type={event_type}")
        # 监控文本 Token 时延 (TTFT & Interval)
        if event_type == "llm_output":
            if not self._first_token_logged:
                ttft_ms = int((current_time - self._start_time) * 1000)
                to_logger(
                    level=Level.INFO,
                    message={},
                    extra=Extra(tag=Tag.TAG_LLM_CALL_FIRST_TOKEN, cost=ttft_ms),
                )
                self._first_token_logged = True
                self._last_token_time = current_time
            else:
                if self._last_token_time:
                    interval_ms = int((current_time - self._last_token_time) * 1000)
                    to_logger(
                        level=Level.DEBUG,
                        message={},
                        extra=Extra(tag=Tag.TAG_LLM_CALL_STREAM_TOKEN, cost=interval_ms),
                    )
                self._last_token_time = current_time

        # 监控 Token 消耗 (监听 llm_usage 事件)
        if event_type == "llm_usage":
            usage_data = payload.get("usage_metadata", {})
            if isinstance(usage_data, dict):
                input_tokens = usage_data.get("input_tokens", 0)
                output_tokens = usage_data.get("output_tokens", 0)
                total_tokens = usage_data.get("total_tokens", 0)
                to_logger(
                    level=Level.INFO,
                    message={
                        "input_tokens": {input_tokens},
                        "output_tokens": {output_tokens},
                        "total_tokens": {total_tokens},
                    },
                    extra=Extra(tag=Tag.TAG_LLM_CALL_STATISTICS, cost=0),
                )

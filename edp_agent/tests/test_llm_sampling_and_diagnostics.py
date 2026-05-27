"""LLM sampling override + 空响应诊断日志测试.

背景：openjiuwen 0.1.11 的 ModelRequestConfig 默认 temperature=0.95 / top_p=0.1，
对 reasoning 类模型（glm-5）会偶发返回空 content。
EDPAgent 在 initialize_dpa() 里覆盖为 0.3 / 0.95，并在收到空 answer 时打诊断日志。

诊断日志关键字（grep 友好）：
  [EDP-LLM-CONFIG]  启动时一次，证明覆盖生效
  [EDP-LLM-RAW]     每次 answer 事件一次（无论空/非空），含长度 + 截断 preview
  [EDP-LLM-EMPTY]   仅 answer 内容为空时一次，与 [EDP-LLM-RAW] 同时出现
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger


def _scripts():
    return SimpleNamespace(
        todolist_start="开始规划todolist",
        todolist_end="todolist规划完成",
    )


@pytest.fixture
def captured_logs():
    """捕获 loguru 输出供断言用。"""
    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="DEBUG")
    yield captured
    logger.remove(sink_id)


# ── sampling 覆盖 ────────────────────────────────────────────────────────────

def test_apply_sampling_overrides_writes_hardcoded_values():
    """硬编码 0.3 / 0.95 必须落到 model_config_obj。"""
    from EDPAgent.agent import _apply_sampling_overrides

    cfg = SimpleNamespace(model_config_obj=SimpleNamespace(temperature=0.95, top_p=0.1))
    _apply_sampling_overrides(cfg)

    assert cfg.model_config_obj.temperature == 0.3
    assert cfg.model_config_obj.top_p == 0.95


def test_apply_sampling_overrides_emits_config_log_keyword(captured_logs):
    """启动覆盖后必须打 [EDP-LLM-CONFIG] 关键字日志，含 sampling 值。"""
    from EDPAgent.agent import _apply_sampling_overrides

    cfg = SimpleNamespace(model_config_obj=SimpleNamespace(temperature=0.95, top_p=0.1))
    _apply_sampling_overrides(cfg)

    matched = [line for line in captured_logs if "[EDP-LLM-CONFIG]" in line]
    assert matched, f"未找到 [EDP-LLM-CONFIG] 关键字日志，captured={captured_logs}"
    blob = " ".join(matched)
    assert "0.3" in blob and "0.95" in blob, f"日志缺少 sampling 值：{matched}"


def test_apply_sampling_overrides_no_op_when_config_missing(captured_logs):
    """model_config_obj=None 不应崩溃；仍打日志说明被跳过。"""
    from EDPAgent.agent import _apply_sampling_overrides

    cfg = SimpleNamespace(model_config_obj=None)
    _apply_sampling_overrides(cfg)  # 不抛异常即可

    matched = [line for line in captured_logs if "[EDP-LLM-CONFIG]" in line]
    assert matched, "即使跳过，也应打一条 [EDP-LLM-CONFIG] 日志说明状态"


# ── 空响应诊断 ───────────────────────────────────────────────────────────────

def _ev(event_type: str, content: str = "", **extra):
    payload = {"content": content}
    payload.update(extra)
    return SimpleNamespace(type=event_type, payload=payload)


def test_stream_processor_logs_empty_answer_warning(captured_logs):
    """收到 reasoning 流但最终 answer 内容为空 → 打 [EDP-LLM-EMPTY] 警告。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    proc.process(_ev("llm_reasoning", "推理一段..."))
    proc.process(_ev("llm_reasoning", "再推理一段..."))
    proc.process(_ev("answer", ""))

    matched = [line for line in captured_logs if "[EDP-LLM-EMPTY]" in line]
    assert matched, f"应打 [EDP-LLM-EMPTY]，captured={captured_logs}"
    blob = " ".join(matched)
    # 关键字段必须可 grep
    assert "think_buffer_len" in blob, "诊断日志缺 think_buffer_len 字段"
    assert "answer_buffer_len" in blob, "诊断日志缺 answer_buffer_len 字段"


def test_stream_processor_no_empty_log_when_answer_has_content(captured_logs):
    """正常 answer（非空）不应触发 [EDP-LLM-EMPTY]。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    proc.process(_ev("llm_reasoning", "推理..."))
    proc.process(_ev("llm_output", "回答片段"))
    proc.process(_ev("answer", "完整回答"))

    matched = [line for line in captured_logs if "[EDP-LLM-EMPTY]" in line]
    assert not matched, f"非空 answer 不应触发 [EDP-LLM-EMPTY]，但有：{matched}"


def test_stream_processor_logs_empty_when_no_reasoning_either(captured_logs):
    """既无 reasoning 也无 output，answer 为空 → 仍打 [EDP-LLM-EMPTY]（更严重）。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    proc.process(_ev("answer", ""))

    matched = [line for line in captured_logs if "[EDP-LLM-EMPTY]" in line]
    assert matched, "完全空响应也必须有诊断日志"


# ── RAW 日志（覆盖每次 answer，含正常返回）────────────────────────────────

def test_stream_processor_logs_raw_on_normal_answer(captured_logs):
    """正常 answer 必须打 [EDP-LLM-RAW]，含长度字段和原始 content。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    proc.process(_ev("llm_output", "你好"))
    proc.process(_ev("answer", "你好，我是助手"))

    matched = [line for line in captured_logs if "[EDP-LLM-RAW]" in line]
    assert matched, f"正常 answer 应打 [EDP-LLM-RAW]，captured={captured_logs}"
    blob = " ".join(matched)
    assert "raw_answer_content_len" in blob
    assert "answer_buffer_len" in blob
    assert "think_buffer_len" in blob
    # 现在日志不截断，原始内容必须完整出现在日志里
    assert "你好，我是助手" in blob, f"原始 raw_content 没落盘：{matched}"


def test_stream_processor_raw_log_does_not_truncate_long_content(captured_logs):
    """超长 answer 的 raw_content 必须完整入库（明确不截断）。"""
    from EDPAgent.agent import _StreamProcessor

    long_content = "甲" * 5000  # 5000 char Chinese
    proc = _StreamProcessor(scripts=_scripts())
    proc.process(_ev("answer", long_content))

    matched = [line for line in captured_logs if "[EDP-LLM-RAW]" in line]
    assert matched
    blob = " ".join(matched)
    # 长度字段必须诚实反映 5000
    assert "raw_answer_content_len=5000" in blob, f"raw len 没反映原始长度：{matched}"
    # 不截断：5000 个"甲"应当全部出现在日志中
    assert long_content in blob, "原始 5000 字内容没全部入库（仍在截断？）"
    # 也不应有截断标记
    assert "(truncated)" not in blob, "日志仍带 (truncated) 标记，说明截断没去干净"


def test_stream_processor_raw_and_empty_both_fire_on_empty(captured_logs):
    """空 answer 时 RAW 与 EMPTY 都应出现（一条用于时间线，一条用于快速过滤）。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    proc.process(_ev("llm_reasoning", "推理片段"))
    proc.process(_ev("answer", ""))

    raw_lines = [line for line in captured_logs if "[EDP-LLM-RAW]" in line]
    empty_lines = [line for line in captured_logs if "[EDP-LLM-EMPTY]" in line]
    assert raw_lines, "空响应也应打 RAW（用于时间线对比）"
    assert empty_lines, "空响应必须打 EMPTY（用于快速过滤）"


# ── TOOL 日志（rail 入口处，覆盖所有工具含 suppress 的）─────────────────────

import asyncio
from unittest.mock import AsyncMock, MagicMock


def _mk_rail_ctx(tool_name: str, tool_args):
    """合成 AgentCallbackContext 给 ExecutionLimitRail.before_tool_call 用。"""
    inputs = SimpleNamespace(tool_name=tool_name, tool_args=tool_args)
    state: dict = {}
    session = SimpleNamespace(
        session_id="test-conv",
        get_state=lambda k: state.get(k),
        update_state=lambda d: state.update(d),
        write_stream=AsyncMock(),
    )
    ctx = SimpleNamespace(inputs=inputs, session=session,
                          request_force_finish=lambda *_args, **_kw: None)
    return ctx


def _mk_rail():
    """造一个最小可用的 ExecutionLimitRail（带 stub 的 AgentRule.limits/scripts）。"""
    from EDPAgent.rail.execution_limit_rail import ExecutionLimitRail
    rule = SimpleNamespace(
        limits=SimpleNamespace(tasks={}, max_iterations=100),
        scripts=SimpleNamespace(
            tool_start="正在调用：{tool_name}",
            tool_end="{tool_name} 执行完成",
            todo_start="开始执行：{title}",
            todo_end="{title} 已完成",
        ),
    )
    return ExecutionLimitRail(rule)


def test_rail_logs_tool_keyword_for_business_tool(captured_logs):
    """call_versatile（非 suppress）必须打 [EDP-LLM-TOOL]。"""
    rail = _mk_rail()
    args = {"query_intent": "purchase_product", "params": {"amount": 10, "product_id": "2"}}
    ctx = _mk_rail_ctx("call_versatile", args)

    asyncio.get_event_loop().run_until_complete(rail.before_tool_call(ctx))

    matched = [line for line in captured_logs if "[EDP-LLM-TOOL]" in line]
    assert matched, f"call_versatile 必须触发 [EDP-LLM-TOOL]，captured={captured_logs}"
    blob = " ".join(matched)
    assert "tool_name='call_versatile'" in blob
    assert "purchase_product" in blob, "args 全文未落盘"


def test_rail_logs_tool_keyword_for_suppressed_ask_user(captured_logs):
    """★ ask_user（被 ExecutionLimitRail suppress 不发 tool_start 事件）也必须打 [EDP-LLM-TOOL]。

    这是本次主修复点：之前 [EDP-LLM-TOOL] 绑定 _StreamProcessor 的 tool_start 事件，
    而 ask_user / lite_todo_write / read_file 在 rail 里被 suppress 不会发该事件，
    导致 slot extraction 这种关键调用在诊断日志中"消失"。
    """
    rail = _mk_rail()
    args = {
        "response_template_status": "confirm",
        "response_template_keys": '{"product_select_confirm":"product_select_confirm"}',
        "response_template_vars": '{"productName":"安心90天","amount":"6.00"}',
    }
    ctx = _mk_rail_ctx("ask_user", args)

    asyncio.get_event_loop().run_until_complete(rail.before_tool_call(ctx))

    matched = [line for line in captured_logs if "[EDP-LLM-TOOL]" in line]
    assert matched, "ask_user 即使被 suppress 也必须有 [EDP-LLM-TOOL]"
    blob = " ".join(matched)
    assert "tool_name='ask_user'" in blob
    assert "product_select_confirm" in blob
    assert "安心90天" in blob, "话术 vars 应出现在日志里"


def test_rail_tool_log_does_not_truncate_args(captured_logs):
    """args 全文不截断。"""
    rail = _mk_rail()
    long_args = {"q": "甲" * 3000}
    ctx = _mk_rail_ctx("call_versatile", long_args)

    asyncio.get_event_loop().run_until_complete(rail.before_tool_call(ctx))

    matched = [line for line in captured_logs if "[EDP-LLM-TOOL]" in line]
    assert matched
    blob = " ".join(matched)
    assert "甲" * 3000 in blob
    assert "(truncated)" not in blob


def test_rail_tool_log_handles_string_args(captured_logs):
    """args 是 JSON 字符串而非 dict 时（openjiuwen 偶发），仍打日志不崩。"""
    rail = _mk_rail()
    ctx = _mk_rail_ctx("ask_user", '{"question":"请确认"}')

    asyncio.get_event_loop().run_until_complete(rail.before_tool_call(ctx))

    matched = [line for line in captured_logs if "[EDP-LLM-TOOL]" in line]
    assert matched
    assert "tool_name='ask_user'" in " ".join(matched)

"""E2E: 真 Aliyun DashScope LLM 验证三个诊断关键字落到日志.

  [EDP-LLM-CONFIG] — _apply_sampling_overrides() 走过即出
  [EDP-LLM-RAW]    — 每次 answer 事件 INFO，含 length + preview
  [EDP-LLM-EMPTY]  — 仅当 answer 与 buffer 都为空时 WARNING

正向 case 走真 LLM round-trip（验证 RAW 出现 + EMPTY 不误报）；
负向 case 用合成空 answer 事件（不烧 token，独立验证 EMPTY 触发）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger


pytestmark = [pytest.mark.e2e]


def _aliyun_client(env: dict[str, str]):
    from openai import OpenAI
    return OpenAI(api_key=env["ALIYUN_API_KEY"], base_url=env["ALIYUN_API_BASE"])


def _scripts():
    return SimpleNamespace(
        todolist_start="开始规划todolist",
        todolist_end="todolist规划完成",
    )


@pytest.fixture
def captured_logs():
    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="DEBUG")
    yield captured
    logger.remove(sink_id)


def test_e2e_config_keyword_emitted_on_override(captured_logs):
    """_apply_sampling_overrides 后 [EDP-LLM-CONFIG] 必出。"""
    from EDPAgent.agent import _apply_sampling_overrides

    cfg = SimpleNamespace(model_config_obj=SimpleNamespace(temperature=0.95, top_p=0.1))
    _apply_sampling_overrides(cfg)

    assert cfg.model_config_obj.temperature == 0.3
    assert cfg.model_config_obj.top_p == 0.95
    blob = "\n".join(captured_logs)
    assert "[EDP-LLM-CONFIG]" in blob
    assert "0.3" in blob and "0.95" in blob


def test_e2e_raw_keyword_on_real_llm_response(aliyun_env, captured_logs):
    """跑一次真 LLM 调用，把 answer 喂给 _StreamProcessor，确认 RAW 出现且 EMPTY 不误报。"""
    from EDPAgent.agent import _StreamProcessor

    client = _aliyun_client(aliyun_env)
    response = client.chat.completions.create(
        model=aliyun_env["ALIYUN_MODEL"],
        messages=[{"role": "user", "content": "用一句话介绍你自己。"}],
        temperature=0.3,
        top_p=0.95,
    )
    answer_text = response.choices[0].message.content or ""
    assert answer_text.strip(), f"real LLM 返回空 content（finish={response.choices[0].finish_reason}）"

    # 通过 _StreamProcessor 模拟 Runner 收尾流程：先 llm_output 再 answer
    proc = _StreamProcessor(scripts=_scripts())
    proc.process(SimpleNamespace(type="llm_output", payload={"content": answer_text}))
    proc.process(SimpleNamespace(type="answer", payload={"content": answer_text}))

    raw_lines = [line for line in captured_logs if "[EDP-LLM-RAW]" in line]
    empty_lines = [line for line in captured_logs if "[EDP-LLM-EMPTY]" in line]

    assert raw_lines, f"真 LLM 跑完应有 [EDP-LLM-RAW]，captured={captured_logs}"
    assert not empty_lines, f"非空响应不应触发 [EDP-LLM-EMPTY]：{empty_lines}"
    blob = " ".join(raw_lines)
    assert f"raw_answer_content_len={len(answer_text)}" in blob, \
        f"RAW 长度字段不匹配；answer_text len={len(answer_text)}, log={blob}"


def test_e2e_empty_keyword_on_synthetic_empty_answer(captured_logs):
    """合成空 answer（不烧 token）独立验证 EMPTY 触发。"""
    from EDPAgent.agent import _StreamProcessor

    proc = _StreamProcessor(scripts=_scripts())
    proc.process(SimpleNamespace(type="llm_reasoning", payload={"content": "推理一段..."}))
    proc.process(SimpleNamespace(type="answer", payload={"content": ""}))

    raw_lines = [line for line in captured_logs if "[EDP-LLM-RAW]" in line]
    empty_lines = [line for line in captured_logs if "[EDP-LLM-EMPTY]" in line]
    assert raw_lines, "空响应也应有 RAW（用于时间线对比）"
    assert empty_lines, "空响应必须打 EMPTY（用于快速过滤）"

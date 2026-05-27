#!/usr/bin/env python3
"""diag_llm.py — 在容器内对配置的 LLM gateway 跑诊断序列.

读取与 EDPAgent 完全一致的环境变量（PLANNING_AGENT_MODEL_*），
直接对生产 gateway 发请求，不依赖 agent runtime 启动状态。

用法（host 上推荐方式）：
  docker exec -it edpagent python /app/a2a_service/agents/EDPAgent/scripts/diag_llm.py
  docker exec edpagent python /app/a2a_service/agents/EDPAgent/scripts/diag_llm.py --case cascade --runs 10
  docker exec edpagent python /app/a2a_service/agents/EDPAgent/scripts/diag_llm.py --case all --runs 5

用法（容器内）：
  python /app/a2a_service/agents/EDPAgent/scripts/diag_llm.py

支持的环境变量（**完全对齐 EDPAgent config.py:_build_custom_headers**）：
  PLANNING_AGENT_MODEL_BASE_URL          必填，OpenAI-compat /v1/chat/completions 之前的部分
  PLANNING_AGENT_MODEL_API_KEY           必填，企业内部加密时自动解密
  PLANNING_AGENT_MODEL_NAME              必填
  PLANNING_AGENT_MODEL_TIMEOUT           可选，默认 120 秒
  PLANNING_AGENT_MODEL_TOKEN             可选，企业网关额外鉴权（加密时自动解密）
  PLANNING_AGENT_MODEL_TOKEN_HEADER      与 _TOKEN 配对，header 名（缺失时报错）
  PLANNING_AGENT_MODEL_USER_ID           可选，企业用户 ID
  PLANNING_AGENT_MODEL_USER_ID_HEADER    与 _USER_ID 配对，header 名（缺失时报错）
  PLANNING_AGENT_MODEL_EXTRA_HEADERS     可选，JSON dict，注入任意额外 header
                                          例：'{"X-Tenant":"prod","X-Trace":"diag"}'

加解密：脚本会优先 import `common.crypto.decrypt_config_value`（容器内通过
路径推断从 `/app/a2a_service` 加载）；找不到时降级为 identity（生产环境
若 token 加密但解密器找不到，会 401 鉴权失败，此时贴出错日志告知）。

判读输出（每次调用一行）：
  ✓        正常（finish_reason=stop 且 content 非空，或 finish_reason=tool_calls 且 tool_calls 非空）
  ⚠ EMPTY  bug 复现（content 与 tool_calls 同时为空，含 finish_reason 异常）

bug 复现的请求会把完整 raw response dump 到 /tmp/diag/<case>-<i>.json，
拿去贴回上下文做二次分析。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx


# ── 解密器：优先 import common.crypto.decrypt_config_value，找不到则 identity ─

def _resolve_decrypt() -> Callable[[str], str]:
    """企业内部 token / api_key 通常加密存储，需要 decrypt_config_value 解密。

    路径推断：本脚本在 `/app/a2a_service/agents/EDPAgent/scripts/diag_llm.py`，
    向上 3 级是 `/app/a2a_service/`，注入 sys.path 后 common.crypto 可见。
    其它部署路径（host 上手工跑 / agent-runtime dev 模式）也试过 path 找不到时，
    回退为 identity（不解密），并打 stderr 警告。
    """
    candidates = [
        Path(__file__).resolve().parents[3],   # 容器：/app/a2a_service
        Path(__file__).resolve().parents[3] / "applications" / "a2a_service",  # dev
    ]
    for p in candidates:
        if (p / "common" / "crypto.py").exists():
            sys.path.insert(0, str(p))
            try:
                from common.crypto import decrypt_config_value  # type: ignore
                return decrypt_config_value
            except ImportError:
                continue
    # 找不到 → identity（warn 一下）
    sys.stderr.write(
        "⚠ common.crypto 未找到，TOKEN/API_KEY 不解密。"
        "若企业网关用加密 token，请把 a2a_service 路径加到 PYTHONPATH，"
        "或把这条警告反馈给我们。\n"
    )
    return lambda v: v


# ── 用例库 ──────────────────────────────────────────────────────────────────

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_versatile",
            "description": "调用业务工作流（理财推荐 / 余额查询 / 购买理财产品）",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_intent": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["query_intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "向用户提问以获取必要信息",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
]


CASES: dict[str, dict[str, Any]] = {
    "baseline": {
        "desc": "无 tools 简单问候（基线）",
        "messages": [{"role": "user", "content": "你好"}],
        "tools_enabled": False,
    },
    "tools_no_trigger": {
        "desc": "带 tools 但 prompt 不该触发",
        "messages": [{"role": "user", "content": "你好"}],
        "tools_enabled": True,
    },
    "single_purchase": {
        "desc": "单轮 prompt 直接触发 tool（购买）",
        "messages": [
            {"role": "user", "content": "请帮我购买第二支理财产品，金额10元"}
        ],
        "tools_enabled": True,
    },
    "cascade": {
        "desc": "★ 多轮 cascade 模拟 ask_user 续跑（最像生产购买场景）",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是企业级动态规划智能体，处理基金理财业务。"
                    "需要调用 ask_user 向用户确认购买信息，"
                    "或调用 call_versatile 执行业务动作。"
                ),
            },
            {"role": "user", "content": "推荐两个稳健型理财产品"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "call_versatile",
                            "arguments": json.dumps(
                                {"query_intent": "recommend_products",
                                 "params": {"risk": "low"}},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": (
                    '[{"id":"P001","name":"稳健月月盈","yield":3.2},'
                    '{"id":"P002","name":"安心90天","yield":3.5}]'
                ),
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "ask_user",
                            "arguments": json.dumps(
                                {"question": "以下两个产品请问您选哪一支？"},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": "购买第二支，10元",
            },
            {"role": "user", "content": "购买第二支，10元"},
        ],
        "tools_enabled": True,
    },
}


# ── 环境读取 ───────────────────────────────────────────────────────────────

def _load_env(decrypt: Callable[[str], str]) -> dict[str, Any]:
    """从 os.environ 拿配置。容器内启动时 a2a_service .env 已经被进程 load。

    完全对齐 EDPAgent config.py:_build_custom_headers 的字段集合：
      - TOKEN/USER_ID 各自要求 _HEADER 配对（缺一报错，与 EDPAgent 行为一致）
      - EXTRA_HEADERS 接受 JSON dict
      - TOKEN/API_KEY 走 decrypt_config_value（企业加密场景）
    """
    raw_api_key = os.environ.get("PLANNING_AGENT_MODEL_API_KEY", "")
    required = {
        "base_url": os.environ.get("PLANNING_AGENT_MODEL_BASE_URL", ""),
        "api_key":  decrypt(raw_api_key) if raw_api_key else "",
        "model":    os.environ.get("PLANNING_AGENT_MODEL_NAME", ""),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        sys.stderr.write(
            f"❌ 缺少环境变量: {missing}\n"
            "   请确认容器启动时已经加载 a2a_service.env，或显式 export 这三个：\n"
            "     PLANNING_AGENT_MODEL_BASE_URL\n"
            "     PLANNING_AGENT_MODEL_API_KEY\n"
            "     PLANNING_AGENT_MODEL_NAME\n"
        )
        sys.exit(2)

    # token / user_id（成对校验，与 config.py 行为一致）
    raw_token = os.environ.get("PLANNING_AGENT_MODEL_TOKEN", "")
    token_header = os.environ.get("PLANNING_AGENT_MODEL_TOKEN_HEADER", "")
    if raw_token and not token_header:
        sys.stderr.write(
            "❌ PLANNING_AGENT_MODEL_TOKEN 已设置但 PLANNING_AGENT_MODEL_TOKEN_HEADER 缺失。\n"
            "   生产环境 EDPAgent 在这种情况下会启动失败，脚本同样不允许。\n"
        )
        sys.exit(2)
    user_id = os.environ.get("PLANNING_AGENT_MODEL_USER_ID", "")
    user_id_header = os.environ.get("PLANNING_AGENT_MODEL_USER_ID_HEADER", "")
    if user_id and not user_id_header:
        sys.stderr.write(
            "❌ PLANNING_AGENT_MODEL_USER_ID 已设置但 PLANNING_AGENT_MODEL_USER_ID_HEADER 缺失。\n"
        )
        sys.exit(2)

    # extra headers（JSON dict）
    extra_raw = os.environ.get("PLANNING_AGENT_MODEL_EXTRA_HEADERS", "")
    extra_headers: dict[str, str] = {}
    if extra_raw:
        try:
            parsed = json.loads(extra_raw)
            if isinstance(parsed, dict):
                extra_headers = {str(k): str(v) for k, v in parsed.items()}
            else:
                sys.stderr.write(
                    f"⚠ PLANNING_AGENT_MODEL_EXTRA_HEADERS 不是 JSON dict，跳过。raw={extra_raw!r}\n"
                )
        except json.JSONDecodeError as e:
            sys.stderr.write(
                f"⚠ PLANNING_AGENT_MODEL_EXTRA_HEADERS JSON 解析失败：{e}, raw={extra_raw!r}\n"
            )

    return {
        **required,
        "timeout": float(os.environ.get("PLANNING_AGENT_MODEL_TIMEOUT", "120")),
        "token":          decrypt(raw_token) if raw_token else "",
        "token_header":   token_header,
        "user_id":        user_id,
        "user_id_header": user_id_header,
        "extra_headers":  extra_headers,
    }


def _build_headers(env: dict) -> dict[str, str]:
    """构造 HTTP 请求头，与 EDPAgent 实际发出的请求 header 集合完全一致。

    顺序：先 Authorization（Bearer api_key）+ Content-Type → 再 token /
    user_id → 最后 extra_headers。后者覆盖前者（与 EDPAgent dict.update 一致）。
    """
    headers = {
        "Authorization": f"Bearer {env['api_key']}",
        "Content-Type": "application/json",
    }
    if env["token"] and env["token_header"]:
        headers[env["token_header"]] = env["token"]
    if env["user_id"] and env["user_id_header"]:
        headers[env["user_id_header"]] = env["user_id"]
    if env["extra_headers"]:
        headers.update(env["extra_headers"])
    return headers


# ── 单次调用 ───────────────────────────────────────────────────────────────

def _call_once(env: dict, case: dict, headers: dict, dump_dir: Path,
               case_name: str, idx: int) -> dict[str, Any]:
    body = {
        "model": env["model"],
        "messages": case["messages"],
        "temperature": 0.3,
        "top_p": 0.95,
        "stream": False,
    }
    if case["tools_enabled"]:
        body["tools"] = _TOOLS
        body["tool_choice"] = "auto"

    url = env["base_url"].rstrip("/") + "/chat/completions"
    started = time.monotonic()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=env["timeout"])
        elapsed = time.monotonic() - started
        try:
            data = r.json()
        except Exception:
            data = {"_raw_text": r.text, "_status": r.status_code}
    except Exception as e:
        return {"ok": False, "exception": repr(e), "elapsed_ms": int((time.monotonic()-started)*1000)}

    if r.status_code != 200:
        # HTTP 异常：dump 整段
        path = dump_dir / f"{case_name}-{idx}-http{r.status_code}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return {"ok": False, "http_status": r.status_code, "dump": str(path),
                "elapsed_ms": int(elapsed*1000)}

    # 拆解 OpenAI-shape 响应
    choices = data.get("choices") or [{}]
    msg = choices[0].get("message") or {}
    fr = choices[0].get("finish_reason")
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []
    reasoning = msg.get("reasoning_content") or ""
    usage = data.get("usage") or {}
    completion_tokens = usage.get("completion_tokens", -1)

    # 异常判定：content 与 tool_calls 同时为空
    is_empty = (not content.strip()) and (not tool_calls)

    info: dict[str, Any] = {
        "ok": not is_empty,
        "is_empty": is_empty,
        "finish_reason": fr,
        "content_len": len(content),
        "reasoning_len": len(reasoning),
        "tool_calls_n": len(tool_calls),
        "completion_tokens": completion_tokens,
        "elapsed_ms": int(elapsed * 1000),
        "message_keys": sorted(msg.keys()),
    }
    if tool_calls:
        first = tool_calls[0]
        fn = (first.get("function") or {})
        info["tool_name"] = fn.get("name")
        info["tool_args"] = fn.get("arguments", "")
    if is_empty:
        path = dump_dir / f"{case_name}-{idx}-EMPTY.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        info["dump"] = str(path)
    return info


# ── 主流程 ─────────────────────────────────────────────────────────────────

def _run_case(env: dict, case_name: str, case: dict, runs: int,
              dump_dir: Path, headers: dict) -> int:
    print(f"\n{'='*78}")
    print(f"Case: {case_name} — {case['desc']}")
    print(f"  base_url = {env['base_url']}")
    print(f"  model    = {env['model']}")
    print(f"  tools    = {case['tools_enabled']}    runs = {runs}")
    print('=' * 78)

    empty_count = 0
    for i in range(1, runs + 1):
        info = _call_once(env, case, headers, dump_dir, case_name, i)
        if not info.get("ok"):
            empty_count += 1
            mark = "⚠ EMPTY" if info.get("is_empty") else "❌ FAIL"
        else:
            mark = "✓"
        line = (
            f"  [{i}/{runs}] {mark} "
            f"finish_reason={info.get('finish_reason')!r} "
            f"content_len={info.get('content_len')} "
            f"tool_calls={info.get('tool_calls_n')} "
            f"reasoning_len={info.get('reasoning_len')} "
            f"comp_tok={info.get('completion_tokens')} "
            f"elapsed={info.get('elapsed_ms')}ms"
        )
        print(line)
        if "tool_name" in info:
            args_short = (info["tool_args"] or "")[:120]
            print(f"        └─ tool={info['tool_name']!r} args={args_short!r}")
        if "exception" in info:
            print(f"        └─ exception: {info['exception']}")
        if "dump" in info:
            print(f"        └─ ⚠ raw response dumped: {info['dump']}")
            print(f"        └─ message keys: {info.get('message_keys')}")

    print(f"\n  → empty/fail rate: {empty_count}/{runs} = {empty_count*100//max(runs,1)}%")
    return empty_count


def main() -> None:
    p = argparse.ArgumentParser(description="EDPAgent LLM gateway 诊断")
    p.add_argument("--case", default="all",
                   choices=["all", *CASES.keys()],
                   help="跑哪个用例（默认 all）")
    p.add_argument("--runs", type=int, default=5,
                   help="每个用例跑多少次（默认 5）")
    p.add_argument("--dump-dir", default="/tmp/diag",
                   help="异常响应 dump 目录")
    args = p.parse_args()

    decrypt = _resolve_decrypt()
    env = _load_env(decrypt)
    headers = _build_headers(env)
    dump_dir = Path(args.dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)

    # 打印的 header 摘要里，对 Authorization / token 做脱敏（只保留前后 4 字符）
    def _mask(s: str) -> str:
        if not s or len(s) <= 12:
            return "***"
        return f"{s[:6]}...{s[-4:]}"
    safe_headers = {
        k: (_mask(v) if k.lower() in ("authorization",) or k == env.get("token_header", "")
            else v)
        for k, v in headers.items()
    }
    print(f"📍 dump_dir = {dump_dir}")
    print(f"📍 headers（敏感字段脱敏后）= {safe_headers}")
    if env["extra_headers"]:
        print(f"📍 extra_headers 注入 {len(env['extra_headers'])} 个字段")

    if args.case == "all":
        case_names = list(CASES.keys())
    else:
        case_names = [args.case]

    total_empty = 0
    for name in case_names:
        total_empty += _run_case(env, name, CASES[name], args.runs, dump_dir, headers)

    print(f"\n{'='*78}")
    print(f"汇总：{len(case_names)} 个用例 × {args.runs} 次 = "
          f"{len(case_names)*args.runs} 次调用，异常 {total_empty} 次")
    if total_empty > 0:
        print(f"      详细 dump 在 {dump_dir}/，把目录里的文件贴出来即可定位")
    print('=' * 78)


if __name__ == "__main__":
    main()

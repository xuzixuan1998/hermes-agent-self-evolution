#!/usr/bin/env python3
"""log_matrix.py — 把 EDPAgent 4 关键字日志做矩阵排查.

读 stdin（或 --input 文件）的纯文本日志（通常来自 docker logs），按
conversation_id 聚合 [EDP-LLM-RAW/TOOL/EMPTY] 三类事件 + 关键字段值，
映射到 7 类（3 类正常 + 3 类异常 + 未分类），输出汇总表 + 异常明细。

用法：
  # 实时排查（流式不太适合矩阵，建议先 collect 一段再分析）
  docker logs --since 1h edpagent 2>&1 | python scripts/log_matrix.py

  # 容器内
  docker exec edpagent python /app/a2a_service/agents/EDPAgent/scripts/log_matrix.py \\
    --input <(docker logs --since 1h edpagent 2>&1)   # ← 这条要在 host 上

  # 直接传文件
  docker logs --since 1h edpagent > /tmp/elog.txt 2>&1
  python scripts/log_matrix.py --input /tmp/elog.txt

  # 只看异常
  docker logs --since 1h edpagent 2>&1 | python scripts/log_matrix.py --only-bad

  # 输出 JSON 给后续脚本消费
  docker logs --since 1h edpagent 2>&1 | python scripts/log_matrix.py --json

诊断决策树（同时见模块 docstring 与 README）：
  RAW=1, EMPTY=0, content>0                                 → ✓ 正常聊天
  TOOL=1, EMPTY=0                                            → ✓ 正常工具调用
  RAW≥1, TOOL≥1, EMPTY=0, content>0                          → ✓ 工具→总结
  RAW≥1, EMPTY≥1, think_buffer_len>0, content_len=0          → ⚠ Reasoning 卡死
  RAW≥1, EMPTY≥1, think_buffer_len=0, content_len=0          → ⚠ 完全无产出
  TOOL≥1, EMPTY≥1                                            → ⚠ 工具后总结失败（cascade 异常）
  其它                                                       → ? 未分类

重要假设：日志格式是 EDPAgent 用 loguru 输出的，conversation_id 出现在
keyword 之前最近的一个 token 里（agent_id "edp_agent" 之后的那一个）。
如果你的部署改了 logger 格式，调整 _CONV_RE。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── 正则 ───────────────────────────────────────────────────────────────────
# 例：... edp_agent\x01tool-log-1777790472\x01[EDP-LLM-TOOL] tool_start event: ...
# loguru 用 SOH (\x01) 而不是空格做字段分隔；正则需同时接受 \s 和 \x01。
_SEP = r"[\s\x01]"
_CONV_RE = re.compile(rf"{_SEP}+(\S+){_SEP}+\[EDP-LLM-(CONFIG|RAW|TOOL|EMPTY)\]")

# 关键字段（数值类）
_LEN_RE = re.compile(r"\b(\w+_len)=(\d+)")
_TOOL_NAME_RE = re.compile(r"tool_name='([^']*)'")
_TEMPERATURE_RE = re.compile(r"temperature=([\d.]+)")
_TOP_P_RE = re.compile(r"top_p=([\d.]+)")


# ── 数据结构 ───────────────────────────────────────────────────────────────

@dataclass
class ConvAgg:
    conv_id: str
    raw_n: int = 0
    tool_n: int = 0
    empty_n: int = 0

    # 选择性记录"代表性"字段值。RAW 可能多次出现，取最大 think_buffer_len
    # 与最大 content_len（已经"成功"过的轮次不算异常）。
    max_think_len: int = 0
    max_answer_buf_len: int = 0
    max_raw_content_len: int = 0
    tool_names: list[str] = field(default_factory=list)
    max_tool_args_len: int = 0


@dataclass
class Verdict:
    label: str
    severity: str  # "ok" / "bad" / "unknown"


# ── 解析 ───────────────────────────────────────────────────────────────────

def _extract_lens(line: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in _LEN_RE.finditer(line):
        out[m.group(1)] = int(m.group(2))
    return out


def parse(stream) -> tuple[dict[str, ConvAgg], dict[str, Any]]:
    """逐行解析。返回 (按 conv 聚合, 全局元信息)"""
    convs: dict[str, ConvAgg] = {}
    meta: dict[str, Any] = {
        "config_seen": False,
        "config_temperature": None,
        "config_top_p": None,
        "lines_total": 0,
        "lines_keyword": 0,
    }

    for raw_line in stream:
        meta["lines_total"] += 1
        m = _CONV_RE.search(raw_line)
        if not m:
            continue
        conv_id, kind = m.group(1), m.group(2)
        meta["lines_keyword"] += 1

        # CONFIG 是启动级别，没有 conv_id（占位 token 通常是 logger 模块名）
        if kind == "CONFIG":
            meta["config_seen"] = True
            tm = _TEMPERATURE_RE.search(raw_line)
            tp = _TOP_P_RE.search(raw_line)
            if tm:
                meta["config_temperature"] = float(tm.group(1))
            if tp:
                meta["config_top_p"] = float(tp.group(1))
            continue

        agg = convs.setdefault(conv_id, ConvAgg(conv_id=conv_id))
        lens = _extract_lens(raw_line)

        if kind == "RAW":
            agg.raw_n += 1
            agg.max_think_len = max(agg.max_think_len, lens.get("think_buffer_len", 0))
            agg.max_answer_buf_len = max(agg.max_answer_buf_len, lens.get("answer_buffer_len", 0))
            agg.max_raw_content_len = max(agg.max_raw_content_len, lens.get("raw_answer_content_len", 0))
        elif kind == "TOOL":
            agg.tool_n += 1
            agg.max_tool_args_len = max(agg.max_tool_args_len, lens.get("args_len", 0))
            tn = _TOOL_NAME_RE.search(raw_line)
            if tn:
                agg.tool_names.append(tn.group(1))
        elif kind == "EMPTY":
            agg.empty_n += 1
            agg.max_think_len = max(agg.max_think_len, lens.get("think_buffer_len", 0))

    return convs, meta


# ── 矩阵分类 ────────────────────────────────────────────────────────────────

def classify(c: ConvAgg) -> Verdict:
    has_raw = c.raw_n > 0
    has_tool = c.tool_n > 0
    has_empty = c.empty_n > 0
    has_content = c.max_raw_content_len > 0 or c.max_answer_buf_len > 0

    # 异常优先（EMPTY 出现就是 bad）
    if has_empty:
        if has_tool:
            return Verdict("⚠ TOOL_AFTER_BLANK", "bad")
        # 仅 RAW + EMPTY
        if c.max_think_len > 0:
            return Verdict("⚠ REASONING_STUCK", "bad")
        return Verdict("⚠ TOTAL_BLANK", "bad")

    # 正常路径
    if has_raw and has_tool and has_content:
        return Verdict("✓ TOOL_THEN_ANSWER", "ok")
    if has_tool and not has_raw:
        return Verdict("✓ NORMAL_TOOL", "ok")
    if has_raw and not has_tool and has_content:
        return Verdict("✓ NORMAL_CHAT", "ok")
    if has_raw and has_tool and not has_content:
        # 工具调完没收到 answer（用户中途断开 / agent 还在跑）
        return Verdict("✓ TOOL_NO_ANSWER_YET", "ok")
    if has_raw and not has_content:
        return Verdict("? RAW_BUT_BLANK_NO_EMPTY_FLAG", "unknown")
    return Verdict("? UNCLASSIFIED", "unknown")


# ── 渲染 ───────────────────────────────────────────────────────────────────

_LABEL_ORDER = [
    "✓ NORMAL_CHAT",
    "✓ NORMAL_TOOL",
    "✓ TOOL_THEN_ANSWER",
    "✓ TOOL_NO_ANSWER_YET",
    "⚠ REASONING_STUCK",
    "⚠ TOTAL_BLANK",
    "⚠ TOOL_AFTER_BLANK",
    "? RAW_BUT_BLANK_NO_EMPTY_FLAG",
    "? UNCLASSIFIED",
]


def render_text(convs: dict[str, ConvAgg], meta: dict, only_bad: bool) -> str:
    by_label: dict[str, list[ConvAgg]] = defaultdict(list)
    verdicts: dict[str, Verdict] = {}
    for c in convs.values():
        v = classify(c)
        verdicts[c.conv_id] = v
        by_label[v.label].append(c)

    out: list[str] = []
    out.append("=" * 78)
    out.append(f"日志矩阵排查（共 {meta['lines_total']} 行，命中关键字 "
               f"{meta['lines_keyword']} 行，覆盖 {len(convs)} 个会话）")
    out.append("=" * 78)
    if meta["config_seen"]:
        out.append(f"[启动 sampling] temperature={meta['config_temperature']} "
                   f"top_p={meta['config_top_p']}")
    else:
        out.append("[启动 sampling] ⚠ 未抓到 [EDP-LLM-CONFIG]，可能日志范围不含启动")
    out.append("")

    # 汇总分布
    total = sum(len(v) for v in by_label.values())
    bad = sum(len(by_label[k]) for k in _LABEL_ORDER if k.startswith("⚠"))
    out.append(f"分布（异常 {bad}/{total} = "
               f"{bad*100//max(total,1)}%）：")
    for label in _LABEL_ORDER:
        n = len(by_label.get(label, []))
        if n == 0:
            continue
        out.append(f"  {label:32s}  {n:4d}")
    out.append("")

    # 异常会话明细
    bad_labels = [l for l in _LABEL_ORDER if l.startswith("⚠") or l.startswith("?")]
    out.append("─── 异常 / 未分类会话明细 ───")
    saw_any = False
    for label in bad_labels:
        items = by_label.get(label, [])
        if not items:
            continue
        saw_any = True
        out.append(f"\n  {label}：")
        for c in items:
            tools_str = ",".join(c.tool_names[:3]) if c.tool_names else "-"
            out.append(
                f"    conv={c.conv_id}  RAW={c.raw_n} TOOL={c.tool_n} EMPTY={c.empty_n}  "
                f"think={c.max_think_len} content={c.max_raw_content_len} "
                f"answer_buf={c.max_answer_buf_len} tool_args={c.max_tool_args_len}  "
                f"tools=[{tools_str}]"
            )
    if not saw_any:
        out.append("  （无异常会话 ✓）")

    if not only_bad:
        out.append("\n─── 正常会话明细 ───")
        ok_labels = [l for l in _LABEL_ORDER if l.startswith("✓")]
        for label in ok_labels:
            items = by_label.get(label, [])
            if not items:
                continue
            out.append(f"\n  {label}：")
            for c in items:
                tools_str = ",".join(c.tool_names[:3]) if c.tool_names else "-"
                out.append(
                    f"    conv={c.conv_id}  RAW={c.raw_n} TOOL={c.tool_n}  "
                    f"content={c.max_raw_content_len} tools=[{tools_str}]"
                )

    out.append("")
    out.append("─── 排查建议 ───")
    out.append("  ⚠ REASONING_STUCK    → sampling 仍不健康；提高 top_p 或换非 reasoning 模型")
    out.append("  ⚠ TOTAL_BLANK        → 上下文超长 / 安全审核 / MaaS 网关翻译失败")
    out.append("  ⚠ TOOL_AFTER_BLANK   → cascade 消息序列异常 / 工具结果 prompt 太大")
    out.append("  ? UNCLASSIFIED       → 数据不全（如客户端中断）")
    out.append("=" * 78)
    return "\n".join(out)


def render_json(convs: dict[str, ConvAgg], meta: dict) -> str:
    rows = []
    for c in convs.values():
        v = classify(c)
        rows.append({
            "conv_id": c.conv_id,
            "label": v.label,
            "severity": v.severity,
            "raw_n": c.raw_n,
            "tool_n": c.tool_n,
            "empty_n": c.empty_n,
            "max_think_len": c.max_think_len,
            "max_answer_buf_len": c.max_answer_buf_len,
            "max_raw_content_len": c.max_raw_content_len,
            "max_tool_args_len": c.max_tool_args_len,
            "tool_names": c.tool_names,
        })
    return json.dumps({"meta": meta, "convs": rows}, ensure_ascii=False, indent=2)


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="EDPAgent 4 关键字日志矩阵排查")
    p.add_argument("--input", help="日志文件路径，默认 stdin")
    p.add_argument("--only-bad", action="store_true",
                   help="只输出异常会话明细")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    if args.input:
        with open(args.input, "r", encoding="utf-8", errors="replace") as f:
            convs, meta = parse(f)
    else:
        convs, meta = parse(sys.stdin)

    if args.json:
        print(render_json(convs, meta))
    else:
        print(render_text(convs, meta, args.only_bad))


if __name__ == "__main__":
    main()

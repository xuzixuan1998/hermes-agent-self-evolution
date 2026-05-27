# Handoff 006: EDPAgent 远端 API 重构 — 实施完成

**Date:** 2026-05-27
**Based on:** [Plan 005](../plans/005-edp-agent-remote-api-refactor/plan.md) | [Spec](../../specs/005-edp-agent-remote-api-refactor.md)

## Summary

完成了 EDPAgent 推理后端的远端 API 重构，将 ~220 行 async event-loop + cascade-resume + VA 调用代码替换为 ~40 行纯同步 HTTP 客户端。同时删除了 `edp_agent/` 本地目录和 5 个不再需要的依赖。

## What Was Done

### 核心变更
- **`evolution/agents/edp_agent.py`** — 完全重写（218 行 → 40 行）。删除所有 `async`/`await`/`nest_asyncio`/`sys.path` hack。现在是纯 `httpx` 同步 HTTP 客户端，调用 3 个远端 API（`infer` / `agentrule update` / `skill update`）。`run()` 签名不变。
- **`evolution/core/fitness.py`** — `_summarize_trajectory()` 删除 edp-agent 格式的 `role: "tool"` 分支，只保留 hermes-agent 格式。
- **`evolution/core/config.py`** — 删除 `agent_framework_path`、`edp_agent_path` 字段和 `_get_edp_agent_path()` 函数。
- **`evolution/prompts/evolve_prompt.py`** — 删除 `--agent-framework-path` CLI 选项。
- **`evolution/skills/evolve_skill.py`** — 同上。

### 删除
- `edp_agent/` 整个目录（agent.py, agent_rule.py, config.py, rail/, tool/, deployment/, skills/, docs/, 等）
- `tests/edp_agent/` 整个目录
- `pyproject.toml` 移除依赖：`openjiuwen`, `a2a-sdk`, `fastapi`, `nest-asyncio`, `loguru`

### 测试重写
- **`tests/agents/test_agents.py`** — `TestEDPAgent` 用 mock HTTP (`patch("httpx.post")`) 重写，4 个测试全覆盖。
- **`tests/core/test_config.py`** — 删除 `edp_agent_path` 和 `agent_framework_path` 相关测试。

### 配置
- `.env.example` 新增 3 个环境变量：`EDP_INFER_URL`, `EDP_AGENTRULE_UPDATE_URL`, `EDP_SKILL_UPDATE_URL`

## Current State

- **190 tests, 0 failures** — 全量测试通过
- **gepa==0.0.27** — 尝试升级到 0.1.1 失败，因为 dspy 3.2.1 依赖 `gepa[dspy]` extra，而 gepa 0.1.1 是独立版本线不带该 extra。`pyproject.toml` 中设为 `"gepa>=0.0.27"`。
- **CLAUDE.md** — 已添加 `unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy` 的 proxy 说明（此环境 uv/pip 需要关代理才能访问 PyPI）。

## Remaining / Open Issues

1. **远端 API 尚未就绪** — EDPAgent 现在通过 HTTP 调用远端服务，但 API 实际地址待填入 `.env.example`。当前所有测试通过 mock HTTP 运行。
2. **`evolve_prompt.py` 中引用了 `edp_agent/AgentRule.md`** — CLAUDE.md 和 evolve_prompt.py 的 help text 中仍有此引用，目录已删除需要在其他地方准备 AgentRule.md。
3. **`pyproject.toml` 中 gepa 版本** — 若未来 dspy 升级到支持 gepa 0.1.x 的版本，需要同步更新。
4. **git 提交** — 所有变更未提交，当前在工作区中（modified + deleted）。

## Key Files Changed

| File | Status |
|------|--------|
| `evolution/agents/edp_agent.py` | Rewritten (40 lines) |
| `evolution/core/fitness.py` | Modified (removed tool role branch) |
| `evolution/core/config.py` | Modified (removed 2 fields + 1 function) |
| `evolution/prompts/evolve_prompt.py` | Modified (removed CLI option) |
| `evolution/skills/evolve_skill.py` | Modified (removed CLI option) |
| `pyproject.toml` | Modified (removed 5 deps, updated packages.find) |
| `tests/agents/test_agents.py` | Rewritten TestEDPAgent |
| `tests/core/test_config.py` | Modified (removed 2 tests) |
| `.env.example` | Created |
| `CLAUDE.md` | Modified (added proxy note) |
| `edp_agent/` | **Deleted** (entire directory) |
| `tests/edp_agent/` | **Deleted** (entire directory) |
| `uv.lock` | Modified (dependencies resolved) |

## Suggested Skills

- `agent-skills:review` — 对本次变更做五轴代码评审
- `agent-skills:test` — 验证远端 API 端点可用后，补充集成测试
- `agent-skills:ship` — 准备提交和 PR 时使用预发布检查清单
- `agent-skills:context-engineering` — 更新 CLAUDE.md 中已过时的 `edp_agent/` 引用

# EDPAgent —— 动态规划 Agent 参考实现

基于 **openJiuwen agent-runtime** 构建的规则驱动动态规划 Agent。内置六规则配置、HITL 中断、三类 Rail 与 17 种 SSE 事件，面向企业金融/客服等场景提供开箱即用的 Agent 能力。

---

## 核心能力一览

| 能力 | 说明 |
|---|---|
| **规则驱动规划** | 通过 `AgentRule.md` 配置业务范围、规划步骤、迭代上限、执行次数、总结格式、话术六条规则，LLM 按规则动态拆解任务 |
| **HITL 中断恢复** | `ask_user` 工具触发中断，Redis Checkpoint 持久化，用户回复后 cascade 续轮；支持终止关键词 / 超时 / 次数限制 |
| **3 类 Rail** | IterationLimitRail（全局迭代上限）、ExecutionLimitRail（单工具调用次数）、AskUserRail（HITL） |
| **17 种 SSE 事件** | 覆盖会话 / 思考 / 规划 / 任务 / 工具 / 中断 / 总结七大阶段，客户端可分事件类型精细渲染 |
| **LLM 自定义请求头** | `PLANNING_AGENT_MODEL_TOKEN` / `PLANNING_AGENT_MODEL_USER_ID` 与对应 `_HEADER` 成对配置，适配银行/保险/政企各类网关 |
| **北向接口健壮性** | 非 JSON → 415，解析失败 → 400，body 非 dict → 400，统一错误契约 |

详见：[`docs/feat-north-api-sse.md`](docs/feat-north-api-sse.md)

---

## 源码来源（双仓组合）

| 仓库 | 作用 | 推荐分支 |
|---|---|---|
| **agent-runtime**（上游：`openJiuwen/agent-runtime`） | A2A 框架、VersatileAdapter、a2a_service 运行时 | `feature/procode_enhancement` |
| **agent-store-zhl**（本仓：`vincenttao/agent-store-zhl` fork） | EDPAgent 业务代码、AgentRule、部署脚本、文档 | `vincent/edp-agent-clean` |

两个仓在部署时会合并：`agent-store-zhl/community/EDPAgent/` 的全部内容被放入 `agent-runtime/applications/a2a_service/agents/EDPAgent/`。

---

## 三种部署方式

| 方式 | 适用场景 | 入口 |
|---|---|---|
| **Linux 独立安装** | 开发机 / POC / 单机部署，目标机能联网装 Python 包 | [`docs/deployment.md` §A](docs/deployment.md#a-linux-独立安装) |
| **Windows 独立安装** | 开发调试 / 客户 Windows 服务器本机部署 | [`docs/deployment.md` §B](docs/deployment.md#b-windows-独立安装) |
| **Docker 离线打包部署**（推荐生产） | 离线环境 / 客户机不能联网 / 一键启停 | [`docs/deployment.md` §C](docs/deployment.md#c-docker-离线打包部署) |

**不清楚选哪个？**
- 客户机离线或运维希望一键部署 → **Docker 打包**
- Linux 开发/测试机能联网 → **Linux 独立**
- Windows 机器且无 WSL → **Windows 独立**；有 WSL → 用 Linux 方式更省心

---

## 运行时拓扑

```
  客户端
    │  HTTP /v1/.../conversations/<id>
    ▼
┌─────────────────────────────────┐
│  a2a_service       :8090        │
│    ├── Orchestrator             │
│    └── EDPAgent (ReActAgent)    │
│        ├── AgentRule.md (六规则)│
│        ├── 3 Rail + HITL        │
│        └── ask_user / tools     │
└────────┬────────────────────────┘
         │ JSON-RPC A2A
         ▼
┌─────────────────────────────────┐
│  versatile_adapter :8091        │
│    └── VersatileProxy           │
└────────┬────────────────────────┘
         │ HTTP
         ▼
  Versatile 低代码平台
```

- **a2a_service**：面向客户端，北向接口 + 动态规划
- **versatile_adapter**：封装 Versatile 低码平台的 A2A Executor
- **Redis（≥ 6.0）**：会话状态 / Checkpoint / 限流
- **LLM 网关**：OpenAI 兼容接口（如 `glm-5` / `deepseek-chat` / 企业自建网关）

---

## 快速导航

| 场景 | 文档 |
|---|---|
| 完整部署步骤（三种方式） | [`docs/deployment.md`](docs/deployment.md) |
| 北向接口与 SSE 事件需求说明 | [`docs/feat-north-api-sse.md`](docs/feat-north-api-sse.md) |
| 北向返回报文格式规范（实抓包逆向）| [`docs/north-api-response-format.md`](docs/north-api-response-format.md) |
| 需求文档覆盖度分析 | [`docs/gap-analysis-against-feat-doc.md`](docs/gap-analysis-against-feat-doc.md) |
| 测试场景用例 | [`docs/test-scenarios.md`](docs/test-scenarios.md) |
| 新增 tool 开发指南 | [`docs/tool-authoring.md`](docs/tool-authoring.md) |
| 新增 skill 开发指南 | [`docs/skill-authoring.md`](docs/skill-authoring.md) |
| AgentRule 六规则实际配置 | [`AgentRule.md`](AgentRule.md) |

---

## 文档日期

2026-04-23

"""
DPA Agent 系统提示词补充。

Skill 正文不直接注入系统提示词；技能通过 agent.register_skill 注册，
由框架引导模型在运行时使用 read_file 按需读取对应的 SKILL.md。
"""
from __future__ import annotations

_BASE_PROMPT = """\
## 六、技能与工具补充

### 6.1 可用工具

- call_mcp：通用脚本调用，通过 script_command 指定脚本路径、script_params 传入业务参数 JSON，由 MCPInterruptRail 拦截并执行
- call_versatile：通用业务工作流调用，适用于理财推荐、选品、购买筹划等 Skill 场景
- ask_user：在关键信息缺失或敏感操作确认时向用户追问
- execute_cmd：执行 shell 命令，用于 Skill 中调用脚本获取数据
- lite_todo_write：管理待办清单（覆盖式写入），用于 3+ 步任务规划与进度展示

### 6.2 MCP 先行架构

理财推荐类 Skill 采用 MCP 先行架构：
1. 先调用 call_mcp（script_command 指定脚本、script_params 传入业务参数）获取 MCP 产品推荐数据（MCPInterruptRail 拦截后将结果写入 session state）
2. 再调用 call_versatile 获取低码平台银行信息（VersatileInterruptRail 自动读取 MCP 数据注入委托请求）
3. 沙箱归一化脚本合并 MCP 产品列表与低码平台数据
"""


def build_system_prompt() -> str:
    return _BASE_PROMPT

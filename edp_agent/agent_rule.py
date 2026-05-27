"""
AgentRule.md 加载与 schema 定义。

对齐需求文档 §4.2 的六项规则 + 话术 + 终止关键词。

YAML frontmatter 示例：
---
scope:
  allowed: "基金理财相关业务（余额查询、转账）"
  out_of_scope_message: "尚在学习中"

planning_steps:
  - 需求解析
  - 目标拆解
  - 方案生成
  - 规则校验
  - 结果输出

limits:
  max_iterations: 30
  max_input_attempts: 3
  interrupt_timeout_seconds: 300
  tasks:
        call_versatile: 10
        ask_user: 5

summary:
  format: "需求概述→规划过程→任务执行→结果汇总→异常说明"
  max_length: 500
  required_fields:
    - 理财产品名称
    - 购买金额

scripts:
  tool_start: "正在调用：{tool_name}"
  tool_end: "{tool_name} 执行完成"
  interrupt_start: "需要您确认以下信息"
---

# Markdown body 注入到 LLM 系统提示词
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ScopeConfig(BaseModel):
    """业务范围配置（规则 1）。"""
    allowed: str = Field(default="", description="允许处理的业务类型描述")
    out_of_scope_message: str = Field(
        default="尚在学习中",
        description="超范围时返回的默认话术"
    )


class LimitsConfig(BaseModel):
    """执行限制配置（规则 4、5 + HITL 限制）。"""
    # 规则 4
    max_iterations: int = Field(default=30, ge=1, le=500)
    # HITL
    max_input_attempts: int = Field(default=3, ge=1, le=20)
    interrupt_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    # 规则 5：按工具名配置上限
    tasks: dict[str, int] = Field(default_factory=dict)


class TodoStepConfig(BaseModel):
    """单条 todolist 业务步骤配置——绑定 step_id 到 content 与 skill。

    YAML 格式（AgentRule.md frontmatter 下 todolist_steps 列表元素）：

        - step_id: 1
          content: "推荐理财产品"
          skill: "product_recommend_skill"
    """
    step_id: int = Field(ge=1, description="业务步骤编号；必须唯一")
    content: str = Field(description="渲染到 TodoListItemEvent.content 的业务步骤名")
    skill: str = Field(description="该步骤将要调用的 skill 名（与 SKILL.md frontmatter name 一致）")


class SummaryConfig(BaseModel):
    """执行总结格式配置（规则 6）。"""
    format: str = Field(
        default="需求概述→规划过程→任务执行情况→结果汇总→异常说明"
    )
    max_length: int = Field(default=500, ge=100, le=2000)
    required_fields: list[str] = Field(default_factory=list)


class ScriptsConfig(BaseModel):
    """话术配置（对应需求文档 §6）。可选，未配置时使用默认。"""
    tool_start: str = Field(default="正在调用：{tool_name}")
    tool_end: str = Field(default="{tool_name} 执行完成")
    todo_start: str = Field(default="开始执行：{title}")
    todo_end: str = Field(default="{title} 已完成")
    todolist_start: str = Field(default="已生成任务规划")
    todolist_end: str = Field(default="任务规划完成")
    interrupt_start: str = Field(default="需要您确认以下信息")
    mcp_result_empty: str = Field(default="根据您的条件没有找到合适产品，您可以从以下产品中选择或者重新筛选。")
    product_recommend_success: str = Field(default="您可以告诉我想要购买第几支产品")
    product_recommend_empty: str = Field(default="抱歉，暂无符合您条件的理财产品，请调整筛选条件后重试。")
    product_recommend_no_card: str = Field(default="当前账户没有绑定借记卡")
    request_start: str = Field(default="您的请求已收到。")
    planning_start: str = Field(default="我们正在为您进行规划。")
    product_select_confirm: str = Field(default="请确认是否购买{amount}元{productName}理财产品")
    product_select_missing_product: str = Field(default="您可以告诉我想要购买第几支产品")
    product_select_missing_amount: str = Field(default="请问您购买的金额是多少")
    product_select_invalid: str = Field(default="抱歉没有理解您的意思，请重新输入")
    task_cancelled: str = Field(default="好的，已为您取消当前操作。如需其他帮助，请随时告诉我。")
    cancel_confirm: str = Field(default="确认要取消当前操作吗？")
    out_of_scope: str = Field(default="正在学习中，暂不支持该业务。")
    fund_planning_success: str = Field(default="已为您完成理财产品购买")
    fund_planning_buy_failed: str = Field(default="购买失败，请重新尝试")
    fund_planning_transfer_limit: str = Field(default="您已超过转账次数限制，购买失败")
    fund_planning_wealth_insufficient: str = Field(default="理财账户资金不足，查询活期账户余额")
    fund_planning_both_insufficient: str = Field(default="您的活期账户余额不足，结束理财产品购买")
    # talking_points L35-36, L52-53 终止/异常话术
    fund_planning_balance_insufficient: str = Field(default="您的活期账户余额不足，结束理财产品购买")
    fund_planning_card_mismatch: str = Field(default="您只有一张卡，不满足当前理财购买流程，已退出购买")
    fund_planning_purchase_aborted: str = Field(default="购买异常，已退出购买流程")
    fund_planning_session_timeout: str = Field(default="对话超时，已退出购买流程")

    def get_response_template(self, key: str, default: str = "") -> str:
        return getattr(self, key, default)


class AgentRuleConfig(BaseModel):
    """AgentRule.md 完整配置（六规则 + 话术）。"""

    # 规则 1
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    # 规则 2
    planning_steps: list[str] = Field(default_factory=list)
    # 规则 3（任务依赖，结构暂时简化为 dict）
    task_dependencies: dict[str, list[str]] = Field(
        default_factory=dict,
        description="任务 ID → 前置任务 ID 列表；暂不强制使用"
    )
    # 规则 4、5
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    # 规则 6
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    # todolist 业务步骤目录（与 lite_todo_write step_id 枚举绑定）
    # 必填——本字段为空时 agent.initialize_dpa() 调 configure_steps() 会抛 ValueError
    todolist_steps: list[TodoStepConfig] = Field(default_factory=list)
    # 话术
    scripts: ScriptsConfig = Field(default_factory=ScriptsConfig)
    # 原始 frontmatter（保留以供后续扩展）
    raw_frontmatter: dict[str, Any] = Field(default_factory=dict)
    # 注入到 LLM system prompt 的 markdown body
    markdown_body: str = Field(
        default="",
        description="Markdown body，注入到 LLM 系统提示词"
    )


_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def load_agent_rule(rule_path: str | Path) -> AgentRuleConfig:
    """从 AgentRule.md 加载完整规则。"""
    path = Path(rule_path)
    if not path.exists():
        raise FileNotFoundError(f"AgentRule file not found: {path}")

    content = path.read_text(encoding="utf-8")

    match = _FRONTMATTER_PATTERN.match(content)
    if match:
        yaml_content = match.group(1)
        data = yaml.safe_load(yaml_content) or {}
        markdown_body = content[match.end():].strip()
    else:
        data = {}
        markdown_body = content.strip()

    return AgentRuleConfig(
        scope=ScopeConfig(**(data.get("scope") or {})),
        planning_steps=data.get("planning_steps") or [],
        task_dependencies=data.get("task_dependencies") or {},
        limits=LimitsConfig(**(data.get("limits") or {})),
        summary=SummaryConfig(**(data.get("summary") or {})),
        todolist_steps=[TodoStepConfig(**s) for s in (data.get("todolist_steps") or [])],
        scripts=ScriptsConfig(**(data.get("scripts") or {})),
        raw_frontmatter=data,
        markdown_body=markdown_body,
    )

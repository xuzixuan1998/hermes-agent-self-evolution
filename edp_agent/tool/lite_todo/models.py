"""lite_todo data models — step_id-based schema, 配置由 AgentRule.md 注入.

设计要点
--------

1. **不再有内置默认 4 项**——所有 step_id / content / skill 必须由 AgentRule.md
   frontmatter 的 ``todolist_steps`` section 配置。配置缺失时本模块的 getter 都抛
   ``RuntimeError``，TodoItem 也无法创建（pydantic validator 会拒）。
2. ``configure_steps()`` 必须在 ``agent.initialize_dpa()`` 早期调用一次（在创建
   ToolCard 与 register_tools 之前）。它由 ``AgentRuleConfig.todolist_steps`` 喂值。
3. content 字符串完全固定——LLM 不能自定义，schema 用 step_id 枚举强约束。
4. 项数可变 / 顺序由 LLM 决定（任意非空子集）；step_id 不能重复、不能超出已配置
   范围。
5. status 仅 ``pending`` / ``done`` 两档——"运行中"由 todo_status 单独事件承载，
   不打算做的步骤直接不放进列表（不需要 skip 状态）。
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, List, Tuple

from pydantic import BaseModel, ConfigDict, field_validator


class TodoStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"


# ── 模块级配置状态（由 configure_steps 设置；初始为空 → 任何 getter 都抛错）──
_active_steps: list[Tuple[int, str, str]] = []
"""列表元素 = (step_id, content, skill)；按 configure_steps 的顺序保存。"""


def configure_steps(steps: Iterable) -> None:
    """根据 AgentRule.md ``todolist_steps`` 配置激活业务步骤。

    必须在创建 ``LITE_TODO_CARD`` / ``TodoItem`` / ``register_tools`` 之前调用。

    参数
    ----
    steps :
        list of dict 或 ``TodoStepConfig`` 实例。每个元素必须含
        ``step_id`` / ``content`` / ``skill`` 三个字段。

    抛错
    ----
    - ValueError: ``steps`` 为空、step_id 重复、step_id < 1。
    """
    seen: set[int] = set()
    new_steps: list[Tuple[int, str, str]] = []
    for s in steps or []:
        if isinstance(s, dict):
            sid = int(s["step_id"])
            content = str(s["content"])
            skill = str(s["skill"])
        else:
            sid = int(getattr(s, "step_id"))
            content = str(getattr(s, "content"))
            skill = str(getattr(s, "skill"))
        if sid < 1:
            raise ValueError(f"step_id 必须 ≥ 1，得到 {sid}")
        if sid in seen:
            raise ValueError(f"step_id 重复：{sid}")
        if not content:
            raise ValueError(f"step_id={sid} 的 content 不能为空")
        if not skill:
            raise ValueError(f"step_id={sid} 的 skill 不能为空")
        seen.add(sid)
        new_steps.append((sid, content, skill))

    if not new_steps:
        raise ValueError(
            "todolist_steps 配置为空——必须在 AgentRule.md frontmatter 里配置 "
            "todolist_steps section（至少 1 项），lite_todo_write 才能工作。"
        )

    global _active_steps
    _active_steps = new_steps


def reset_steps() -> None:
    """清空当前配置——仅供测试用例清理用。"""
    global _active_steps
    _active_steps = []


def is_configured() -> bool:
    return bool(_active_steps)


def _ensure_configured() -> None:
    if not _active_steps:
        raise RuntimeError(
            "lite_todo todolist_steps 未配置——请先调用 configure_steps() "
            "（agent.initialize_dpa() 加载 AgentRule.md 后会自动调用）。"
        )


def get_canonical_steps() -> dict[int, str]:
    """返回 {step_id: content} 映射；未配置时抛 RuntimeError。"""
    _ensure_configured()
    return {sid: name for sid, name, _ in _active_steps}


def get_step_to_skill() -> dict[int, str]:
    """返回 {step_id: skill_name} 映射；未配置时抛 RuntimeError。"""
    _ensure_configured()
    return {sid: skill for sid, _, skill in _active_steps}


def get_step_ids() -> list[int]:
    """返回当前已配置的 step_id 列表（保持配置顺序）；未配置时抛 RuntimeError。"""
    _ensure_configured()
    return [sid for sid, _, _ in _active_steps]


# ── TodoItem ──────────────────────────────────────────────────────────────


class TodoItem(BaseModel):
    """单条 todo 项；step_id 由 ``_active_steps`` 动态校验。"""
    model_config = ConfigDict(extra="forbid")

    step_id: int
    status: TodoStatus

    @field_validator("step_id")
    @classmethod
    def _check_step_id(cls, v: int) -> int:
        # 用 ValueError（而不是 RuntimeError）让 pydantic 自动包装成 ValidationError
        if not _active_steps:
            raise ValueError(
                "lite_todo todolist_steps 未配置；TodoItem 不能创建。"
                "请先调用 configure_steps()（agent.initialize_dpa() 会自动调用）。"
            )
        valid = {sid for sid, _, _ in _active_steps}
        if v not in valid:
            raise ValueError(
                f"step_id 必须在 {sorted(valid)} 之一（来自 AgentRule.md "
                f"todolist_steps 配置），得到 {v}"
            )
        return v

    @property
    def content(self) -> str:
        """便捷只读属性——按 step_id 解出标准 content 字符串。"""
        return get_canonical_steps()[self.step_id]

    @property
    def skill(self) -> str:
        """便捷只读属性——按 step_id 解出绑定的 skill 名。"""
        return get_step_to_skill()[self.step_id]


TodoList = List[TodoItem]


# ── 中文 status 标签（rendered into TodoListItemEvent.content）──────────────
TODO_STATUS_CN: dict[str, str] = {
    "pending": "待执行",
    "done": "完成",
}

"""Tests for lite_todo configure_steps / reset_steps / YAML override."""
from __future__ import annotations

import pytest


def test_get_canonical_steps_raises_when_unconfigured():
    """未调用 configure_steps 时所有 getter 都抛 RuntimeError。"""
    from EDPAgent.tool.lite_todo.models import (
        get_canonical_steps,
        get_step_ids,
        get_step_to_skill,
        is_configured,
        reset_steps,
    )

    reset_steps()
    assert is_configured() is False
    with pytest.raises(RuntimeError, match="未配置"):
        get_canonical_steps()
    with pytest.raises(RuntimeError, match="未配置"):
        get_step_to_skill()
    with pytest.raises(RuntimeError, match="未配置"):
        get_step_ids()


def test_todo_item_creation_raises_when_unconfigured():
    """未调用 configure_steps 时 TodoItem(...) 也抛错。"""
    from pydantic import ValidationError

    from EDPAgent.tool.lite_todo.models import TodoItem, reset_steps

    reset_steps()
    with pytest.raises(ValidationError):
        TodoItem(step_id=1, status="pending")


def test_configure_steps_rejects_empty():
    """空列表 → ValueError。"""
    from EDPAgent.tool.lite_todo.models import configure_steps

    with pytest.raises(ValueError, match="todolist_steps 配置为空"):
        configure_steps([])


def test_configure_steps_rejects_duplicate_step_id():
    from EDPAgent.tool.lite_todo.models import configure_steps

    with pytest.raises(ValueError, match="step_id 重复"):
        configure_steps([
            {"step_id": 1, "content": "A", "skill": "skill_a"},
            {"step_id": 1, "content": "B", "skill": "skill_b"},
        ])


def test_configure_steps_rejects_negative_step_id():
    from EDPAgent.tool.lite_todo.models import configure_steps

    with pytest.raises(ValueError, match="必须 ≥ 1"):
        configure_steps([
            {"step_id": 0, "content": "A", "skill": "skill_a"},
        ])


def test_configure_steps_accepts_dict_or_pydantic_model():
    """configure_steps 兼容 dict 和 TodoStepConfig 两种输入。"""
    from EDPAgent.agent_rule import TodoStepConfig
    from EDPAgent.tool.lite_todo.models import configure_steps, get_canonical_steps

    # dict 形式
    configure_steps([{"step_id": 7, "content": "任意名", "skill": "skill_x"}])
    assert get_canonical_steps() == {7: "任意名"}

    # pydantic 实例形式
    configure_steps([TodoStepConfig(step_id=8, content="新任务", skill="skill_y")])
    assert get_canonical_steps() == {8: "新任务"}


def test_yaml_override_changes_step_id_enum():
    """配置任意 step_id 后，TodoItem 接受新 ID、拒绝旧默认 ID。"""
    from pydantic import ValidationError

    from EDPAgent.tool.lite_todo.models import TodoItem, configure_steps

    configure_steps([
        {"step_id": 10, "content": "新步骤 A", "skill": "skill_a"},
        {"step_id": 20, "content": "新步骤 B", "skill": "skill_b"},
    ])
    # 新 ID 通过
    item = TodoItem(step_id=10, status="pending")
    assert item.content == "新步骤 A"
    assert item.skill == "skill_a"

    # 默认 ID（1..4）现在不再有效
    with pytest.raises(ValidationError, match=r"step_id 必须在 \[10, 20\]"):
        TodoItem(step_id=1, status="pending")


def test_yaml_override_drives_tool_card_schema():
    """tool_card 的 schema enum 跟随当前激活配置。"""
    from EDPAgent.tool.lite_todo.models import configure_steps
    from EDPAgent.tool.lite_todo.tool_card import build_lite_todo_card

    configure_steps([
        {"step_id": 100, "content": "C", "skill": "s_c"},
        {"step_id": 200, "content": "D", "skill": "s_d"},
    ])
    card = build_lite_todo_card()
    # 取出 step_id 的 enum
    step_id_schema = card.input_params["properties"]["todos"]["items"]["properties"]["step_id"]
    assert step_id_schema["enum"] == [100, 200]


def test_load_agent_rule_populates_todolist_steps(tmp_path):
    """load_agent_rule 解析 frontmatter 里的 todolist_steps section。"""
    from EDPAgent.agent_rule import load_agent_rule

    rule_md = tmp_path / "rule.md"
    rule_md.write_text(
        """---
scope:
  allowed: "test"
  out_of_scope_message: "尚在学习中"
todolist_steps:
  - step_id: 1
    content: "推荐理财产品"
    skill: "rebuild_product_recommend_skill"
  - step_id: 2
    content: "交互式理财筛选"
    skill: "rebuild_interact_finance_rec_skill"
---
# Body
""",
        encoding="utf-8",
    )
    cfg = load_agent_rule(rule_md)
    assert len(cfg.todolist_steps) == 2
    assert cfg.todolist_steps[0].step_id == 1
    assert cfg.todolist_steps[0].content == "推荐理财产品"
    assert cfg.todolist_steps[0].skill == "rebuild_product_recommend_skill"
    assert cfg.todolist_steps[1].step_id == 2


def test_load_agent_rule_with_no_todolist_steps_returns_empty_list(tmp_path):
    """frontmatter 没有 todolist_steps section 时，AgentRuleConfig.todolist_steps 是空 list。"""
    from EDPAgent.agent_rule import load_agent_rule

    rule_md = tmp_path / "rule.md"
    rule_md.write_text(
        """---
scope:
  allowed: "test"
  out_of_scope_message: "尚在学习中"
---
# Body
""",
        encoding="utf-8",
    )
    cfg = load_agent_rule(rule_md)
    assert cfg.todolist_steps == []

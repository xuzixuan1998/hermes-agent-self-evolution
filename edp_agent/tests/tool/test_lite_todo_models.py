"""Unit tests for lite_todo models — step_id schema + Chinese status mapping (v2)."""
from __future__ import annotations

import pytest


def test_todo_status_has_two_values():
    """v2 status：pending / done（in_progress 与 skip_optional 都已下线；
    不打算做的步骤直接不放进 todos 即可，不需要"跳过"状态）。"""
    from EDPAgent.tool.lite_todo.models import TodoStatus

    assert TodoStatus.PENDING.value == "pending"
    assert TodoStatus.DONE.value == "done"
    assert {s.value for s in TodoStatus} == {"pending", "done"}


def test_todo_item_schema_step_id_status():
    """TodoItem 字段 = {step_id (1..4), status}；content 是计算属性，不是模型字段。"""
    from EDPAgent.tool.lite_todo.models import TodoItem, TodoStatus

    item = TodoItem(step_id=1, status=TodoStatus.PENDING)
    assert item.step_id == 1
    assert item.status == TodoStatus.PENDING
    assert item.content == "推荐理财产品"  # 由 step_id 反查
    # Schema 字段
    fields = set(TodoItem.model_fields.keys())
    assert fields == {"step_id", "status"}


def test_todo_item_rejects_step_id_out_of_range():
    """step_id 必须在 1..4；超界拒绝。"""
    from pydantic import ValidationError

    from EDPAgent.tool.lite_todo.models import TodoItem

    with pytest.raises(ValidationError):
        TodoItem(step_id=0, status="pending")
    with pytest.raises(ValidationError):
        TodoItem(step_id=5, status="pending")  # 4 项后 5 已无效


def test_todo_item_rejects_invalid_status():
    """status 限定为 pending / done；in_progress 与 skip_optional 都已下线。"""
    from pydantic import ValidationError

    from EDPAgent.tool.lite_todo.models import TodoItem

    with pytest.raises(ValidationError):
        TodoItem(step_id=1, status="in_progress")
    with pytest.raises(ValidationError):
        TodoItem(step_id=1, status="skip_optional")
    with pytest.raises(ValidationError):
        TodoItem(step_id=1, status="cancelled")


def test_canonical_steps_has_4_entries():
    """conftest 自动配置 4 个固定业务步骤（与默认理财业务流程对齐）。"""
    from EDPAgent.tool.lite_todo.models import get_canonical_steps

    canonical = get_canonical_steps()
    assert set(canonical.keys()) == {1, 2, 3, 4}
    assert canonical[1] == "推荐理财产品"
    assert canonical[2] == "交互式理财筛选"
    assert canonical[3] == "确定购买产品和金额"
    assert canonical[4] == "查询理财账户余额，如果资金不足进行资金筹划，并购买理财产品"


def test_step_to_skill_one_to_one_binding():
    """每个 step_id 与一个 skill 一一绑定，且与 canonical 同步。"""
    from EDPAgent.tool.lite_todo.models import get_canonical_steps, get_step_to_skill

    canonical = get_canonical_steps()
    skills = get_step_to_skill()
    assert set(skills.keys()) == set(canonical.keys())
    assert skills[1] == "rebuild_product_recommend_skill"
    assert skills[2] == "rebuild_interact_finance_rec_skill"
    assert skills[3] == "rebuild_product_select_skill"
    assert skills[4] == "model_driven_fund_planning_skill"


def test_todo_item_has_skill_property():
    """TodoItem.skill 计算属性反查 STEP_TO_SKILL。"""
    from EDPAgent.tool.lite_todo.models import TodoItem, TodoStatus

    item = TodoItem(step_id=3, status=TodoStatus.PENDING)
    assert item.skill == "rebuild_product_select_skill"
    assert item.content == "确定购买产品和金额"


def test_todo_status_cn_maps_three_values():
    """中文状态文案——渲染 talking_points content。"""
    from EDPAgent.tool.lite_todo.models import TODO_STATUS_CN

    assert TODO_STATUS_CN["pending"] == "待执行"
    assert TODO_STATUS_CN["done"] == "完成"
    assert set(TODO_STATUS_CN.keys()) == {"pending", "done"}

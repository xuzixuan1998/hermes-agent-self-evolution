"""ToolCard for lite_todo_write — schema 由 AgentRule.md todolist_steps 配置。

`LITE_TODO_CARD` 不再是模块级常量——访问会触发 ``build_lite_todo_card()`` 工厂；
未调用 ``configure_steps()`` 之前访问会抛 RuntimeError。
"""
from openjiuwen.core.foundation.tool import ToolCard

from .models import get_canonical_steps, get_step_to_skill


def _canonical_steps_doc() -> str:
    """生成给 LLM 看的步骤目录说明（基于当前 ``_active_steps``）。"""
    canonical = get_canonical_steps()
    skills = get_step_to_skill()
    lines = []
    for sid in sorted(canonical):
        lines.append(f"  - **{sid}** = `{canonical[sid]}`  → 调用 `{skills[sid]}`")
    return "\n".join(lines)


def _build_description() -> str:
    """运行时拼描述——必须在 configure_steps() 之后调用。"""
    return f"""
管理当前会话的待办清单——一次性传入完整列表，覆盖式更新。

**重要语义**：本工具的 todo 列表 = **本次任务即将依次调用的 skill 顺序**。
LLM 选择 step_id 等价于声明"我接下来会调用对应的 skill"。每个 step_id 与
一个 skill **一一绑定**，content 字符串和 skill 路由都是固定的——LLM 无需也
不能自定义步骤名。

## 何时使用

- 进入业务流程后**第一件事**就调用本工具发出 todo 列表
- 任何会调 ≥ 2 个 skill 的任务

## 何时不使用

- 单步信息查询、与买理财业务无关的请求

## 业务步骤目录（**只能引用以下 step_id**，禁止自创步骤）

{_canonical_steps_doc()}

## 调用格式

每次传入完整 todo 列表（覆盖现有状态）。`step_id` 必须从上面目录里挑；可以
**任意非空子集**：例如直接走推荐 → 选品 → 购买而跳过中间筛选，就传 `[1, 3, 4]`：

```json
{{
    "todos": [
        {{"step_id": 1, "status": "pending"}},
        {{"step_id": 3, "status": "pending"}},
        {{"step_id": 4, "status": "pending"}}
    ]
}}
```

## 字段

- `step_id`: 整数，**必须**是上面业务步骤目录中的某个 step_id
- `status`: 二选一
  - `pending` — 还没做
  - `done` — 已完成
  - **不需要**特殊"跳过"状态：不打算做的步骤直接**不放进** todos 即可

## 重要规则

1. **You MUST**：每次调用传入完整列表，覆盖式更新（不要只传变化的项）
2. **You MUST**：调用本工具后，可在同一响应内立即调用业务工具（call_versatile / call_mcp）执行下一步
3. **You MUST**：每个 step_id 在 todos 列表里只能出现一次；不要重复
4. **You MUST**：todos 列表只放打算做的项——不打算做的步骤**不要**放进列表
5. **You MUST**：进入"业务步骤目录"中某一项前，再次调用本工具把对应项 status 仍然标 `pending`（"正在执行哪一项"由框架的 todo_start/todo_status 事件单独表达，**不要**自己加 in_progress）；翻 `done` 必须对应该 step 绑定 skill 的一次实际成功执行（典型表现为 call_versatile / call_mcp `tool_end success=true`）。`ask_user` 收到用户回包**不构成**任何 step 的完成依据；用户回包后下一步必须是调用业务工具，而不是更新 todo
6. **You MUST**：项数 / 顺序由 LLM 决定，但 **content 由框架按 step_id 自动拼装**——你不要也不能写 content 字符串
7. 失败/取消由业务工具自身的 tool_end 反映，不需要在 todo 里建模
""".strip()


def _build_input_params() -> dict:
    canonical = get_canonical_steps()
    skills = get_step_to_skill()
    enum_ids = sorted(canonical.keys())
    desc = "业务步骤编号；只能从下列固定值里选（每个 step 与一个 skill 一一绑定）：" + "; ".join(
        f"{sid}={canonical[sid]}→{skills[sid]}" for sid in enum_ids
    )
    return {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "完整 todo 列表，每项 {step_id, status}",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {
                            "type": "integer",
                            "enum": enum_ids,
                            "description": desc,
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "done"],
                            "description": (
                                "任务状态。pending=待执行；done=已完成。"
                                "**禁止**使用 in_progress（运行中状态由 todo_status 单独承载）；"
                                "不打算做的步骤直接不放进 todos 列表，不需要特殊'跳过'状态。"
                            ),
                        },
                    },
                    "required": ["step_id", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }


def build_lite_todo_card() -> ToolCard:
    """工厂——读取当前 ``_active_steps`` 构建 ToolCard。

    必须在 ``configure_steps()`` 之后调用。未配置时本函数会抛 RuntimeError
    （由 ``get_canonical_steps()`` 抛出）。
    """
    return ToolCard(
        id="lite_todo_write",
        name="lite_todo_write",
        description=_build_description(),
        input_params=_build_input_params(),
    )


# 向后兼容：保留 LITE_TODO_DESCRIPTION / LITE_TODO_CARD 名称——
# 但访问触发动态构建（未配置则抛错）。
def __getattr__(name: str):
    if name == "LITE_TODO_CARD":
        return build_lite_todo_card()
    if name == "LITE_TODO_DESCRIPTION":
        return _build_description()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

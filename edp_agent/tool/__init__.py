"""Tools 入口。

``TOOLS`` 不再是模块级常量——`lite_todo_write` 工具的 schema 依赖 AgentRule.md
里的 ``todolist_steps`` 配置，必须在 ``configure_steps()`` 之后才能构建。

调用方应在 ``agent.initialize_dpa()`` 加载 AgentRule.md + 调 configure_steps()
之后再调 ``build_tools()`` 拿 list。
"""
from .ask_user import ask_user_tool
from .call_mcp import call_mcp_tool
from .call_versatile import call_versatile_tool
from .cancel_task import cancel_task_tool
from .lite_todo import lite_todo_tools


def build_tools() -> list:
    """运行时构建工具列表——必须在 ``configure_steps()`` 之后调用。

    抛错路径：未调用 configure_steps() 时 ``lite_todo_tools()`` 会触发
    `LiteTodoWriteTool()` 构造，进而抛 RuntimeError。
    """
    return [
        ask_user_tool,
        call_mcp_tool,
        call_versatile_tool,
        cancel_task_tool,
        *lite_todo_tools(),
    ]


# 向后兼容：保留 TOOLS 名字——访问触发 build_tools()。
def __getattr__(name: str):
    if name == "TOOLS":
        return build_tools()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ask_user_tool",
    "call_mcp_tool",
    "call_versatile_tool",
    "cancel_task_tool",
    "lite_todo_tools",
    "build_tools",
    "TOOLS",
]

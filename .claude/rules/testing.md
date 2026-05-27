# 测试规范

## 测试框架

- 使用 `pytest`，async 测试额外依赖 `pytest-asyncio`
- 配置文件在 `pyproject.toml` 的 `[tool.pytest.ini_options]`

## 文件组织

```
tests/
├── core/               # 对应 evolution/core/
│   ├── test_config.py
│   ├── test_fitness.py
│   ├── test_constraints.py
│   └── test_dataset_builder.py
├── skills/             # 对应 evolution/skills/
│   ├── test_evolve.py
│   └── test_skill_module.py
├── edp_agent/          # 对应 edp_agent/
│   └── test_agent.py
└── __init__.py
```

- 测试目录镜像源码目录结构
- 测试文件命名：`test_<module_name>.py`

## 测试类与方法命名

- 每个被测试模块/组件一个测试类，类名 `Test<ComponentName>`
- 测试方法：`test_<what_happens>_<expected_behavior>`
- 示例：`test_run_edp_agent_handles_exception`、`test_reload_preserves_frontmatter`

## Mock 策略

- 外部依赖（LLM API、Redis、文件系统）一律 mock
- 跨模块调用（如 `edp_agent.agent`）通过 `patch()` mock
- 重型第三方库（`openjiuwen`、`dspy`）在模块级别 mock
- Mock 辅助方法：`_make_mock_config()` 放在测试类内部
- 不 mock 被测试的目标函数本身

```python
class TestRunHermesAgent:
    def _make_mock_config(self):
        config = MagicMock()
        config.agent_model = "gpt-4"
        return config

    def test_run_hermes_agent_returns_dict(self):
        with patch("evolution.skills.skill_module.AIAgent") as mock:
            mock_instance = mock.return_value
            mock_instance.run_conversation.return_value = {
                "final_response": "ok",
                "messages": [],
            }
            result = run_hermes_agent("skill", "input", self._make_mock_config())
            assert result["completed"] is True
```

## 测试覆盖维度

每个新功能至少覆盖：

| 维度 | 说明 |
|---|---|
| Happy path | 正常输入输出 |
| 异常处理 | 依赖失败时返回安全默认值 |
| Edge case | 空输入、None 值、空集合 |
| 状态变更 | 模块级状态正确更新（如 `_edp_initialized`） |
| 副作用 | cleanup/release 在异常路径也被调用 |

## 测试原则

- 单元测试不调用真实 LLM API
- 不做多余的 assert — 关键字段验证即可
- 测试之间独立，不依赖执行顺序
- 避免 `pytest.mark.skip`，如果不需要就删掉

## 运行测试

```bash
# 全部测试
python -m pytest

# 特定文件
python -m pytest tests/edp_agent/test_agent.py -v

# 特定测试类
python -m pytest tests/skills/test_skill_module.py::TestRunEdpAgent -v
```

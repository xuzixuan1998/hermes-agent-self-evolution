"""Tests for edp_agent.agent.reload_agent_rule()."""

import sys
from unittest.mock import MagicMock, patch

# -- Mock heavy external deps before importing edp_agent.agent ------------------
_EXTERNAL_DEPS = [
    "loguru",
    "httpx",
    "openjiuwen",
    "openjiuwen.core",
    "openjiuwen.core.single_agent",
    "openjiuwen.core.single_agent.interrupt",
    "openjiuwen.core.single_agent.interrupt.state",
    "openjiuwen.extensions",
    "openjiuwen.extensions.checkpointer",
    "openjiuwen.extensions.checkpointer.redis",
    "openjiuwen.extensions.checkpointer.redis.checkpointer",
    "openjiuwen.core.runner",
    "openjiuwen.core.runner.runner_config",
    "openjiuwen.core.session",
    "openjiuwen.core.session.checkpointer",
    "openjiuwen.core.session.checkpointer.checkpointer",
    "openjiuwen.core.session.agent",
    "openjiuwen.core.sys_operation",
    "openjiuwen.core.sys_operation.config",
    "openjiuwen.extensions.sys_operation",
    "openjiuwen.extensions.sys_operation.sandbox",
    "openjiuwen.extensions.sys_operation.sandbox.providers",
    "common",
    "common.crypto",
    "common.events",
    "common.logger",
    "yaml",
    "pydantic",
]

for dep in _EXTERNAL_DEPS:
    if dep not in sys.modules:
        sys.modules[dep] = MagicMock()


class TestReloadAgentRule:
    """Unit tests for reload_agent_rule()."""

    def test_reload_updates_markdown_body(self):
        """reload sets _agent_rule.markdown_body and calls agent.configure."""
        import edp_agent.agent as agent_module

        mock_agent = MagicMock()
        mock_rule = MagicMock()
        mock_rule.markdown_body = "old body"
        mock_rule.limits.max_iterations = 30

        with patch.object(agent_module, "_agent", mock_agent), \
             patch.object(agent_module, "_agent_rule", mock_rule), \
             patch.object(agent_module, "build_system_prompt", return_value="sys prompt"):
            agent_module.reload_agent_rule("new body")

            assert mock_rule.markdown_body == "new body"
            mock_agent.configure.assert_called_once()

    def test_reload_preserves_frontmatter(self):
        """Only markdown_body is changed; scope/limits are preserved."""
        import edp_agent.agent as agent_module

        mock_agent = MagicMock()
        mock_rule = MagicMock()
        mock_rule.scope.allowed = "finance domain"
        mock_rule.limits.max_iterations = 30
        mock_rule.markdown_body = "old body"
        mock_rule.todolist_steps = []
        mock_rule.scripts = MagicMock()

        with patch.object(agent_module, "_agent", mock_agent), \
             patch.object(agent_module, "_agent_rule", mock_rule), \
             patch.object(agent_module, "build_system_prompt", return_value="sys prompt"):
            agent_module.reload_agent_rule("new body")

            assert mock_rule.scope.allowed == "finance domain"
            assert mock_rule.limits.max_iterations == 30

    def test_reload_agent_none_no_error(self):
        """_agent=None logs warning but does not raise."""
        import edp_agent.agent as agent_module

        with patch.object(agent_module, "_agent", None), \
             patch.object(agent_module, "_agent_rule", MagicMock()):
            # Should not raise
            agent_module.reload_agent_rule("new body")

    def test_reload_agent_rule_none_no_error(self):
        """_agent_rule=None logs warning but does not raise."""
        import edp_agent.agent as agent_module

        with patch.object(agent_module, "_agent", MagicMock()), \
             patch.object(agent_module, "_agent_rule", None):
            # Should not raise
            agent_module.reload_agent_rule("new body")

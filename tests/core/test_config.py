"""Tests for evolution.core.config."""

import pytest
from evolution.core.config import EvolutionConfig


class TestEvolutionConfigNewFields:
    """Test the new fields added to EvolutionConfig."""

    def test_config_defaults(self):
        """EvolutionConfig() defaults inference_mode='single-turn',
        evaluator='fast', agent_model=None, agent_max_iterations=10."""
        config = EvolutionConfig()
        assert config.inference_mode == "single-turn"
        assert config.evaluator == "fast"
        assert config.agent_model is None
        assert config.agent_max_iterations == 10

    def test_config_agent_model_fallback(self):
        """agent_model=None is valid (wiring layer handles fallback)."""
        config = EvolutionConfig(agent_model=None)
        assert config.agent_model is None

    def test_config_custom_values(self):
        """Setting inference_mode='hermes-agent', evaluator='llm-judge',
        agent_model='claude-sonnet', agent_max_iterations=15 works."""
        config = EvolutionConfig(
            inference_mode="hermes-agent",
            evaluator="llm-judge",
            agent_model="claude-sonnet",
            agent_max_iterations=15,
        )
        assert config.inference_mode == "hermes-agent"
        assert config.evaluator == "llm-judge"
        assert config.agent_model == "claude-sonnet"
        assert config.agent_max_iterations == 15

    def test_config_custom_does_not_affect_defaults(self):
        """Setting custom values on one config does not leak to defaults."""
        custom = EvolutionConfig(
            inference_mode="hermes-agent",
            evaluator="llm-judge",
            agent_model="claude-sonnet",
            agent_max_iterations=15,
        )
        default = EvolutionConfig()
        assert default.inference_mode == "single-turn"
        assert default.evaluator == "fast"
        assert default.agent_model is None
        assert default.agent_max_iterations == 10
        # Sanity check that custom is still custom
        assert custom.inference_mode == "hermes-agent"

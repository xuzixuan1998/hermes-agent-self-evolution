"""Real Hermes agent inference via AIAgent.run_conversation."""

import sys

from evolution.agents.base import BaseAgent


class HermesAgent(BaseAgent):
    """Execute a skill via real Hermes agent (AIAgent.run_conversation)."""

    def run(self, system_prompt: str, task_input: str, config) -> dict:
        if str(config.hermes_agent_path) not in sys.path:
            sys.path.insert(0, str(config.hermes_agent_path))

        from run_agent import AIAgent

        model = config.agent_model or config.optimizer_model

        agent = AIAgent(
            model=model,
            quiet_mode=True,
            max_iterations=config.agent_max_iterations,
            enabled_toolsets=["terminal", "web"],
        )

        try:
            result = agent.run_conversation(
                user_message=task_input,
                system_message=system_prompt,
            )
            return {
                "output": result.get("final_response", "") or "",
                "messages": result.get("messages", []),
                "completed": True,
            }
        except Exception:
            return {"output": "", "messages": [], "completed": False}

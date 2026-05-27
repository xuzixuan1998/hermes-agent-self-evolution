"""Single-turn LLM inference via dspy.ChainOfThought — fastest, no tool use."""

import dspy

from evolution.agents.base import BaseAgent


class SingleTurnAgent(BaseAgent):
    """Execute a skill as a single-turn LLM call using dspy.ChainOfThought."""

    class _Signature(dspy.Signature):
        """Complete a task following the provided skill instructions."""
        skill_instructions: str = dspy.InputField(desc="The skill instructions to follow")
        task_input: str = dspy.InputField(desc="The task to complete")
        output: str = dspy.OutputField(desc="Your response following the skill instructions")

    def run(self, system_prompt: str, task_input: str, config) -> dict:
        predictor = dspy.ChainOfThought(self._Signature)
        result = predictor(skill_instructions=system_prompt, task_input=task_input)

        messages = [
            {"role": "user", "content": task_input},
            {"role": "assistant", "content": getattr(result, "output", "") or ""},
        ]
        return {
            "output": getattr(result, "output", "") or "",
            "messages": messages,
            "completed": True,
        }

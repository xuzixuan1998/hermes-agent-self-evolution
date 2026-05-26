"""Wraps a SKILL.md file as a DSPy module for optimization.

The key abstraction: a skill file becomes a parameterized DSPy module
where the skill text is the optimizable parameter. GEPA can then
mutate the skill text and evaluate the results.
"""

import re
import sys
from pathlib import Path
from typing import Optional

import dspy


def run_single_turn(skill_text: str, task_input: str, config) -> dict:
    """Execute a skill as a single-turn LLM call using dspy.ChainOfThought.

    Returns unified dict: {"output": str, "messages": list[dict], "completed": bool}
    """
    class _SingleTurn(dspy.Signature):
        """Complete a task following the provided skill instructions."""
        skill_instructions: str = dspy.InputField(desc="The skill instructions to follow")
        task_input: str = dspy.InputField(desc="The task to complete")
        output: str = dspy.OutputField(desc="Your response following the skill instructions")

    predictor = dspy.ChainOfThought(_SingleTurn)
    result = predictor(skill_instructions=skill_text, task_input=task_input)

    messages = [
        {"role": "user", "content": task_input},
        {"role": "assistant", "content": getattr(result, "output", "") or ""},
    ]
    return {"output": getattr(result, "output", "") or "", "messages": messages, "completed": True}


def run_hermes_agent(skill_text: str, task_input: str, config) -> dict:
    """Execute a skill via real Hermes agent (AIAgent.run_conversation).

    Returns unified dict: {"output": str, "messages": list[dict], "completed": bool}
    """
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
            system_message=skill_text,
        )
        return {
            "output": result.get("final_response", "") or "",
            "messages": result.get("messages", []),
            "completed": True,
        }
    except Exception:
        return {"output": "", "messages": [], "completed": False}


def load_skill(skill_path: Path) -> dict:
    """Load a skill file and parse its frontmatter + body.

    Returns:
        {
            "path": Path,
            "raw": str (full file content),
            "frontmatter": str (YAML between --- markers),
            "body": str (markdown after frontmatter),
            "name": str,
            "description": str,
        }
    """
    raw = skill_path.read_text()

    # Parse YAML frontmatter
    frontmatter = ""
    body = raw
    if raw.strip().startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()

    # Extract name and description from frontmatter
    name = ""
    description = ""
    for line in frontmatter.split("\n"):
        if line.strip().startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
        elif line.strip().startswith("description:"):
            description = line.split(":", 1)[1].strip().strip("'\"")

    return {
        "path": skill_path,
        "raw": raw,
        "frontmatter": frontmatter,
        "body": body,
        "name": name,
        "description": description,
    }


def find_skill(skill_name: str, hermes_agent_path: Path) -> Optional[Path]:
    """Find a skill by name in the hermes-agent skills directory.

    Searches recursively for a SKILL.md in a directory matching the skill name.
    """
    skills_dir = hermes_agent_path / "skills"
    if not skills_dir.exists():
        return None

    # Direct match: skills/<category>/<skill_name>/SKILL.md
    for skill_md in skills_dir.rglob("SKILL.md"):
        if skill_md.parent.name == skill_name:
            return skill_md

    # Fuzzy match: check the name field in frontmatter
    for skill_md in skills_dir.rglob("SKILL.md"):
        try:
            content = skill_md.read_text()[:500]
            if f"name: {skill_name}" in content or f'name: "{skill_name}"' in content:
                return skill_md
        except Exception:
            continue

    return None


class SkillModule(dspy.Module):
    """A DSPy module that wraps a skill file for optimization.

    The skill text (body) is the parameter that GEPA optimizes.
    On each forward pass, the module:
    1. Uses the skill text as instructions
    2. Processes the task input
    3. Returns the agent's response
    """

    class TaskWithSkill(dspy.Signature):
        """Complete a task following the provided skill instructions.

        You are an AI agent following specific skill instructions to complete a task.
        Read the skill instructions carefully and follow the procedure described.
        """
        skill_instructions: str = dspy.InputField(desc="The skill instructions to follow")
        task_input: str = dspy.InputField(desc="The task to complete")
        output: str = dspy.OutputField(desc="Your response following the skill instructions")

    def __init__(self, skill_text: str):
        super().__init__()
        self.skill_text = skill_text
        self.predictor = dspy.ChainOfThought(self.TaskWithSkill)

    def forward(self, task_input: str) -> dspy.Prediction:
        result = self.predictor(
            skill_instructions=self.skill_text,
            task_input=task_input,
        )
        return dspy.Prediction(output=result.output)


def reassemble_skill(frontmatter: str, evolved_body: str) -> str:
    """Reassemble a skill file from frontmatter and evolved body.

    Preserves the original YAML frontmatter (name, description, metadata)
    and replaces only the body with the evolved version.
    """
    return f"---\n{frontmatter}\n---\n\n{evolved_body}\n"

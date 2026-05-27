"""EDPAgent inference backend via remote HTTP API."""

import os
import logging

import httpx

from evolution.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class EDPAgent(BaseAgent):
    """Execute agent inference via remote EDP HTTP API."""

    def __init__(self):
        self._infer_url = os.environ["EDP_INFER_URL"]
        self._agentrule_update_url = os.environ["EDP_AGENTRULE_UPDATE_URL"]
        self._skill_update_url = os.environ.get("EDP_SKILL_UPDATE_URL", "")

    def update_agentrule(self, body: str) -> None:
        httpx.post(self._agentrule_update_url, json={"body": body}).raise_for_status()

    def update_skill(self, name: str, body: str) -> None:
        if not self._skill_update_url:
            return
        httpx.post(self._skill_update_url, json={"name": name, "body": body}).raise_for_status()

    def infer(self, task_input: str) -> dict:
        resp = httpx.post(self._infer_url, json={"query": task_input})
        resp.raise_for_status()
        return resp.json()

    def run(self, system_prompt: str, task_input: str, config) -> dict:
        try:
            self.update_agentrule(system_prompt)
            return self.infer(task_input)
        except Exception:
            logger.exception("EDPAgent.run failed")
            return {"output": "", "messages": [], "completed": False}

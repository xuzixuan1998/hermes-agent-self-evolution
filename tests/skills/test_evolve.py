"""Integration tests for evolution.skills.evolve_skill.evolve().

All code paths go through gepa.optimize() + EvolutionAdapter.
All external dependencies are mocked at the test level; no real LLM calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolution.skills.evolve_skill import evolve
from evolution.core.dataset_builder import EvalDataset, EvalExample

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SAMPLE_FRONTMATTER = "name: test\ndescription: test skill"
SAMPLE_BODY = "# Test Skill\n\n## Procedure\n1. Do the thing\n2. Verify\n"
SAMPLE_SKILL_MD = ("---\n" + SAMPLE_FRONTMATTER + "\n---\n\n" + SAMPLE_BODY)


def _skill_dict(path):
    return {
        "path": path, "raw": SAMPLE_SKILL_MD,
        "frontmatter": SAMPLE_FRONTMATTER, "body": SAMPLE_BODY,
        "name": "test", "description": "test skill",
    }


def _make_dataset():
    return EvalDataset(
        train=[EvalExample(task_input="t", expected_behavior="e")],
        val=[EvalExample(task_input="v", expected_behavior="e")],
        holdout=[EvalExample(task_input="h", expected_behavior="e")],
    )


def _passing_constraints():
    return [
        MagicMock(passed=True, constraint_name=f"c{i}", message="ok")
        for i in range(4)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvolveDryRun:
    def test_dry_run_returns_cleanly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sf = tmp_path / "SKILL.md"
        sf.write_text(SAMPLE_SKILL_MD)

        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(sf)),
        ):
            evolve(skill_name="test", dry_run=True,
                   inference_mode="hermes-agent", evaluator="llm-judge")

    def test_dry_run_accepts_edp_agent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sf = tmp_path / "SKILL.md"
        sf.write_text(SAMPLE_SKILL_MD)

        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(sf)),
        ):
            evolve(skill_name="test", dry_run=True, inference_mode="edp-agent")


class TestEvolveGepaPath:
    """All inference/evaluator combos use gepa.optimize + EvolutionAdapter."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.sf = tmp_path / "SKILL.md"
        self.sf.write_text(SAMPLE_SKILL_MD)

    def _run_gepa(self, **kw):
        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=self.sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=self.sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(self.sf)),
            patch("evolution.skills.evolve_skill.SyntheticDatasetBuilder") as builder,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
            patch("evolution.skills.evolve_skill.EvolutionAdapter") as adapter_cls,
            patch("evolution.skills.evolve_skill._keyword_overlap", return_value=0.75),
            patch("gepa.optimize") as mock_optimize,
        ):
            builder.return_value.generate.return_value = _make_dataset()
            validator.return_value.validate_all.return_value = _passing_constraints()

            mock_agent = MagicMock()
            mock_agent.run.return_value = {"output": "mock output", "messages": [], "completed": True}
            adapter = MagicMock()
            adapter.agent = mock_agent
            adapter.trajectories = []
            adapter.judge.score.return_value.composite = 0.85
            adapter_cls.return_value = adapter

            mock_optimize.return_value = MagicMock(
                candidates=[{"artifact_body": "evolved gepa body"}])

            evolve(skill_name="test", iterations=4, **kw)

    def test_single_turn_with_fast(self):
        """single-turn + fast (formerly legacy path) now uses gepa.optimize."""
        self._run_gepa(inference_mode="single-turn", evaluator="fast")
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1

    def test_hermes_agent_with_llm_judge(self):
        self._run_gepa(inference_mode="hermes-agent", evaluator="llm-judge")
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1
        latest = sorted(output_roots)[-1]
        metrics = json.loads((latest / "metrics.json").read_text())
        assert metrics["inference_mode"] == "hermes-agent"
        assert metrics["evaluator"] == "llm-judge"

    def test_hermes_agent_with_fast_evaluator(self):
        self._run_gepa(inference_mode="hermes-agent", evaluator="fast")
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1

    def test_single_turn_with_llm_judge(self):
        self._run_gepa(inference_mode="single-turn", evaluator="llm-judge")
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1

    def test_config_json_written(self):
        self._run_gepa(inference_mode="hermes-agent", evaluator="llm-judge",
                       agent_model="claude-sonnet", agent_max_iterations=15)
        output_roots = list(Path("output").glob("test/*"))
        latest = sorted(output_roots)[-1]
        cfg = json.loads((latest / "config.json").read_text())
        assert cfg["inference_mode"] == "hermes-agent"
        assert cfg["evaluator"] == "llm-judge"
        assert cfg["agent_model"] == "claude-sonnet"
        assert cfg["agent_max_iterations"] == 15

    def test_output_files_created(self):
        self._run_gepa(inference_mode="hermes-agent", evaluator="fast")
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1
        latest = sorted(output_roots)[-1]
        assert (latest / "evolved_skill.md").exists()
        assert (latest / "metrics.json").exists()
        assert (latest / "config.json").exists()

    def test_dataset_generated_alongside(self):
        self._run_gepa(inference_mode="single-turn", evaluator="fast")
        assert (Path("datasets") / "skills" / "test" / "train.jsonl").exists()


class TestEvolveEdgeCases:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.sf = tmp_path / "SKILL.md"
        self.sf.write_text(SAMPLE_SKILL_MD)

    def test_find_skill_not_found_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("evolution.skills.evolve_skill.find_skill", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                evolve(skill_name="nonexistent")
            assert exc_info.value.code == 1

    def test_constraint_failure_saves_failed_file(self):
        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=self.sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=self.sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(self.sf)),
            patch("evolution.skills.evolve_skill.SyntheticDatasetBuilder") as builder,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
            patch("evolution.skills.evolve_skill.EvolutionAdapter") as adapter_cls,
            patch("evolution.skills.evolve_skill._keyword_overlap", return_value=0.5),
            patch("gepa.optimize") as mock_optimize,
        ):
            builder.return_value.generate.return_value = _make_dataset()
            validator.return_value.validate_all.side_effect = [
                _passing_constraints(),
                [MagicMock(passed=False, constraint_name="growth", message="fail")],
            ]

            mock_agent = MagicMock()
            mock_agent.run.return_value = {"output": "mock", "messages": [], "completed": True}
            adapter = MagicMock()
            adapter.agent = mock_agent
            adapter.trajectories = []
            adapter.judge.score.return_value.composite = 0.5
            adapter_cls.return_value = adapter

            mock_optimize.return_value = MagicMock(
                candidates=[{"artifact_body": "evolved body"}])

            evolve(skill_name="test", iterations=2,
                   inference_mode="single-turn", evaluator="fast")

        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1
        latest = sorted(output_roots)[-1]
        assert (latest / "evolved_FAILED.md").exists()

    def test_dry_run_does_not_call_any_optimizer(self):
        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=self.sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=self.sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(self.sf)),
            patch("gepa.optimize") as mock_optimize,
        ):
            evolve(skill_name="test", dry_run=True,
                   inference_mode="single-turn", evaluator="fast")
        mock_optimize.assert_not_called()


class TestEvolveGoldenDatasetSource:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.sf = tmp_path / "SKILL.md"
        self.sf.write_text(SAMPLE_SKILL_MD)
        self.ddir = tmp_path / "golden"
        self.ddir.mkdir()
        for s in ("train", "val", "holdout"):
            (self.ddir / f"{s}.jsonl").write_text(
                json.dumps({"task_input": f"{s}_in", "expected_behavior": f"{s}_ex"}) + "\n")

    def test_golden_dataset_loaded(self):
        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=self.sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=self.sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(self.sf)),
            patch("evolution.skills.evolve_skill.GoldenDatasetLoader") as loader,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
            patch("evolution.skills.evolve_skill.EvolutionAdapter") as adapter_cls,
            patch("evolution.skills.evolve_skill._keyword_overlap", return_value=0.75),
            patch("gepa.optimize") as mock_optimize,
        ):
            loader.load.return_value = _make_dataset()
            validator.return_value.validate_all.return_value = _passing_constraints()

            mock_agent = MagicMock()
            mock_agent.run.return_value = {"output": "mock", "messages": [], "completed": True}
            adapter = MagicMock()
            adapter.agent = mock_agent
            adapter.trajectories = []
            adapter.judge.score.return_value.composite = 0.75
            adapter_cls.return_value = adapter

            mock_optimize.return_value = MagicMock(
                candidates=[{"artifact_body": "evolved"}])

            evolve(skill_name="test", eval_source="golden",
                   dataset_path=str(self.ddir), iterations=2,
                   inference_mode="single-turn", evaluator="fast")

        loader.load.assert_called_once()
        mock_optimize.assert_called_once()

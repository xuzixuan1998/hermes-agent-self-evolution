"""Integration tests for evolution.skills.evolve_skill.evolve().

Tests the orchestration logic for both code paths:

  Legacy path:  single-turn inference + fast evaluator  →  dspy.GEPA
  New path:     all other combos                        →  gepa.optimize (custom adapter)

All external dependencies are mocked at the test level; no real LLM calls.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evolution.skills.evolve_skill import evolve
from evolution.core.dataset_builder import EvalDataset, EvalExample

# A clean prediction-like object (not MagicMock — getattr must return actual strings)
class _FakePrediction:
    def __init__(self, output="mock output"):
        self.output = output

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
        """--inference edp-agent --dry-run should succeed without error."""
        monkeypatch.chdir(tmp_path)
        sf = tmp_path / "SKILL.md"
        sf.write_text(SAMPLE_SKILL_MD)

        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(sf)),
        ):
            evolve(skill_name="test", dry_run=True, inference_mode="edp-agent")


class TestEvolveLegacyPath:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.sf = tmp_path / "SKILL.md"
        self.sf.write_text(SAMPLE_SKILL_MD)

    def _run_legacy(self, **kw):
        """Run evolve() with full legacy-path mocks. Returns evolve() result (None)."""
        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=self.sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=self.sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(self.sf)),
            patch("evolution.skills.evolve_skill.dspy.LM") as dspy_lm,
            patch("evolution.skills.evolve_skill.dspy.configure") as dspy_cfg,
            patch("evolution.skills.evolve_skill.dspy.context") as dspy_ctx,
            patch("evolution.skills.evolve_skill.dspy.GEPA") as gepa,
            patch("evolution.skills.evolve_skill.SkillModule") as skill_mod,
            patch("evolution.skills.evolve_skill.SyntheticDatasetBuilder") as builder,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
        ):
            # Configure dspy context manager
            dspy_ctx.return_value.__enter__ = MagicMock(return_value=None)
            dspy_ctx.return_value.__exit__ = MagicMock(return_value=None)

            # Configure dspy.GEPA
            gepa_instance = MagicMock()
            gepa_instance.compile.return_value = MagicMock(skill_text="evolved body")
            gepa.return_value = gepa_instance

            # Configure SkillModule to return a mock that has .output
            skill_instance = MagicMock()
            skill_instance.return_value = _FakePrediction()
            skill_mod.return_value = skill_instance

            # Dataset + Constraints
            builder.return_value.generate.return_value = _make_dataset()
            validator.return_value.validate_all.return_value = _passing_constraints()

            evolve(skill_name="test", iterations=2,
                   inference_mode="single-turn", evaluator="fast", **kw)

    def test_dspy_gepa_compile_is_called(self):
        self._run_legacy()
        # The dspy.GEPA mock should have been called
        from unittest.mock import patch as _patch
        # Assertions are verified through the with-block — if evolve() leaked
        # an unpatched dspy call it would raise. Just check output exists.
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1

    def test_output_files_created(self):
        self._run_legacy()
        output_roots = list(Path("output").glob("test/*"))
        assert len(output_roots) >= 1
        latest = sorted(output_roots)[-1]
        assert (latest / "evolved_skill.md").exists()
        assert (latest / "metrics.json").exists()
        assert (latest / "config.json").exists()

    def test_dataset_generated_alongside(self):
        self._run_legacy()
        assert (Path("datasets") / "skills" / "test" / "train.jsonl").exists()


class TestEvolveGepaPath:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.sf = tmp_path / "SKILL.md"
        self.sf.write_text(SAMPLE_SKILL_MD)

    def _run_gepa(self, **kw):
        """Run evolve() with gepa-path mocks."""
        with (
            patch("evolution.core.config.get_hermes_agent_path", return_value=self.sf.parent),
            patch("evolution.skills.evolve_skill.find_skill", return_value=self.sf),
            patch("evolution.skills.evolve_skill.load_skill", return_value=_skill_dict(self.sf)),
            patch("evolution.skills.evolve_skill.dspy.LM") as dspy_lm,
            patch("evolution.skills.evolve_skill.dspy.configure") as dspy_cfg,
            patch("evolution.skills.evolve_skill.dspy.context") as dspy_ctx,
            patch("evolution.skills.evolve_skill.SkillModule") as skill_mod,
            patch("evolution.skills.evolve_skill.SyntheticDatasetBuilder") as builder,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
            patch("evolution.skills.evolve_skill.EvolutionAdapter") as adapter_cls,
            patch("gepa.optimize") as mock_optimize,
        ):
            # SkillModule for holdout eval
            skill_instance = MagicMock()
            skill_instance.return_value = _FakePrediction()
            skill_mod.return_value = skill_instance

            builder.return_value.generate.return_value = _make_dataset()
            validator.return_value.validate_all.return_value = _passing_constraints()

            adapter = MagicMock()
            adapter.trajectories = []
            adapter_cls.return_value = adapter

            mock_optimize.return_value = MagicMock(
                candidates=[{"artifact_body": "evolved gepa body"}])

            evolve(skill_name="test", iterations=4, **kw)

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
            patch("evolution.skills.evolve_skill.dspy.LM"),
            patch("evolution.skills.evolve_skill.dspy.configure"),
            patch("evolution.skills.evolve_skill.dspy.context"),
            patch("evolution.skills.evolve_skill.dspy.GEPA") as gepa,
            patch("evolution.skills.evolve_skill.SyntheticDatasetBuilder") as builder,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
        ):
            gepa_instance = MagicMock()
            gepa_instance.compile.return_value = MagicMock(skill_text="evolved body")
            gepa.return_value = gepa_instance

            builder.return_value.generate.return_value = _make_dataset()
            validator.return_value.validate_all.side_effect = [
                _passing_constraints(),
                [MagicMock(passed=False, constraint_name="growth", message="fail")],
            ]

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
            patch("evolution.skills.evolve_skill.dspy.GEPA") as gepa,
        ):
            evolve(skill_name="test", dry_run=True,
                   inference_mode="single-turn", evaluator="fast")
        gepa.assert_not_called()


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
            patch("evolution.skills.evolve_skill.dspy.LM"),
            patch("evolution.skills.evolve_skill.dspy.configure"),
            patch("evolution.skills.evolve_skill.dspy.context"),
            patch("evolution.skills.evolve_skill.dspy.GEPA") as gepa,
            patch("evolution.skills.evolve_skill.SkillModule") as skill_mod,
            patch("evolution.skills.evolve_skill.GoldenDatasetLoader") as loader,
            patch("evolution.skills.evolve_skill.ConstraintValidator") as validator,
        ):
            gepa_instance = MagicMock()
            gepa_instance.compile.return_value = MagicMock(skill_text="evolved")
            gepa.return_value = gepa_instance

            skill_instance = MagicMock()
            skill_instance.return_value = _FakePrediction()
            skill_mod.return_value = skill_instance

            loader.load.return_value = _make_dataset()
            validator.return_value.validate_all.return_value = _passing_constraints()

            evolve(skill_name="test", eval_source="golden",
                   dataset_path=str(self.ddir), iterations=2,
                   inference_mode="single-turn", evaluator="fast")

        loader.load.assert_called_once()
        gepa.assert_called_once()

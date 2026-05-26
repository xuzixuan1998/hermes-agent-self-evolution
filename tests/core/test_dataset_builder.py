"""Tests for evolution.core.dataset_builder."""

import pytest
from evolution.core.dataset_builder import EvalDataset, EvalExample


class TestEvalDatasetToGepaDataInst:
    """Tests for EvalDataset.to_gepa_datainst()."""

    def test_to_gepa_datainst_format(self):
        """Create EvalDataset with 2 EvalExample in train split.
        Returns list of 2 dicts with keys input, answer, additional_context.
        input maps to task_input, answer maps to expected_behavior."""
        examples = [
            EvalExample(
                task_input="Write a poem about AI",
                expected_behavior="Poem should mention neural networks, learning, and creativity",
            ),
            EvalExample(
                task_input="Explain quantum computing",
                expected_behavior="Should cover superposition and entanglement at a high level",
            ),
        ]
        dataset = EvalDataset(train=examples)
        result = dataset.to_gepa_datainst("train")

        assert isinstance(result, list)
        assert len(result) == 2

        for i, item in enumerate(result):
            assert isinstance(item, dict)
            assert set(item.keys()) == {"input", "answer", "additional_context"}, (
                f"Item {i} has unexpected keys: {set(item.keys())}"
            )

        assert result[0]["input"] == "Write a poem about AI"
        assert result[0]["answer"] == "Poem should mention neural networks, learning, and creativity"
        assert result[1]["input"] == "Explain quantum computing"
        assert result[1]["answer"] == "Should cover superposition and entanglement at a high level"

    def test_to_gepa_datainst_empty_split(self):
        """Call to_gepa_datainst on empty split, verify returns empty list."""
        dataset = EvalDataset(train=[])
        result = dataset.to_gepa_datainst("train")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_to_gepa_datainst_additional_context(self):
        """Verify additional_context contains difficulty and category fields."""
        examples = [
            EvalExample(
                task_input="Debug this Python code",
                expected_behavior="Should identify the off-by-one error",
                difficulty="hard",
                category="debugging",
            ),
            EvalExample(
                task_input="Write a bash script to rename files",
                expected_behavior="Should use a for loop with mv command",
                difficulty="medium",
                category="scripting",
            ),
        ]
        dataset = EvalDataset(train=examples)
        result = dataset.to_gepa_datainst("train")

        assert len(result) == 2

        ctx0 = result[0]["additional_context"]
        assert isinstance(ctx0, dict)
        assert ctx0["difficulty"] == "hard"
        assert ctx0["category"] == "debugging"

        ctx1 = result[1]["additional_context"]
        assert isinstance(ctx1, dict)
        assert ctx1["difficulty"] == "medium"
        assert ctx1["category"] == "scripting"

    def test_to_gepa_datainst_default_additional_context(self):
        """EvalExample with default difficulty/category produces expected
        additional_context values."""
        examples = [
            EvalExample(
                task_input="Say hello",
                expected_behavior="Should greet the user",
            ),
        ]
        dataset = EvalDataset(train=examples)
        result = dataset.to_gepa_datainst("train")

        ctx = result[0]["additional_context"]
        assert ctx["difficulty"] == "medium"
        assert ctx["category"] == "general"

    def test_to_gepa_datainst_other_split(self):
        """to_gepa_datainst works on val and holdout splits."""
        train_ex = [EvalExample(task_input="train_q", expected_behavior="train_a")]
        val_ex = [EvalExample(task_input="val_q", expected_behavior="val_a")]
        holdout_ex = [EvalExample(task_input="hold_q", expected_behavior="hold_a")]

        dataset = EvalDataset(train=train_ex, val=val_ex, holdout=holdout_ex)

        train_result = dataset.to_gepa_datainst("train")
        assert len(train_result) == 1
        assert train_result[0]["input"] == "train_q"

        val_result = dataset.to_gepa_datainst("val")
        assert len(val_result) == 1
        assert val_result[0]["input"] == "val_q"

        holdout_result = dataset.to_gepa_datainst("holdout")
        assert len(holdout_result) == 1
        assert holdout_result[0]["input"] == "hold_q"

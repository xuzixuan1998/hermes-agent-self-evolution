"""Evolve a Hermes Agent skill using DSPy + GEPA.

Usage:
    python -m evolution.skills.evolve_skill --skill github-code-review --iterations 10
    python -m evolution.skills.evolve_skill --skill arxiv --eval-source golden --dataset datasets/skills/arxiv/
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from evolution.core.config import EvolutionConfig
from evolution.core.dataset_builder import SyntheticDatasetBuilder, EvalDataset, GoldenDatasetLoader
from evolution.core.external_importers import build_dataset_from_external
from evolution.core.fitness import EvolutionAdapter, _keyword_overlap
from evolution.core.constraints import ConstraintValidator
from evolution.skills.skill_module import (
    load_skill,
    find_skill,
    reassemble_skill,
)

console = Console()


def _save_output_artifacts(output_dir, skill_name, timestamp, iterations,
                           inference_mode, evaluator, agent_model, optimizer_model,
                           eval_model, dataset, skill, evolved_body, elapsed,
                           all_pass, avg_baseline, avg_evolved, improvement,
                           eval_source, agent_max_iterations, trajectory_records,
                           console):
    """Save all output artifacts: skill files, metrics, config, trajectories."""
    evolved_full = reassemble_skill(skill["frontmatter"], evolved_body)

    (output_dir / "evolved_skill.md").write_text(evolved_full)
    (output_dir / "baseline_skill.md").write_text(skill["raw"])

    metrics = {
        "skill_name": skill_name, "timestamp": timestamp, "iterations": iterations,
        "inference_mode": inference_mode, "evaluator": evaluator,
        "agent_model": agent_model or optimizer_model,
        "agent_max_iterations": agent_max_iterations,
        "optimizer_model": optimizer_model, "eval_model": eval_model,
        "baseline_score": avg_baseline, "evolved_score": avg_evolved,
        "improvement": improvement,
        "baseline_size": len(skill["body"]), "evolved_size": len(evolved_body),
        "train_examples": len(dataset.train), "val_examples": len(dataset.val),
        "holdout_examples": len(dataset.holdout),
        "elapsed_seconds": elapsed, "constraints_passed": all_pass,
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    config_snapshot = {
        "inference_mode": inference_mode, "evaluator": evaluator,
        "agent_model": agent_model or optimizer_model,
        "agent_max_iterations": agent_max_iterations,
        "optimizer_model": optimizer_model, "eval_model": eval_model,
        "iterations": iterations, "eval_source": eval_source,
        "timestamp": timestamp, "skill_name": skill_name,
    }
    (output_dir / "config.json").write_text(json.dumps(config_snapshot, indent=2))

    if trajectory_records:
        with open(output_dir / "trajectories.jsonl", "w") as f:
            for rec in trajectory_records:
                f.write(json.dumps(rec, default=str) + "\n")
        console.print(f"  Trajectories saved: {len(trajectory_records)} records")

    console.print(f"\n  Output saved to {output_dir}/")


def evolve(
    skill_name: str,
    iterations: int = 10,
    eval_source: str = "synthetic",
    dataset_path: Optional[str] = None,
    optimizer_model: str = "openai/gpt-4.1",
    eval_model: str = "openai/gpt-4.1-mini",
    hermes_repo: Optional[str] = None,
    run_tests: bool = False,
    dry_run: bool = False,
    inference_mode: str = "single-turn",
    evaluator: str = "fast",
    agent_model: Optional[str] = None,
    agent_max_iterations: int = 10,
):
    """Main evolution function — orchestrates the full optimization loop."""

    config = EvolutionConfig(
        iterations=iterations,
        optimizer_model=optimizer_model,
        eval_model=eval_model,
        judge_model=eval_model,  # Use same model for dataset generation
        run_pytest=run_tests,
        inference_mode=inference_mode,
        evaluator=evaluator,
        agent_model=agent_model,
        agent_max_iterations=agent_max_iterations,
        skill_name=skill_name,
    )
    if hermes_repo:
        config.hermes_agent_path = Path(hermes_repo)

    # ── 1. Find and load the skill ──────────────────────────────────────
    console.print(f"\n[bold cyan]🧬 Hermes Agent Self-Evolution[/bold cyan] — Evolving skill: [bold]{skill_name}[/bold]\n")

    skill_path = find_skill(skill_name, config.hermes_agent_path)
    if not skill_path:
        console.print(f"[red]✗ Skill '{skill_name}' not found in {config.hermes_agent_path / 'skills'}[/red]")
        sys.exit(1)

    skill = load_skill(skill_path)
    console.print(f"  Loaded: {skill_path.relative_to(config.hermes_agent_path)}")
    console.print(f"  Name: {skill['name']}")
    console.print(f"  Size: {len(skill['raw']):,} chars")
    console.print(f"  Description: {skill['description'][:80]}...")

    if dry_run:
        console.print(f"\n[bold green]DRY RUN — setup validated successfully.[/bold green]")
        console.print(f"  Inference: {inference_mode}, Evaluator: {evaluator}")
        if inference_mode == "hermes-agent":
            agent_fallback = agent_model or optimizer_model
            console.print(f"  Agent model: {agent_fallback} (max {agent_max_iterations} iterations)")
        console.print(f"  Would generate eval dataset (source: {eval_source})")
        console.print(f"  Would run GEPA optimization ({iterations} iterations)")
        console.print(f"  Would validate constraints and create PR")
        return

    # ── 2. Build or load evaluation dataset ─────────────────────────────
    console.print(f"\n[bold]Building evaluation dataset[/bold] (source: {eval_source})")

    if eval_source == "golden" and dataset_path:
        dataset = GoldenDatasetLoader.load(Path(dataset_path))
        console.print(f"  Loaded golden dataset: {len(dataset.all_examples)} examples")
    elif eval_source == "sessiondb":
        save_path = Path(dataset_path) if dataset_path else Path("datasets") / "skills" / skill_name
        dataset = build_dataset_from_external(
            skill_name=skill_name,
            skill_text=skill["raw"],
            sources=["claude-code", "copilot", "hermes"],
            output_path=save_path,
            model=eval_model,
        )
        if not dataset.all_examples:
            console.print("[red]✗ No relevant examples found from session history[/red]")
            sys.exit(1)
        console.print(f"  Mined {len(dataset.all_examples)} examples from session history")
    elif eval_source == "synthetic":
        builder = SyntheticDatasetBuilder(config)
        dataset = builder.generate(
            artifact_text=skill["raw"],
            artifact_type="skill",
        )
        # Save for reuse
        save_path = Path("datasets") / "skills" / skill_name
        dataset.save(save_path)
        console.print(f"  Generated {len(dataset.all_examples)} synthetic examples")
        console.print(f"  Saved to {save_path}/")
    elif dataset_path:
        dataset = EvalDataset.load(Path(dataset_path))
        console.print(f"  Loaded dataset: {len(dataset.all_examples)} examples")
    else:
        console.print("[red]✗ Specify --dataset-path or use --eval-source synthetic[/red]")
        sys.exit(1)

    console.print(f"  Split: {len(dataset.train)} train / {len(dataset.val)} val / {len(dataset.holdout)} holdout")

    # ── 3. Validate constraints on baseline ─────────────────────────────
    console.print(f"\n[bold]Validating baseline constraints[/bold]")
    validator = ConstraintValidator(config)
    baseline_constraints = validator.validate_all(skill["raw"], "skill")
    all_pass = True
    for c in baseline_constraints:
        icon = "✓" if c.passed else "✗"
        color = "green" if c.passed else "red"
        console.print(f"  [{color}]{icon} {c.constraint_name}[/{color}]: {c.message}")
        if not c.passed:
            all_pass = False

    if not all_pass:
        console.print("[yellow]⚠ Baseline skill has constraint violations — proceeding anyway[/yellow]")

    # ── 4. Set up optimizer ────────────────────────────────────────────

    console.print(f"\n[bold]Configuring optimizer[/bold]")
    console.print(f"  Inference: {inference_mode}, Evaluator: {evaluator}")
    console.print(f"  Optimizer model: {optimizer_model}")
    console.print(f"  Eval model: {eval_model}")
    console.print(f"  Engine: gepa.optimize")
    console.print(f"  Agent model: {agent_model or optimizer_model}")
    console.print(f"  Agent max iterations: {agent_max_iterations}")

    # ── 5. Run optimization ──────────────────────────────────────────
    console.print(f"\n[bold cyan]Running GEPA optimization ({iterations} iterations)...[/bold cyan]\n")

    from gepa import optimize

    adapter = EvolutionAdapter(config)

    gepa_trainset = dataset.to_gepa_datainst("train")
    gepa_valset = dataset.to_gepa_datainst("val")

    seed_candidate = {"artifact_body": skill["body"]}

    start_time = time.time()

    result = optimize(
        seed_candidate=seed_candidate,
        trainset=gepa_trainset,
        valset=gepa_valset,
        adapter=adapter,
        reflection_lm=optimizer_model,
        max_metric_calls=iterations,
        display_progress_bar=True,
    )

    if result.candidates:
        best_candidate = result.candidates[-1]
        if isinstance(best_candidate, dict):
            evolved_body = best_candidate.get("artifact_body", "") or skill["body"]
        else:
            evolved_body = skill["body"]
    else:
        evolved_body = skill["body"]

    trajectory_records = adapter.trajectories

    elapsed = time.time() - start_time
    console.print(f"\n  Optimization completed in {elapsed:.1f}s")

    # ── 6. Extract evolved skill text ───────────────────────────────────
    evolved_full = reassemble_skill(skill["frontmatter"], evolved_body)

    # ── 7. Validate evolved skill ───────────────────────────────────────
    console.print(f"\n[bold]Validating evolved skill[/bold]")
    evolved_constraints = validator.validate_all(evolved_full, "skill", baseline_text=skill["raw"])
    all_pass = True
    for c in evolved_constraints:
        icon = "✓" if c.passed else "✗"
        color = "green" if c.passed else "red"
        console.print(f"  [{color}]{icon} {c.constraint_name}[/{color}]: {c.message}")
        if not c.passed:
            all_pass = False

    if not all_pass:
        console.print("[red]✗ Evolved skill FAILED constraints — not deploying[/red]")
        # Still save for inspection
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path("output") / skill_name / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "evolved_FAILED.md").write_text(evolved_full)
        _save_output_artifacts(output_dir, skill_name, timestamp, iterations,
                               inference_mode, evaluator, agent_model, optimizer_model,
                               eval_model, dataset, skill, evolved_body, elapsed,
                               all_pass, 0.0, 0.0, 0.0,
                               eval_source, agent_max_iterations, trajectory_records,
                               console)
        return

    # ── 8. Evaluate on holdout set ──────────────────────────────────────
    console.print(f"\n[bold]Evaluating on holdout set ({len(dataset.holdout)} examples)[/bold]")

    holdout_insts = dataset.to_gepa_datainst("holdout")

    baseline_scores = []
    evolved_scores = []
    for data in holdout_insts:
        task_input = data["input"]

        baseline_result = adapter.agent.run(skill["body"], task_input, config)
        evolved_result = adapter.agent.run(evolved_body, task_input, config)

        if evaluator == "llm-judge" and adapter.judge:
            baseline_fitness = adapter.judge.score(
                task_input=task_input,
                expected_behavior=data["answer"],
                agent_output=baseline_result["output"],
                skill_text=skill["body"],
            )
            baseline_scores.append(baseline_fitness.composite)
            evolved_fitness = adapter.judge.score(
                task_input=task_input,
                expected_behavior=data["answer"],
                agent_output=evolved_result["output"],
                skill_text=evolved_body,
            )
            evolved_scores.append(evolved_fitness.composite)
        else:
            baseline_scores.append(
                _keyword_overlap(baseline_result["output"], data["answer"]))
            evolved_scores.append(
                _keyword_overlap(evolved_result["output"], data["answer"]))

    avg_baseline = sum(baseline_scores) / max(1, len(baseline_scores))
    avg_evolved = sum(evolved_scores) / max(1, len(evolved_scores))
    improvement = avg_evolved - avg_baseline

    # ── 9. Report results ───────────────────────────────────────────────
    table = Table(title="Evolution Results")
    table.add_column("Metric", style="bold")
    table.add_column("Baseline", justify="right")
    table.add_column("Evolved", justify="right")
    table.add_column("Change", justify="right")

    change_color = "green" if improvement > 0 else "red"
    table.add_row(
        "Holdout Score",
        f"{avg_baseline:.3f}",
        f"{avg_evolved:.3f}",
        f"[{change_color}]{improvement:+.3f}[/{change_color}]",
    )
    table.add_row(
        "Skill Size",
        f"{len(skill['body']):,} chars",
        f"{len(evolved_body):,} chars",
        f"{len(evolved_body) - len(skill['body']):+,} chars",
    )
    table.add_row("Time", "", f"{elapsed:.1f}s", "")
    table.add_row("Iterations", "", str(iterations), "")

    console.print()
    console.print(table)

    # ── 10. Save output ─────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("output") / skill_name / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    _save_output_artifacts(output_dir, skill_name, timestamp, iterations,
                           inference_mode, evaluator, agent_model, optimizer_model,
                           eval_model, dataset, skill, evolved_body, elapsed,
                           all_pass, avg_baseline, avg_evolved, improvement,
                           eval_source, agent_max_iterations, trajectory_records,
                           console)

    if improvement > 0:
        console.print(f"\n[bold green]✓ Evolution improved skill by {improvement:+.3f} ({improvement/max(0.001, avg_baseline)*100:+.1f}%)[/bold green]")
        console.print(f"  Review the diff: diff {output_dir}/baseline_skill.md {output_dir}/evolved_skill.md")
    else:
        console.print(f"\n[yellow]⚠ Evolution did not improve skill (change: {improvement:+.3f})[/yellow]")
        console.print("  Try: more iterations, better eval dataset, or different optimizer model")


@click.command()
@click.option("--skill", required=True, help="Name of the skill to evolve")
@click.option("--iterations", default=10, help="Number of GEPA iterations")
@click.option("--eval-source", default="synthetic", type=click.Choice(["synthetic", "golden", "sessiondb"]),
              help="Source for evaluation dataset")
@click.option("--dataset-path", default=None, help="Path to existing eval dataset (JSONL)")
@click.option("--optimizer-model", default="openai/gpt-4.1", help="Model for GEPA reflections")
@click.option("--eval-model", default="openai/gpt-4.1-mini", help="Model for evaluations")
@click.option("--hermes-repo", default=None, help="Path to hermes-agent repo")
@click.option("--run-tests", is_flag=True, help="Run full pytest suite as constraint gate")
@click.option("--dry-run", is_flag=True, help="Validate setup without running optimization")
@click.option("--inference", "inference_mode", default="single-turn",
              type=click.Choice(["single-turn", "hermes-agent", "edp-agent"]),
              help="How to execute the skill during evaluation")
@click.option("--evaluator", "evaluator", default="fast",
              type=click.Choice(["fast", "llm-judge"]),
              help="How to score agent outputs")
@click.option("--agent-model", default=None, help="Model for Hermes agent inference (defaults to --optimizer-model)")
@click.option("--agent-max-iterations", default=10, type=int,
              help="Max tool-calling rounds per agent run")
def main(skill, iterations, eval_source, dataset_path, optimizer_model, eval_model, hermes_repo, run_tests, dry_run, inference_mode, evaluator, agent_model, agent_max_iterations):
    """Evolve a Hermes Agent skill using DSPy + GEPA optimization."""
    evolve(
        skill_name=skill,
        iterations=iterations,
        eval_source=eval_source,
        dataset_path=dataset_path,
        optimizer_model=optimizer_model,
        eval_model=eval_model,
        hermes_repo=hermes_repo,
        run_tests=run_tests,
        dry_run=dry_run,
        inference_mode=inference_mode,
        evaluator=evaluator,
        agent_model=agent_model,
        agent_max_iterations=agent_max_iterations,
    )


if __name__ == "__main__":
    main()
    main()

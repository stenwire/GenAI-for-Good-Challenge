from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Municipal QA: answer questions from source files, with evidence.")


def _not_implemented(name: str) -> None:
    typer.echo(f"{name}: not implemented")
    raise typer.Exit(code=1)


@app.command("setup-data")
def setup_data(
    no_hash: Annotated[bool, typer.Option("--no-hash", help="Check sizes only.")] = False,
) -> None:
    """Extract SourceMaterials.zip and verify every source's size and SHA256."""
    import time

    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import DataSetupError, load_manifest
    from municipal_qa.data.manifest import setup_data as _setup

    cfg = load_config()
    t0 = time.perf_counter()
    try:
        _setup(cfg, verify_hashes=not no_hash)
    except DataSetupError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    n = len(load_manifest(cfg))
    checks = "size" if no_hash else "size and SHA256"
    typer.echo(
        f"verified {n} sources ({checks}) in {cfg.sources_dir} [{time.perf_counter() - t0:.1f}s]"
    )


@app.command()
def profile(
    source: Annotated[str | None, typer.Option(help="Profile one source ID only.")] = None,
) -> None:
    """Build or refresh the source profile cache."""
    _not_implemented("profile")


@app.command()
def index() -> None:
    """Build the retrieval index."""
    _not_implemented("index")


@app.command("check-model")
def check_model() -> None:
    """Verify Ollama, the configured model and its parameter count."""
    from municipal_qa.config import load_config
    from municipal_qa.llm.client import LLMClient, LLMError

    client = LLMClient(load_config())
    try:
        info = client.check_model()
    except LLMError as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(code=1) from e
    finally:
        client.close()
    typer.echo(f"model: {info['name']}")
    typer.echo(f"digest: {info['digest']}")
    typer.echo(f"parameters: {info['parameter_count']:,} (cap {info['max_params']:,}) OK")
    typer.echo(f"quantization: {info['quantization']}")


@app.command()
def run(
    split: Annotated[str, typer.Option(help="train, dev or test.")] = "dev",
    limit: Annotated[int | None, typer.Option(help="Answer only the first N questions.")] = None,
    ids: Annotated[str | None, typer.Option(help="Comma-separated question IDs.")] = None,
    resume: Annotated[str | None, typer.Option(help="Run ID to resume.")] = None,
) -> None:
    """Answer questions and write artifacts/runs/<run_id>/."""
    _not_implemented("run")


@app.command()
def evaluate(
    run_id: str,
    split: Annotated[
        str | None, typer.Option(help="Override the split recorded in run_manifest.json.")
    ] = None,
) -> None:
    """Score a dev run per domain and question type."""
    import json

    from municipal_qa.config import load_config
    from municipal_qa.evaluation.report import render
    from municipal_qa.evaluation.scorer import evaluate_run

    cfg = load_config()
    run_dir = cfg.artifacts_dir / "runs" / run_id
    if not (run_dir / "answers.jsonl").exists():
        typer.echo(f"no answers.jsonl in {run_dir}", err=True)
        raise typer.Exit(code=1)
    manifest = run_dir / "run_manifest.json"
    if split is None and manifest.exists():
        split = json.loads(manifest.read_text()).get("split")
    split = split or "dev"
    typer.echo(f"# Evaluation of {run_id} ({split})\n")
    typer.echo(render(evaluate_run(run_dir, split, cfg)))


@app.command()
def submit(run_id: str) -> None:
    """Write and validate artifacts/runs/<run_id>/Submission.md."""
    _not_implemented("submit")


@app.command()
def validate(path: Path) -> None:
    """Validate any submission file."""
    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions
    from municipal_qa.submission.validator import validate_submission

    cfg = load_config()
    test = load_questions(cfg, "test")
    warnings: list[str] = []
    problems = validate_submission(
        path,
        [q.id for q in test],
        {q.id: q.source_ids for q in test},
        set(load_manifest(cfg)),
        warnings,
    )
    if warnings:
        typer.echo(f"{len(warnings)} warning(s), e.g. {warnings[0]}")
    for p in problems:
        typer.echo(f"problem: {p}", err=True)
    if problems:
        typer.echo(f"INVALID: {len(problems)} problem(s) in {path}")
        raise typer.Exit(code=1)
    typer.echo(f"valid: {len(test)} Test IDs in order, {path.stat().st_size:,} bytes")


@app.command()
def show(run_id: str, question_id: str) -> None:
    """Pretty-print one AnswerRecord."""
    _not_implemented("show")


if __name__ == "__main__":
    app()

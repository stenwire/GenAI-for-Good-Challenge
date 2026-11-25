from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Municipal QA: answer questions from source files, with evidence.")


def _not_implemented(name: str) -> None:
    typer.echo(f"{name}: not implemented")
    raise typer.Exit(code=1)


@app.command("setup-data")
def setup_data() -> None:
    """Extract SourceMaterials.zip and verify every source's size and SHA256."""
    _not_implemented("setup-data")


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
    _not_implemented("check-model")


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
def evaluate(run_id: str) -> None:
    """Score a dev run per domain and question type."""
    _not_implemented("evaluate")


@app.command()
def submit(run_id: str) -> None:
    """Write and validate artifacts/runs/<run_id>/Submission.md."""
    _not_implemented("submit")


@app.command()
def validate(path: Path) -> None:
    """Validate any submission file."""
    _not_implemented("validate")


@app.command()
def show(run_id: str, question_id: str) -> None:
    """Pretty-print one AnswerRecord."""
    _not_implemented("show")


if __name__ == "__main__":
    app()

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
    workers: Annotated[int, typer.Option(help="Parallel worker processes.")] = 4,
    force: Annotated[bool, typer.Option("--force", help="Rebuild cached profiles.")] = False,
) -> None:
    """Build or refresh the source profile cache."""
    import time

    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.profiling.profile import ProfileStore, profile_summary

    cfg = load_config()
    manifest = load_manifest(cfg)
    if source:
        if source not in manifest:
            typer.echo(f"unknown source {source}", err=True)
            raise typer.Exit(code=1)
        manifest = {source: manifest[source]}
    store = ProfileStore(cfg, manifest)
    t0 = time.perf_counter()
    if force:
        for sid in manifest:
            store.path(sid).unlink(missing_ok=True)
    store.build_all(workers=workers)
    info = profile_summary(store)
    kinds = ", ".join(f"{k} {n}" for k, n in info["kinds"].items())
    typer.echo(
        f"profiled {len(manifest)} sources in {time.perf_counter() - t0:.1f}s -> "
        f"{store.cache_dir}\nkinds: {kinds}\nfallback (binary with warning): "
        f"{len(info['fallbacks'])}"
    )
    for sid, name, why in info["fallbacks"]:
        typer.echo(f"  {sid} {name}: {why}")


@app.command()
def index(
    source: Annotated[str | None, typer.Option(help="Index one source ID only.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Re-embed cached sources.")] = False,
) -> None:
    """Build the retrieval index: passages, column passages and encoder vectors."""
    import time

    import municipal_qa.readers  # noqa: F401
    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.llm.client import LLMClient, LLMError
    from municipal_qa.profiling.profile import ProfileStore
    from municipal_qa.retrieval.index import Retriever

    cfg = load_config()
    manifest = load_manifest(cfg)
    if source:
        if source not in manifest:
            typer.echo(f"unknown source {source}", err=True)
            raise typer.Exit(code=1)
        manifest = {source: manifest[source]}
    client = LLMClient(cfg)
    try:
        info = client.check_embedder()
    except LLMError as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(code=1) from e
    retriever = Retriever(cfg, ProfileStore(cfg, manifest), client)
    if force:
        for sid in manifest:
            retriever._path(sid).unlink(missing_ok=True)
    t0 = time.perf_counter()
    meta = retriever.build(list(manifest))
    typer.echo(
        f"indexed {meta['sources']} sources, {meta['passages']:,} passages in "
        f"{time.perf_counter() - t0:.1f}s -> {retriever.dir}\nencoder: {info['name']} "
        f"({info['architecture']}, {info['parameter_count']:,} parameters, {info['capabilities']})"
    )


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
    import datetime as dt
    import time

    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions
    from municipal_qa.llm.client import LLMClient, LLMError
    from municipal_qa.pipeline import run as runner
    from municipal_qa.pipeline.answer import Deps, answer_question

    cfg = load_config()
    run_id = resume or runner.new_run_id()
    run_dir = cfg.artifacts_dir / "runs" / run_id
    if resume and not run_dir.exists():
        typer.echo(f"no run {resume} to resume", err=True)
        raise typer.Exit(code=1)
    if not resume and run_dir.exists():
        typer.echo(f"run {run_id} already exists; pass --resume {run_id} to continue it", err=True)
        raise typer.Exit(code=1)
    questions = runner.select(load_questions(cfg, split), ids, limit)
    client = LLMClient(cfg)
    try:
        model = client.check_model()
    except LLMError as e:
        typer.echo(f"FAILED model check: {e}", err=True)
        raise typer.Exit(code=1) from e
    deps = Deps(cfg, load_manifest(cfg), client, cache_dir=cfg.artifacts_dir / "passages")
    run_dir.mkdir(parents=True, exist_ok=bool(resume))
    start = dt.datetime.now().isoformat(timespec="seconds")
    runner.write_manifest(run_dir, cfg, split, model, {"started": start, "status": "running"})
    t0 = time.perf_counter()

    def progress(i: int, n: int, rec) -> None:
        if i == n or i % 25 == 0:
            typer.echo(f"  {i}/{n} answered ({time.perf_counter() - t0:.0f}s)")

    n = runner.run_questions(questions, lambda q: answer_question(q, deps), run_dir, progress)
    rows, max_calls, max_tokens = runner.write_budget_csv(run_dir)
    runner.write_manifest(
        run_dir,
        cfg,
        split,
        model,
        {
            "finished": dt.datetime.now().isoformat(timespec="seconds"),
            "status": "complete",
            "answered_this_session": n,
            "answers": rows,
            "max_calls": max_calls,
            "max_generated_tokens": max_tokens,
        },
    )
    typer.echo(
        f"run {run_id}: {rows} answers, max calls {max_calls}, "
        f"max generated tokens {max_tokens} -> {run_dir}"
    )


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
    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions
    from municipal_qa.pipeline.run import load_run_records
    from municipal_qa.submission.validator import validate_submission
    from municipal_qa.submission.writer import write_submission

    cfg = load_config()
    run_dir = cfg.artifacts_dir / "runs" / run_id
    test = load_questions(cfg, "test")
    ids = [q.id for q in test]
    records = load_run_records(run_dir)
    missing = [i for i in ids if i not in records]
    if missing:
        typer.echo(
            f"run {run_id} lacks {len(missing)} Test answers (e.g. {missing[:2]}); "
            "finish it with: mqa run --split test --resume " + run_id,
            err=True,
        )
        raise typer.Exit(code=1)
    out = write_submission({i: records[i] for i in ids}, ids, run_dir / "Submission.md")
    warnings: list[str] = []
    problems = validate_submission(
        out, ids, {q.id: q.source_ids for q in test}, set(load_manifest(cfg)), warnings
    )
    for p in problems:
        typer.echo(f"problem: {p}", err=True)
    if problems:
        raise typer.Exit(code=1)
    typer.echo(f"wrote {out} ({out.stat().st_size:,} bytes), valid, {len(warnings)} warning(s)")


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
    from municipal_qa.config import load_config
    from municipal_qa.pipeline.run import load_run_records

    records = load_run_records(load_config().artifacts_dir / "runs" / run_id)
    if question_id not in records:
        typer.echo(f"{question_id} not in run {run_id}", err=True)
        raise typer.Exit(code=1)
    rec = records[question_id]
    typer.echo(f"# {question_id}\n\n{rec.answer}\n\n### Evidence")
    for line in rec.evidence:
        typer.echo(f"- {line}")
    typer.echo(
        f"\ncalls {rec.total_calls}, generated tokens {rec.total_generated_tokens}, "
        f"fallback {rec.fallback_used}, error {rec.error}"
    )


if __name__ == "__main__":
    app()

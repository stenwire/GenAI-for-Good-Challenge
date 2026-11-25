# Milestones

Status values: `not started`, `in progress`, `awaiting verify`, `complete`.

**A milestone reaches `complete` only after `/verify` has run for it and
`docs/VERIFICATION.md`'s header names that milestone with zero blockers.**
Re-read that header immediately before writing `complete`.

Normative spec: `dev/TECHNICAL_PLAN.md` (the plan). Challenge rules and
organiser clarifications: `docs/CHALLENGE_BRIEF.md` (the brief) and the two
official PDFs in `docs/`. Where the plan and the rules disagree, the rules win
and a Decisions row in `docs/TODO.md` records it.

Requirement references use the plan's own build-card IDs (`M1`..`M8`, `T1`..`T4`,
`S1`..`S5`) and section numbers (`§6.9`). Card owners in the plan are ignored:
one developer builds everything (D-001).

Hard external dates (from the brief, not movable):

- **30 Nov 2026**: last day to email external dataset proposals.
- **13 Dec 2026 23:59 EST**: Zindi closes. Final two submissions must be
  selected before this.
- Up to 1 Zindi submission per day, 20 in total. Uploading is done by the user,
  never by `/implement`.

| Milestone | Name | Plan cards | Target | Depends on | Status |
|---|---|---|---|---|---|
| M0 | Foundation | M1, M2, M6, M7, S1, S2 | 27 Nov 2026 | - | complete |
| M1 | Baseline Submission | M3, T1, T4, S5 (basic), T3 (≥ 60) | 30 Nov 2026 | M0 | in progress |
| M2 | Tools and Planner | M4, M5, T2, T3 (≥ 90), S3, S4 | 5 Dec 2026 | M1 | not started |
| M3 | Feature Freeze | S5 (complete), M8 (draft) | 9 Dec 2026 | M2 | not started |
| M4 | Final Package | M8 | 12 Dec 2026 | M3 | not started |

Target dates replace the plan §12 dates, which had passed with nothing built
(D-005).

Branch per milestone, cut from `main`: `m0-foundation`,
`m1-baseline-submission`, `m2-tools-and-planner`, `m3-feature-freeze`,
`m4-final-package`.

All proof commands run from the repo root with an explicit
`cd municipal-qa && ...` (D-003).

---

## M0 - Foundation

Delivers the `municipal-qa/` package, toolchain, shared schema, data access,
submission writer and the model client with its budget guard. It comes first
because **no proof command runs today**: there is no `pyproject.toml`, so
`pytest`, `ruff` and `mqa` all fail.

**Plan cards:** M1, M2, M6, M7, S1, S2

### Definition of Done

| # | Criterion | State |
|---|---|---|
| 1 | Root `LICENSE` is Apache License 2.0 (D-002); `head -3 LICENSE` shows it | [x] |
| 2 | `cd municipal-qa && uv run python -c "import sys; print(sys.prefix)"` prints a path inside `municipal-qa/.venv` | [x] |
| 3 | `cd municipal-qa && uv run pytest -q` collects a non-zero number of tests and passes | [x] |
| 4 | `cd municipal-qa && uv run ruff check .` passes with no `No Python files found` warning; `uv run ruff format --check .` exits 0 | [x] |
| 5 | `cd municipal-qa && uv run mqa --help` lists every command in plan §8 | [x] |
| 6 | `uv run mqa setup-data` verifies all 188 sources by size and SHA256 against `SourceManifest.csv` | [x] |
| 7 | `load_questions` returns 600 Test and 9 Train questions; Train and Test source sets are disjoint | [x] |
| 8 | A placeholder `Submission.md` for all 600 Test IDs passes `uv run mqa validate` | [x] |
| 9 | `uv run mqa check-model` reports the configured model with a total parameter count ≤ 3,000,000,000 and its digest | [x] |
| 10 | `QuestionBudget` boundary tests pass: 15th call allowed, 16th refused; 12,000 tokens allowed, 12,001 refused | [x] |
| 11 | `.gitignore` covers `municipal-qa/.venv/`, `municipal-qa/artifacts/`, `municipal-qa/.env`, `Data/sources/` | [x] |
| 12 | No dependency in `uv.lock` is under a strong or weak copyleft licence except where a Decisions row allows it | [x] |

### Verification

- Verified: yes, 2026-11-25 (`docs/VERIFICATION.md`: M0 - Foundation, 0 blockers, PASS)

---

## M1 - Baseline Submission

Delivers the first valid, scored submission: cell-level spreadsheet, delimited,
text and structured readers, a minimal passage retriever, a one-call composer
with deterministic evidence and fallbacks, the dev scorer, and a dev set of at
least 60 questions. Weak on numbers by design (plan §12 baseline definition),
but every one of the 600 answers is non-blank and the file is valid.

**Plan cards:** M3, T1, T4, S5 (basic), T3 (≥ 60 rows)

### Definition of Done

| # | Criterion | State |
|---|---|---|
| 1 | Train `bus01.ods` cells `BUS01a` H23, T23, H29, T29 read with both stored and displayed values (card M3) | [x] |
| 2 | HSY `.xlsx` `Tulokset` AA9 returns full precision for `stored` and the formatted string for `displayed` (plan §7.2) | [x] |
| 3 | Special markers (`[z]`, `[x]`, `[c]`, `..`, `-`) stay strings in every reader, proved by a fixture test | [ ] |
| 4 | Every `.pdf`, `.md`, `.txt`, `.html`, `.docx`, `.json`, `.xml`, `.xsd` source yields passages with valid locators (card T1) | [ ] |
| 5 | Scorer returns 1.0 on the Train reference answers and 0.0 on empty answers (card T4) | [ ] |
| 6 | `municipal-qa/dev/dev_questions.csv` has ≥ 60 rows and `devset_check.py` passes (no Test sources) | [ ] |
| 7 | `uv run mqa run --split test` completes for all 600 questions with zero blank answers | [ ] |
| 8 | `uv run mqa validate` on that run's `Submission.md` reports no problems | [ ] |
| 9 | That run's `budget.csv` shows max calls ≤ 15 and max generated tokens ≤ 12,000 | [ ] |
| 10 | `SUBMISSIONS.md` has a row for the run (date, tag, run ID, dev score; leaderboard score filled in when the user reports it) | [ ] |

### Verification

- Verified: no

---

## M2 - Tools and Planner

Delivers source profiling, the operation DSL with table, calc, spatial, raster
and point-cloud ops, hybrid retrieval with reranking, and the router/planner
with JSON-schema-constrained output and repair.

**Plan cards:** M4, M5, T2, T3 (≥ 90 rows), S3, S4

### Definition of Done

| # | Criterion | State |
|---|---|---|
| 1 | All 188 profiles build with no unhandled exception in under 15 minutes; `render_for_prompt` < 6,000 chars for every Test question (card M4) | [ ] |
| 2 | Every op in plan §6.5 is registered, has a JSON args schema and a fixture test, and returns `Fact`s with locators (card M5) | [ ] |
| 3 | Train `1bbb3964` reproduced by `set_compare`; Train `1d78cef6` reproduced by `lookup_row` + `date_diff` (35 days) | [ ] |
| 4 | Train `06ffb5b9` yields 3.48 ha; 100 m square → 1.00000 ha; half-up vs half-even on 2.345; inclusive radius; DSM−DTM with nodata (card S4) | [ ] |
| 5 | Retrieval recall@8 > 0.8 on dev semantics questions; search < 300 ms per query on CPU (card T2) | [ ] |
| 6 | Dev set ≥ 90 rows; `devset_check.py` passes (card T3) | [ ] |
| 7 | ≥ 80% of dev plans execute without error after at most 2 repairs (card S3) | [ ] |
| 8 | `uv run mqa evaluate` on a full dev run, score table pasted, per domain and per qtype | [ ] |

### Verification

- Verified: no

---

## M3 - Feature Freeze

Completes the answer loop (composer rules, deterministic evidence, verifier with
one rewrite, fallbacks), per-question budget logs, run manifests, and a clean,
offline reproduction that mirrors the organisers' rerun environment.

**Plan cards:** S5 (complete), M8 (draft)

### Definition of Done

| # | Criterion | State |
|---|---|---|
| 1 | `answer_question` never raises and never returns a blank answer, proved by a test that injects reader, tool, LLM and budget failures | [ ] |
| 2 | Evidence lines are built only in `pipeline/evidence.py`, match `^- SRC_[0-9a-f]{12} \| .+ \| .+$`, max 8 per answer (plan §6.9) | [ ] |
| 3 | Verifier rejects an answer containing a number absent from the facts, proved by a test | [ ] |
| 4 | Every run writes `run_manifest.json` (git hash, config, model name and digest, package versions, seed) and `budget.csv` | [ ] |
| 5 | LLM preprocessing reused across questions is charged to every question that uses it (organiser thread 35998), proved by a test | [ ] |
| 6 | Import-scan test: nothing under `pipeline/`, `tools/`, `llm/` or `cli.py` imports `evaluation.judge` | [ ] |
| 7 | Dev score beats the M1 baseline in every domain, both tables pasted | [ ] |
| 8 | Clean reproduction from a fresh clone, with network used only for package install, runs `setup-data → profile → index → run --split test --limit 20 → submit → validate` | [ ] |

### Verification

- Verified: no

---

## M4 - Final Package

Delivers the final full run, the two selected submissions, and a code package
ready for the 48-hour code request.

**Plan cards:** M8

### Definition of Done

| # | Criterion | State |
|---|---|---|
| 1 | Final full Test run validated by `mqa validate`; file size reported | [ ] |
| 2 | Both final submissions recorded in `SUBMISSIONS.md` with git tags `sub-NN-YYYY-MM-DD` that exist (`git tag -l 'sub-*'`) | [ ] |
| 3 | README documents hardware, runtime, model checkpoint and digest, total parameter count, every dependency and its licence, every data source and its licence (from `SourceManifest.csv`), and the exact command sequence | [ ] |
| 4 | `LICENSE` is Apache 2.0 and no copyleft dependency remains in `uv.lock` | [ ] |
| 5 | Code fits the organisers' template repository structure and entry point once it is published (blocker B-003 until then) | [ ] |

### Verification

- Verified: no

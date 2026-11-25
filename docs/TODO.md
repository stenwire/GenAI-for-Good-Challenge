# TODO

Source of truth for task state, alongside `docs/MILESTONES.md`. Both are read in
full at the start of every `/implement` run.

State: `[ ]` not started, `[~]` in progress or blocked, `[x]` done with proof.

**A task is never ticked on inspection.** `[x]` requires pasted output from a
real command in the run that set it.

Task IDs are banded by milestone: T-0xx = M0, T-1xx = M1, T-2xx = M2,
T-3xx = M3, T-4xx = M4. Never reuse a retired ID. The `Card` column names the
build card in `dev/TECHNICAL_PLAN.md` §9 that specifies the task.

## Queue

| ID | Milestone | Card | Task | Dep | State |
|---|---|---|---|---|---|
| T-001 | M0 | - | Replace root `LICENSE` (MIT) with the Apache License 2.0 text, copyright holder unchanged (D-002) | - | [x] |
| T-002 | M0 | - | Extend `.gitignore`: `municipal-qa/.venv/`, `municipal-qa/artifacts/`, `municipal-qa/.env`, `Data/sources/`; prove with `git check-ignore -v` on each path | - | [ ] |
| T-003 | M0 | M1 | `uv init --package --name municipal-qa --python 3.12 municipal-qa`; add plan §3.2 dependencies **except `ifcopenshell`** (D-006); ruff and pytest config from §3.2; `.python-version`; prove the env resolves inside `municipal-qa/.venv`. Resolves B-001. | T-002 | [ ] |
| T-004 | M0 | M1 | `configs/default.yaml` (plan §6.6), `config.py` with `load_config` expanding `${MQA_DATA_DIR}` from env or `municipal-qa/.env`, `.env.example`, `tests/test_config.py` | T-003 | [ ] |
| T-005 | M0 | M1 | Typer `cli.py` with every plan §8 command as a stub; `.vscode/settings.json` and `extensions.json` (plan §3.4); `uv run mqa --help` lists all commands | T-004 | [ ] |
| T-006 | M0 | S1 | `schema.py` exactly as plan §5 | T-003 | [ ] |
| T-007 | M0 | S1 | `pipeline/context.py`: Context → `Conventions` per plan §6.3, CRS alias table, question-text parsing; one test per rule-table row, the 3 Train contexts, and all 58 distinct Test contexts parse without exception | T-006 | [ ] |
| T-008 | M0 | M2 | `data/manifest.py` and `data/questions.py`: `load_manifest`, `setup_data` (zip-slip guard, size and SHA256 streaming check), `load_questions`; `uv run mqa setup-data` verifies 188 sources | T-005, T-006 | [ ] |
| T-009 | M0 | M7 | `submission/writer.py` and `validator.py` per card M7; round-trip test and 8 broken-file tests; placeholder submission for 600 IDs validates | T-008 | [ ] |
| T-010 | M0 | M6 | `llm/budget.py` `QuestionBudget` with worst-case `can_call`; exact boundary tests (D-007) | T-006 | [ ] |
| T-011 | M0 | S2 | `llm/client.py` `LLMClient` (`check_model`, `chat`) per card S2, tests with mocked HTTP; `uv run mqa check-model` against a live Ollama | T-010 | [ ] |
| T-012 | M0 | - | License audit of `uv.lock`: list every package's licence; no copyleft without a Decisions row | T-003 | [ ] |
| T-013 | M0 | - | User decision on B-004: whether to untrack the repo-root `Data/` challenge files (and whether to purge them from history); then add `Data/` to `.gitignore` | - | [ ] |
| T-101 | M1 | M3 | `readers/base.py` protocol and registry; `readers/spreadsheet.py` cell-level `.xlsx` and `.ods` with stored and displayed values, merged cells, capped repeats, notes sheets as passages, markers kept as strings; synthetic fixtures in `tests/conftest.py` | T-011 | [ ] |
| T-102 | M1 | M3 | `readers/delimited.py` (encoding and delimiter sniffing, DuckDB above 50 MB, 1-based data-row locators, WKT detection), `readers/sqlite.py` (read-only URI), `readers/archive.py` (zip members, jpg metadata) | T-101 | [ ] |
| T-103 | M1 | T1 | `readers/text.py`: pdf (pdfplumber, page numbers, tables), html, docx, md/txt split by headings with line ranges | T-101 | [ ] |
| T-104 | M1 | T1 | `readers/structured.py`: json (GeoJSON handoff, record flattening, JSON-pointer passages), xml and xsd passages | T-101 | [ ] |
| T-105 | M1 | T4 | `evaluation/scorer.py`, `evaluation/report.py`, `mqa evaluate`; aggregation element → question → case → domain → equal-weight mean; 1.0 on Train references, 0.0 on empty; filename → source-ID mapping for Train evidence | T-009 | [ ] |
| T-106 | M1 | T3 | `municipal-qa/dev/dev_questions.csv` ≥ 60 rows from Train sources only, with `elements` JSON; `dev/README.md`; `evaluation/devset_check.py` refusing Test sources | T-105 | [ ] |
| T-107 | M1 | S5 | Baseline `answer_question`: BM25 over reader passages restricted to `Source_IDs` (docs first, k=8), one compose call, evidence from top passages built in code, fallbacks per plan §6.10; never raises | T-102, T-103, T-104 | [ ] |
| T-108 | M1 | S5 | `mqa run` (resume, `answers.jsonl`, `budget.csv`, `errors.jsonl`, `run_manifest.json`) and `mqa submit`; full Test run; validated `Submission.md`; `SUBMISSIONS.md` row; hand the file to the user for upload | T-107 | [ ] |
| T-109 | M1 | - | Ask the user whether any external dataset proposal is needed before the 30 Nov 2026 deadline; record the answer as a Decisions row | - | [ ] |
| T-201 | M2 | M4 | `profiling/profile.py` `ProfileStore` with JSON cache, `build_all`, `render_for_prompt` (docs first, < 6,000 chars); `mqa profile` | T-108 | [ ] |
| T-202 | M2 | S4 | `readers/vector.py` (all layers, CRS from file, never repair, CityGML via lxml), `readers/raster.py`, `readers/pointcloud.py` (chunked), `readers/ifc.py` as a STEP-text parser without ifcopenshell (D-006) | T-201 | [ ] |
| T-203 | M2 | M5 | `tools/registry.py` (`OpSpec`, `register`, `ops_menu`, `run_step` with `$name` references and profile validation) and `tools/table_ops.py` with every table op; `sql` accepts a single SELECT only | T-201 | [ ] |
| T-204 | M2 | S4 | `tools/calc.py`: `Decimal` arithmetic, rounding modes, round at end, unit conversion, `date_diff` with inclusive flags, `check_criteria` | T-203 | [ ] |
| T-205 | M2 | S4 | `tools/spatial_ops.py`: reproject to Conventions CRS before measuring, CRS stated in the claim, inclusive radius, validity reported never repaired unless allowed | T-202, T-204 | [ ] |
| T-206 | M2 | S4 | `tools/raster_ops.py`: raster stats with nodata, aligned pair diff over valid pairs only, point-cloud class counts and z stats | T-202, T-204 | [ ] |
| T-207 | M2 | T2 | `retrieval/` hybrid BM25 + `multilingual-e5-small` with RRF, cross-encoder rerank, synthetic column passages, `tools/text_ops.py`; `mqa index` | T-201 | [ ] |
| T-208 | M2 | T3 | Extend dev set to ≥ 90 rows; `devset_check.py` passes | T-106 | [ ] |
| T-209 | M2 | S3 | `pipeline/router.py`, `pipeline/planner.py`, `executor.py`, prompts `route.md`, `plan.md`, `repair.md`; JSON-schema output with op and source-ID enums; ≥ 80% of dev plans execute | T-203, T-204, T-205, T-206, T-207 | [ ] |
| T-210 | M2 | S3 | Full dev run with the planner wired in; `mqa evaluate` table pasted per domain and qtype | T-208, T-209 | [ ] |
| T-301 | M3 | S5 | `pipeline/composer.py` (plan §6.8 rules), `pipeline/evidence.py` (§6.9), `pipeline/verifier.py` with one rewrite call; failure-injection tests | T-210 | [ ] |
| T-302 | M3 | S5 | Per-question call log including charged shared preprocessing (thread 35998); test that a shared call is charged to each question using it | T-301 | [ ] |
| T-303 | M3 | S5 | Import-scan test: `evaluation.judge` not imported by `pipeline/`, `tools/`, `llm/` or `cli.py` | T-301 | [ ] |
| T-304 | M3 | S5 | Dev score vs M1 baseline in every domain; both tables pasted | T-302 | [ ] |
| T-305 | M3 | M8 | README draft and a clean offline reproduction from a fresh clone (network only for `uv sync` and the model pull, see checklist Gaps) | T-304 | [ ] |
| T-401 | M4 | M8 | Final full Test run; `mqa validate`; report file size | T-305 | [ ] |
| T-402 | M4 | M8 | Record the user's two final submission choices in `SUBMISSIONS.md`; create `sub-NN-YYYY-MM-DD` tags for them | T-401 | [ ] |
| T-403 | M4 | M8 | Final README: hardware, runtime, model and digest, parameter count, dependency licences, source licences, exact commands | T-401 | [ ] |
| T-404 | M4 | M8 | Restructure into the organisers' template repository and entry point | T-403 | [ ] |

## Change log

Append only, newest at the bottom. One row per `/implement` run.

| Date | Task | Change | Proof |
|---|---|---|---|
| 2026-11-25 | scaffold | Generated `/implement` and `/verify` skills, `references/checklist.md`, `docs/MILESTONES.md` and `docs/TODO.md` from `dev/TECHNICAL_PLAN.md` and `docs/CHALLENGE_BRIEF.md` | No code exists yet; see B-001. `SourceMaterials.zip` and `sources/` confirmed present in `GenAI-for-Good-Challenge/Data` (outside the repo). |
| 2026-11-25 | T-001 | Root `LICENSE` replaced with the Apache License 2.0 text (byte-identical to the canonical copy except the appendix line, filled as `Copyright 2026 Stephen Nwankwo`). Branch `m0-foundation` cut from `main`. Found tracked challenge data under repo-root `Data/` in a public repo: B-004, T-013. | `head -3 LICENSE` → `Apache License` / `Version 2.0, January 2004` / `http://www.apache.org/licenses/`; `diff` vs canonical shows only line 179; `grep -c "MIT License" LICENSE` → 0 |

## Decisions

Anything chosen that the spec did not dictate. Append only; supersede by adding
a new row that names the old one, never by editing or deleting.

| ID | Date | Decision | Rationale |
|---|---|---|---|
| D-001 | 2026-11-25 | One developer builds every card. Owner names and the "agree with all three" rules in the plan (§0, §4, §11) are ignored; a change to `schema.py`, `configs/default.yaml` or the submission format instead needs a Decisions row. | User instruction: task sharing is handled by one person. |
| D-002 | 2026-11-25 | Code licence is **Apache License 2.0**, superseding plan §2's MIT. | User choice. IEEE Official Rules §9a: all submission code "will be publicly released and licensed under the Apache 2.0 license". |
| D-003 | 2026-11-25 | The package lives in `municipal-qa/` under the repo root, as plan §3.2's `uv init` creates it. Every proof command runs `cd municipal-qa && ...`. `MQA_DATA_DIR` is the absolute path to `GenAI-for-Good-Challenge/Data` (outside the repo), set in `municipal-qa/.env`. | User choice of layout; the challenge files and extracted `sources/` already live in that outer folder. |
| D-004 | 2026-11-25 | One branch per milestone cut from `main`, one commit per `/implement` run, PR opened when the milestone's last task is proved, never merged by Claude. Supersedes plan §11's per-card branches. | User choice. |
| D-005 | 2026-11-25 | Milestones re-dated to 27 Nov, 30 Nov, 5 Dec, 9 Dec, 12 Dec 2026. | Plan §12 dates (15 Nov onward) had passed with no code built. Hard dates (30 Nov proposals, 13 Dec close) unchanged. |
| D-006 | 2026-11-25 | `ifcopenshell` is not a dependency. `readers/ifc.py` parses IFC STEP text directly; BIM ops use that parser. | `ifcopenshell` is LGPL-3.0-or-later (weak copyleft). IEEE Official Rules §9b allow only permissive, non-copyleft, OSI-approved licences. |
| D-007 | 2026-11-25 | Card M6 (budget guard) moved from M1 to M0. | Card S2 (`LLMClient.chat`) takes a `QuestionBudget`, so S2 cannot be proved without it. |
| D-008 | 2026-11-25 | Test questions and sources may be inspected to decide which generic readers, tools and prompts to build. Prompts and thresholds are still tuned only against dev-set scores, never by reading Test outputs. Resolves the open question in plan §2. | Organiser reply in Zindi thread 35998 (13 Nov 2026) allows building tools from Test inspection; hard-coding and training on Test remain prohibited. |
| D-009 | 2026-11-25 | The dev set lives at `municipal-qa/dev/`. The repo-root `dev/` keeps the planning documents. | Plan §4 places `dev/` inside the package; the repo root `dev/` already holds `TECHNICAL_PLAN.md`. |
| D-010 | 2026-11-25 | The copyright holder is kept by filling the Apache 2.0 appendix boilerplate line (`Copyright 2026 Stephen Nwankwo`); the licence body is unmodified canonical text. | T-001 says "copyright holder unchanged"; the appendix line is the standard place for it and leaves the licence text verbatim. |

## Blockers

| ID | Date | Blocks | Blocker | State |
|---|---|---|---|---|
| B-001 | 2026-11-25 | T-003..T-012 and every proof command | No `municipal-qa/pyproject.toml` exists, so `uv run pytest`, `uv run ruff check .` and `uv run mqa` cannot run. Resolved by T-003. | open |
| B-002 | 2026-11-25 | T-011 | `ollama list` → `Error: could not connect to ollama server, run 'ollama serve' to start it` (client 0.40.4). The user must start Ollama and run `ollama pull qwen3:1.7b` before `mqa check-model` can be proved. | open |
| B-003 | 2026-11-25 | T-404 | The organisers' template repository for code review (thread 35998) has not been published. | open |
| B-004 | 2026-11-25 | T-013 | `Data/AllowedDatasets.csv`, `SampleSubmission.md`, `SourceManifest.csv`, `StarterNotebook.ipynb`, `Test.csv`, `Train.csv` are tracked on `main` (commit `2859d92`) and `origin` is a PUBLIC GitHub repo. Skill rule: never commit challenge data; D-003 places data outside the repo. Untracking or rewriting history needs the user's decision. | open |

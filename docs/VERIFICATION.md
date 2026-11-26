# Verification Report

**Milestone:** M1 - Baseline Submission
**Scope:** M1 tasks T-101..T-109 and all 10 Definition-of-Done rows; every checklist item touched by M1 code (`readers/` all modules, `retrieval/bm25.py`, `pipeline/answer.py`, `pipeline/evidence.py`, `pipeline/run.py`, `llm/prompts/`, `evaluation/` scorer, report and devset check, `cli.py` run/submit/show/evaluate, `dev/` dev set and builder, `SUBMISSIONS.md`); Dimension 1 (RUL-01..RUL-19) in full. Branch `m1-baseline-submission` at `adbb92c`.
**Run audited:** `20261125-2323-f27f59e` (Test, 600 questions); dev score taken from `20261125-2322-f27f59e`
**Date:** 2026-11-26
**Blockers:** 0
**Major:** 1
**Minor:** 4
**Verdict:** PASS
**Dimensions not verified:** RUL-04 (no embedder or reranker yet), RUL-08 (no shared generative preprocessing exists), RUL-05's import-scan test (`evaluation/judge.py` not built; test is T-303); COR-04/05/09/10/11 and LAY-03 (calc, spatial, composer, verifier, op Facts not built in M1); REP-05..07 (no local encoders yet; README is M3/M4). Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, Zindi file-size limit (the audited file is 795,356 bytes, above the unofficial 256 KiB guidance; tracked as T-306), challenge data remaining in public git history.

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|
| 1 | major | 4. Reproducibility | `municipal-qa/src/municipal_qa/retrieval/bm25.py:15`, `:41-48` | The passage cache key is `<source_id>.v1.json` only. A change to any reader leaves old cached passages in use until someone bumps `CACHE_VERSION` by hand, so a local run can silently differ from what a fresh clone (the organisers' rerun) produces. Not triggered in the audited run: 0 of 183 cache files are older than the newest reader or retrieval source. It will be triggered in M2, when `.geojson` moves to the vector reader (T-202). | Key the cache on the source SHA256 plus a hash of the reader package's code (or the reader name and a reader version constant bumped by tests), and record the cache key in `run_manifest.json`. Test that a reader change invalidates the cache. |
| 2 | minor | 4. Reproducibility | `municipal-qa/src/municipal_qa/pipeline/run.py:53-55`; `municipal-qa/src/municipal_qa/cli.py:94-107` | `new_run_id` has minute resolution and `mqa run` creates the run directory with `exist_ok=True`. Two non-resume runs started in the same minute on the same commit (for example dev then test) share one directory: answers mix and the manifest's `split` is overwritten. | Add seconds (or a counter) to the run ID and refuse an existing directory unless `--resume` is given. |
| 3 | minor | 2. Submission | `municipal-qa/src/municipal_qa/pipeline/answer.py` `clean_answer`; `municipal-qa/src/municipal_qa/llm/prompts/compose_baseline.md:12` | The prompt says "no lists", but 30 of 600 answers contain bullet lines (132 lines starting `- `). They sit in the answer section, none look like evidence, and the file validates; the checklist's SUB-04 count (`^- ` lines vs evidence lines, 2,337 vs 2,205) therefore over-reports. | Fold bullet lines into sentences in `clean_answer`, or narrow SUB-04's command to lines after `### Evidence`. |
| 4 | minor | 6. Layering (LAY-04) | `municipal-qa/src/municipal_qa/evaluation/scorer.py:149`; `municipal-qa/dev/build_devset.py` (`add`) | Reference evidence lines are formatted inline instead of through `pipeline/evidence.line`. Dev and evaluation code only, not submissions. | Use `evidence.line` so the format lives in one place. |
| 5 | minor | 6. Layering (LAY-05) | `municipal-qa/src/municipal_qa/pipeline/answer.py` `EVIDENCE_FROM_TOP = 4`; `municipal-qa/src/municipal_qa/retrieval/bm25.py` `search(docs_first=3)` | Baseline tuning values are code literals, not `configs/default.yaml` settings. | Move them under `retrieval:` / `pipeline:` in the config. |

## Commands run

```
$ git status --porcelain            # at start of run
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
adbb92c T-108 (S5): record baseline Test run, submission and dev score
$ ls municipal-qa/artifacts/runs/
20261125-2322-f27f59e
20261125-2323-f27f59e
```

RUL-01
```
model: qwen3:1.7b
digest: 8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7
parameters: 2,031,739,904 (cap 3,000,000,000) OK
quantization: Q4_K_M
exit 0
```

RUL-03
```
src/municipal_qa/llm/client.py:116:            data = self._post("/api/chat", payload)
src/municipal_qa/llm/client.py:128:            raise LLMError(f"{purpose}: malformed /api/chat reply: {e}") from e
```

RUL-05: `rg -n "judge" pipeline llm retrieval cli.py` → exit 1; `evaluation/` holds `devset_check.py`, `report.py`, `scorer.py` only.

RUL-06: `tests/test_budget.py` → 11 passed.

RUL-09 (all 600 rows of the audited run)
```
600 1 600
```

RUL-10 (src, configs, prompt template, tests, notebooks, dev)
```
needles=1355 files=58 hits=0 prompt_files=1
```

RUL-11: exit 1. RUL-15: exit 1. RUL-18: both scans exit 1.

RUL-12
```
OK: dev_questions.csv, 62 rows, 0 problem(s)
exit 0
```

RUL-13
```
src/municipal_qa/llm/client.py:8:import httpx
```

RUL-14: every file opened outside `readers/`, `data/`, `evaluation/` and `submission/` is config, a prompt template, the passage cache or a run artifact (`cli.py:159`, `config.py:164`, `retrieval/bm25.py:49`, `pipeline/run.py:70,92,101,106,124,125,150`, `llm/prompts/__init__.py:14`); readers receive paths only from `SourceInfo` built by `data/manifest.py`, and `retrieval/bm25.py` searches only IDs present in the manifest.

RUL-16
```
Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/
```

RUL-17 / RUL-19
```
 certifi                    2026.11.20  Mozilla Public License 2.0 (MPL 2.0)
 fqdn                       1.6.0       MPL-2.0
(second list empty = RUL-19 pass)
0
```

SUB-01, SUB-03, SUB-04, SUB-08
```
1 warning(s), e.g. file is 795,356 bytes, above the unofficial 256 KiB guidance
valid: 600 Test IDs in order, 795,356 bytes
exit 0
2205          # lines matching ^- SRC_[0-9a-f]{12} \| .+ \| .+$
2337          # lines starting "- "
## lines 600 | ### Evidence 600
SHA256 prefix e41ef0d2d5097cec
answer lines starting '- ': 132 in 30 answers; any SRC-like: False
```

SUB-05
```
evidence lines per answer: {1: 16, 2: 48, 3: 51, 4: 485} | lines citing outside Source_IDs: 0 | file-level lines: 38 of 2205
answers with filename-like evidence locator using original filename instead of SRC id: 0
```

SUB-06 (three records via `mqa show`, structure only): every evidence line is `SRC_<id> | locator | claim` with page, JSON-pointer, section or line locators; `calls 1`, `fallback False`, `error None`.

SUB-07: `tests/test_answer.py` → 10 passed (six injected failures, never blank).

Finding 1 evidence
```
15:CACHE_VERSION = 1
42:        return self.cache_dir / f"{sid}.v{CACHE_VERSION}.json" if self.cache_dir else None
48:        if path and path.exists():
newest reader/retrieval source: Nov 25 23:18:24 2026 src/municipal_qa/retrieval/bm25.py
cache files older than it: 0 of 183
```

Finding 2 evidence
```
53:def new_run_id(now: dt.datetime | None = None) -> str:
55:    return f"{now:%Y%m%d-%H%M}-{git_info()['short'] or 'nogit'}"
94:    run_id = resume or runner.new_run_id()
107:    run_dir.mkdir(parents=True, exist_ok=True)
```

COR-01/02/03/07 (context, spreadsheet, markers, delimited/sqlite/archive, text, structured tests)
```
102 passed in 13.58s
```

COR-06: `rg -n "make_valid|buffer\(0" src` → exit 1. COR-08: every `read_csv` over source or question data uses `dtype=str, keep_default_na=False`; DuckDB uses `all_varchar=true`; SQLite keeps the database's types. COR-04: the only `float(` in `pipeline/` and `retrieval/` is a BM25 score (`bm25.py:97`).

TST-01, TST-05
```
253 passed in 13.32s
1 passed, 22 deselected (test_train_references_score_one_and_empty_scores_zero)
```

LAY-01, LAY-07
```
LAY-07 blocks > 5 lines: none
LAY-01 schema AST == plan §5: True
```

LAY-02: exit 1. LAY-04: `scorer.py:149` and `pipeline/evidence.py:19` (finding 4). LAY-06: no inline prompt text in Python. LAY-08: attribution scans exit 1 (repo, commits on this branch, PR #2 body).

BLD-01..04
```
/Users/Apple/.../municipal-qa/.venv
All checks passed!
57 files already formatted
9
.gitignore:230:municipal-qa/.venv/
.gitignore:231:municipal-qa/artifacts/
.gitignore:232:municipal-qa/.env
.gitignore:227:/Data/
municipal-qa/notebooks/00_starter.ipynb: filter: nbstripout
```

REP-01/02/03
```
3
['answered_this_session', 'answers', 'config', 'finished', 'git', 'max_calls', 'max_generated_tokens', 'model', 'packages', 'platform', 'python', 'seed', 'split', 'started', 'status']
{'commit': 'f27f59e283ea0b5e892d517f22d921bec1489c83', 'short': 'f27f59e', 'dirty': False} 42 {'temperature': 0, 'seed': 42, 'num_ctx': 8192} 8f68893c685c
```

ACC-01, ACC-02
```
T-101[x] T-102[x] T-103[x] T-104[x] T-105[x] T-106[x] T-107[x] T-108[x] T-109[x]
DoD1[x] DoD2[x] DoD3[x] DoD4[x] DoD5[x] DoD6[x] DoD7[x] DoD8[x] DoD9[x] DoD10[x]
M1 status: awaiting verify; open blockers: B-003 only (blocks T-404, M4)
```

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | PASS | RUL-01, 03, 06, 09 (600/600 rows: max 1 call, 600 tokens), 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 pass. RUL-04, 08 and the RUL-05 test not verified (not built). |
| 2. Submission validity and evidence | PASS with minor | Valid, 600 IDs in order, every answer non-blank with 1-4 code-built `SRC_` lines inside its Source_IDs. Finding 3. Size above unofficial guidance is a Gap (T-306). |
| 3. Correctness and conventions | PASS (M1 scope) | Stored/displayed, markers, read-only SQLite, string-preserving reads verified. Calc, spatial and composer checks belong to M2/M3. |
| 4. Reproducibility and licensing | PASS with major | Manifest complete, clean git hash, seed 42, temperature 0. Findings 1 and 2. |
| 5. Tests | PASS | 253 passed; scorer 1.0 / 0.0 on Train. |
| 6. Layering and DRY | PASS with minor | Findings 4 and 5. Schema identical to plan §5; no comment block over 5 lines. |
| 7. Build and lint | PASS | BLD-01..04 pass. |
| 8. Milestone acceptance | PASS | All 9 tasks and all 10 DoD rows `[x]` with Change log proof. |

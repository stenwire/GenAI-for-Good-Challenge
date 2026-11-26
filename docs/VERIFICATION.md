# Verification Report

**Milestone:** M1 - Baseline Submission
**Scope:** M1 tasks T-101..T-116 and all 10 Definition-of-Done rows, with a code re-read of everything changed since the previous M1 audit (`adbb92c..32d777a`: `retrieval/bm25.py`, `pipeline/answer.py`, `pipeline/run.py`, `cli.py` run, `config.py`, `configs/default.yaml`, `evaluation/scorer.py`, `dev/build_devset.py`, `tests/test_answer.py`, `tests/test_run.py`); every checklist item M1 code touches; Dimension 1 (RUL-01..RUL-19) in full. Branch `m1-baseline-submission` at `32d777a`, with `SUBMISSIONS.md`, `docs/MILESTONES.md` and `docs/TODO.md` already modified (uncommitted T-115 record) when the run began.
**Run audited:** `20261125-2323-f27f59e` (Test, 600 questions; the uploaded file, Zindi `69RjT4Yb`). Produced by `f27f59e`, before the T-110..T-114 changes; those were checked against the code, the test suite, the 3-question dev run `20261126-132606-adbb92c` and a replay of `clean_answer` over the stored answers.
**Date:** 2026-11-26
**Blockers:** 0
**Major:** 0
**Minor:** 3
**Verdict:** PASS
**Dimensions not verified:** RUL-04 (no embedder or reranker yet), RUL-08 (no shared generative preprocessing exists), RUL-05's import-scan test (`evaluation/judge.py` not built; test is T-303); COR-04/05/09/10/11 and LAY-03 (calc, spatial, composer, verifier, op Facts not built in M1); REP-05..07 (no local encoders yet; README is M3/M4); SUB-07's `-k fallback` selector matches no test (covered instead by `tests/test_answer.py`, 14 passed). Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, challenge data in public git history. The Zindi file-size Gap is partly answered by user evidence (Zindi scored the 795,356-byte file, public 0.215775462) but has no organiser figure; it stays a Gap until the checklist is updated.

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|
| 1 | minor | 2. Submission | `municipal-qa/src/municipal_qa/pipeline/answer.py:41`, `:51`, `:65` | `_fold_bullets` treats any line starting `N.` or `N)` as a list item, so an answer that opens with its value loses it: `clean_answer("15. Fifteen routes changed.")` → `"Fifteen routes changed."`. Because lines are `rstrip`ped before folding, a bare marker line (`-`, `*`) is not matched and the next item is joined onto it: `"Total:\n- \n- x"` → `"Total:\n- x."`, leaving a line that starts `- `. Neither case occurs in the 662 stored M1 answers (0 numbered lines). | Fold numbered lines only when two or more consecutive lines are numbered; drop bare marker lines before folding; add both cases to `test_bullets_fold_into_sentences`. |
| 2 | minor | 5. Tests | `municipal-qa/tests/test_answer.py:207-214` | `test_fingerprint_covers_every_reader_module` rewrites the tracked file `src/municipal_qa/readers/spreadsheet.py` and restores it in `finally`. An interrupted or parallel test run can leave the source modified or race another test that reads it. | Copy the readers directory to `tmp_path` and monkeypatch `bm25._PKG` (or the glob root) instead of editing the real source. |
| 3 | minor | 4. Reproducibility | `municipal-qa/src/municipal_qa/retrieval/bm25.py:25` | `code_fingerprint` hashes `readers/*.py` and `bm25.py` only. Passages also depend on `schema.py` (`Passage` shape, revalidated from cache with defaults) and on reader library versions (`pdfplumber`, `openpyxl`, `lxml`, `beautifulsoup4`, `python-docx`, `duckdb` in `uv.lock`); a lock bump changes extracted text without invalidating the cache. | Add `schema.py` and the installed versions of the reader libraries (or the `uv.lock` hash) to the fingerprint. |

## Commands run

```
$ git status --porcelain            # at start of run
 M SUBMISSIONS.md
 M docs/MILESTONES.md
 M docs/TODO.md
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
32d777a T-110..T-114, T-116 (S5): fix verify M1 findings and add submission checkpoints
$ ls municipal-qa/artifacts/runs/
20261125-2322-f27f59e
20261125-2323-f27f59e
20261126-132606-adbb92c
$ git diff --stat adbb92c..HEAD -- municipal-qa
 municipal-qa/configs/default.yaml                  |  2 +
 municipal-qa/dev/build_devset.py                   |  3 +-
 municipal-qa/src/municipal_qa/cli.py               |  5 +-
 municipal-qa/src/municipal_qa/config.py            |  2 +
 municipal-qa/src/municipal_qa/evaluation/scorer.py |  3 +-
 municipal-qa/src/municipal_qa/pipeline/answer.py   | 32 ++++++++--
 municipal-qa/src/municipal_qa/pipeline/run.py      |  4 +-
 municipal-qa/src/municipal_qa/retrieval/bm25.py    | 19 +++++-
 municipal-qa/tests/test_answer.py                  | 74 +++++++++++++++++++++-
 municipal-qa/tests/test_run.py                     | 18 ++++++
 10 files changed, 150 insertions(+), 12 deletions(-)
```

RUL-01
```
model: qwen3:1.7b
digest: 8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7
parameters: 2,031,739,904 (cap 3,000,000,000) OK
quantization: Q4_K_M
exit 0
```

RUL-02, RUL-07: `llm/client.py` unchanged since the previous audit (not in the diff above).

RUL-03
```
municipal-qa/src/municipal_qa/llm/client.py:116:            data = self._post("/api/chat", payload)
municipal-qa/src/municipal_qa/llm/client.py:128:            raise LLMError(f"{purpose}: malformed /api/chat reply: {e}") from e
```

RUL-05: `rg -n "judge" pipeline llm cli.py` → exit 1; `evaluation/` holds `devset_check.py`, `report.py`, `scorer.py` only.

RUL-06: `pytest -o addopts="" -q tests/test_budget.py` → `11 passed in 0.01s`.

RUL-09 (all 600 rows of the audited run)
```
600 1 600
```

RUL-10 (src, configs, prompt template, tests, notebooks, dev)
```
needles=1355 files=58 hits=0 prompt_files=1
```

RUL-11: exit 1. RUL-15: exit 1. RUL-18: `git ls-files municipal-qa | rg ...` exit 1; `git ls-files | rg "^Data/"` exit 1.

RUL-12
```
OK: dev_questions.csv, 62 rows, 0 problem(s)
exit 0
```

RUL-13
```
municipal-qa/src/municipal_qa/llm/client.py:8:import httpx
```

RUL-14: the only new file read is `code_fingerprint` reading the package's own `readers/*.py` and `bm25.py` (code, not sources); source paths still come only from `SourceInfo` built by `data/manifest.py`.

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
(filtered, excluding certifi/fqdn):          <- empty = pass
0                                            <- fqdn not in the runtime tree
```

SUB-01, SUB-04, SUB-08
```
1 warning(s), e.g. file is 795,356 bytes, above the unofficial 256 KiB guidance
valid: 600 Test IDs in order, 795,356 bytes
exit 0
e41ef0d2d5097cec        # SHA256 prefix, matches SUBMISSIONS.md
2205                    # ^- SRC_... evidence lines
2337                    # lines starting "- " (file predates T-112)
```

T-112 replay of the current `clean_answer` over stored answers (counts only)
```
20261125-2322-f27f59e answers with list lines: before 3 after clean_answer 0 of 62
20261125-2323-f27f59e answers with list lines: before 30 after clean_answer 0 of 600
```

Finding 1 evidence
```
20261125-2322-f27f59e answers whose FIRST line starts N./N): 0 any line: 0
20261125-2323-f27f59e answers whose FIRST line starts N./N): 0 any line: 0
'15. Fifteen routes changed.' -> 'Fifteen routes changed.'
'7) seven' -> 'seven.'
'* ' -> '*'
'Total:\n- \n- x' -> 'Total:\n- x.'
```

Finding 2 evidence (`tests/test_answer.py`)
```
207:    target = bm25._PKG / "readers" / "spreadsheet.py"
210:        target.write_bytes(original + b"\n")
214:        target.write_bytes(original)
```

Finding 3 evidence: `bm25.py:25` `for f in sorted([*(_PKG / "readers").glob("*.py"), Path(__file__).resolve()]):`; readers import `municipal_qa.schema` (8 modules) and `municipal_qa.config` (1).

SUB-02, SUB-07
```
16 passed in 0.23s                 # tests/test_submission.py
258 deselected in 0.47s            # -k fallback: selector matches nothing
14 passed in 0.28s                 # tests/test_answer.py (six injected failures, never blank)
```

REP-01/02/03
```
.python-version
pyproject.toml
uv.lock
8:  options: {temperature: 0, seed: 42, num_ctx: 8192}
9:  think: false
20261125-2323-f27f59e f27f59e False 42 0 8f68893c685c None complete
20261126-132606-adbb92c adbb92c True 42 0 8f68893c685c 429363157b34a8e4 complete
```
(The audited run predates `passage_cache_code`; the smoke run records it. Its `dirty: True` is the uncommitted T-110..T-114 work at the time; it is not a submission run.)

REP-04: exit 1.

TST-01, TST-05
```
258 passed in 13.49s
23 passed, 235 deselected in 0.38s
```

LAY-04, LAY-07, LAY-08
```
src/municipal_qa/pipeline/evidence.py:19:    return f"{source_id} | {clean(locator) or 'file'} | {clean(claim) or 'source consulted'}"
LAY-07 blocks > 5 lines: none
attribution scans (repo, commits main..HEAD, PR #2 body): exit 1, exit 1, exit 1
```

BLD-01..04
```
/Users/Apple/.../municipal-qa/.venv
All checks passed!
57 files already formatted
9
.gitignore:230:municipal-qa/.venv/	municipal-qa/.venv
.gitignore:231:municipal-qa/artifacts/	municipal-qa/artifacts
.gitignore:232:municipal-qa/.env	municipal-qa/.env
.gitignore:227:/Data/	Data/sources
```

ACC-01, ACC-02
```
T-101[x] T-102[x] T-103[x] T-104[x] T-105[x] T-106[x] T-107[x] T-108[x] T-109[x] T-110[x] T-111[x] T-112[x] T-113[x] T-114[x] T-115[x] T-116[x]
DoD1[x] DoD2[x] DoD3[x] DoD4[x] DoD5[x] DoD6[x] DoD7[x] DoD8[x] DoD9[x] DoD10[x]
M1 status: in progress
open blockers: B-003 only (blocks T-404, M4)
SUBMISSIONS.md leaderboard: 0.215775462 public (Zindi `69RjT4Yb`, uploaded 2026-11-26; rank 98, team Theus)
```
T-115's tick and the leaderboard entry are in the working tree, not yet committed. M1 reads `in progress` although every task is `[x]`; `/implement` moves it on.

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | PASS | RUL-01, 03, 05 (scan), 06, 09 (600/600 rows: max 1 call, 600 tokens), 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 pass. RUL-04, 08 and the RUL-05 test not verified (not built). |
| 2. Submission validity and evidence | PASS with minor | Audited file valid, 600 IDs in order, non-blank, 2,205 code-built evidence lines; the T-112 fix removes every list line from the stored answers on replay. Finding 1. |
| 3. Correctness and conventions | PASS (M1 scope) | No reader, marker or SQLite code changed since the previous audit; calc, spatial and composer checks belong to M2/M3. |
| 4. Reproducibility and licensing | PASS with minor | Previous major (cache key) fixed: key is source SHA256 + reader-code hash, recorded in the run manifest; run IDs have seconds and an existing run directory is refused. Finding 3. |
| 5. Tests | PASS with minor | 258 passed; scorer 1.0 / 0.0 on Train. Finding 2. |
| 6. Layering and DRY | PASS | Evidence lines formatted only in `pipeline/evidence.py`; `docs_first` and `evidence_from_top` now in `configs/default.yaml`. No comment block over 5 lines. |
| 7. Build and lint | PASS | BLD-01..04 pass. |
| 8. Milestone acceptance | PASS | All 16 tasks and all 10 DoD rows `[x]` (T-115 uncommitted); leaderboard score recorded. |

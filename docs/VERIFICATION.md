# Verification Report

**Milestone:** M1 - Baseline Submission
**Scope:** M1 tasks T-101..T-120 and all 10 Definition-of-Done rows, with a code re-read of everything changed since the third M1 audit (`64cce8c..e8acff7`: `pipeline/answer.py` `_is_item`/`_fold_bullets`, `tests/test_answer.py`, tracker updates); every checklist item M1 code touches; Dimension 1 (RUL-01..RUL-19) in full. Branch `m1-baseline-submission` at `e8acff7`, clean apart from the long-standing untracked `.claude/` and `docs/CHALLENGE_BRIEF.md`.
**Run audited:** `20261125-2323-f27f59e` (Test, 600 questions; the uploaded file, Zindi `69RjT4Yb`, public 0.215775462). Produced by `f27f59e`, before T-110..T-120; the T-120 change was checked against the code, the suite, edge-case probes and a replay of `clean_answer` over the stored dev and Test answers.
**Date:** 2026-11-26
**Blockers:** 0
**Major:** 0
**Minor:** 0
**Verdict:** PASS
**Dimensions not verified:** RUL-04 (no embedder or reranker yet), RUL-08 (no shared generative preprocessing exists), RUL-05's import-scan test (`evaluation/judge.py` not built; test is T-303); COR-04/05/09/10/11 and LAY-03 (calc, spatial, composer, verifier, op Facts not built in M1); REP-05..07 (no local encoders yet; README is M3/M4). Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, challenge data in public git history; Zindi file-size limit (user evidence: Zindi scored the 795,356-byte file; no organiser figure yet).

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|

No findings. Third-pass finding 1 (T-120) is confirmed fixed: both probes keep their numbers, and nine further edge-case probes plus the replay over 662 stored answers lose no value, leave no bullet line, no blank answer and no `## ` line.

## Commands run

```
$ git status --porcelain            # at start of run
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
e8acff7 T-120 (S5): keep leading numbers when folding answer lists; complete M1
$ git diff --stat 64cce8c..HEAD
 docs/MILESTONES.md                               |   4 +-
 docs/TODO.md                                     |   8 ++
 docs/VERIFICATION.md                             | 138 ++++++++++-------------
 municipal-qa/src/municipal_qa/pipeline/answer.py |   9 +-
 municipal-qa/tests/test_answer.py                |   7 +-
 5 files changed, 77 insertions(+), 89 deletions(-)
```

RUL-01
```
model: qwen3:1.7b
digest: 8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7
parameters: 2,031,739,904 (cap 3,000,000,000) OK
quantization: Q4_K_M
exit 0
```

RUL-02, RUL-07: `llm/client.py` unchanged since the first M1 audit.

RUL-03
```
municipal-qa/src/municipal_qa/llm/client.py:116:            data = self._post("/api/chat", payload)
municipal-qa/src/municipal_qa/llm/client.py:128:            raise LLMError(f"{purpose}: malformed /api/chat reply: {e}") from e
```

RUL-05: exit 1 (`evaluation/` has no `judge.py`). RUL-11: exit 1. RUL-15: exit 1. RUL-18: exit 1. RUL-14: no file access changed since the third pass.

RUL-06: `11 passed in 0.01s`.

RUL-09 (all 600 rows of the audited run)
```
600 1 600
```

RUL-10 (src, configs, prompt template, tests, notebooks, dev)
```
needles=1355 files=58 hits=0 prompt_files=1
```

RUL-12
```
OK: dev_questions.csv, 62 rows, 0 problem(s)
exit 0
```

RUL-13
```
municipal-qa/src/municipal_qa/llm/client.py:8:import httpx
```

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
(filtered):          <- empty = pass
0                    <- fqdn not in the runtime tree
```

SUB-01, SUB-02, SUB-04, SUB-07, SUB-08
```
1 warning(s), e.g. file is 795,356 bytes, above the unofficial 256 KiB guidance
valid: 600 Test IDs in order, 795,356 bytes
exit 0
e41ef0d2d5097cec
2205
2337          # file predates T-112..T-120; the replay below shows the current cleaner removes every bullet line
16 passed in 0.23s       # tests/test_submission.py
14 passed in 0.33s       # tests/test_answer.py
```

T-120 probes and replay of the current `clean_answer` (counts only for stored answers)
```
'15. Fifteen routes changed.\n- two of them in 2024' -> '15. Fifteen routes changed. two of them in 2024.'
'- 5. stops closed' -> '5. stops closed.'
'15. Fifteen routes changed.' -> '15. Fifteen routes changed.'
'Steps:\n1. Open\n2) Close' -> 'Steps: Open. Close.'
'Total:\n- \n- x\n*' -> 'Total: x.'
'Changes:\n- 3 closed\n1. one new' -> 'Changes: 3 closed.\n1. one new'
'No. The [z] marker is not zero.' -> 'No. The [z] marker is not zero.'
'3.48 ha.\n- zone A' -> '3.48 ha. zone A.'
'Answer:\n\n- a\n\n- b' -> 'Answer:\n\na.\n\nb.'
20261125-2322-f27f59e bullet lines before 3 after 0 | blank after 0 | '## ' after 0 | changed 3 of 62
20261125-2323-f27f59e bullet lines before 30 after 0 | blank after 0 | '## ' after 0 | changed 30 of 600
```

REP-01/02/03/04
```
.python-version
pyproject.toml
uv.lock
8:  options: {temperature: 0, seed: 42, num_ctx: 8192}
9:  think: false
f27f59e False 42 0 8f68893c685c complete
REP-04 exit 1
```

TST-01, TST-05 (and source tree after the suite)
```
258 passed in 14.31s
(git status --porcelain -- src tests: empty)
23 passed, 235 deselected in 0.48s
```

LAY-04, LAY-07, LAY-08
```
municipal-qa/src/municipal_qa/pipeline/evidence.py:19:    return f"{source_id} | {clean(locator) or 'file'} | {clean(claim) or 'source consulted'}"
LAY-07 blocks > 5 lines: none
attribution scans (municipal-qa, commits main..HEAD, PR #2 body): exit 1, exit 1, exit 1
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
T-101[x] T-102[x] T-103[x] T-104[x] T-105[x] T-106[x] T-107[x] T-108[x] T-109[x] T-110[x] T-111[x] T-112[x] T-113[x] T-114[x] T-115[x] T-116[x] T-117[x] T-118[x] T-119[x] T-120[x]
DoD1[x] DoD2[x] DoD3[x] DoD4[x] DoD5[x] DoD6[x] DoD7[x] DoD8[x] DoD9[x] DoD10[x]
M1 status: complete
open blockers: B-003 only (blocks T-404, M4)
PR #2: OPEN
```

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | PASS | RUL-01, 03, 05 (scan), 06, 09 (600/600 rows: max 1 call, 600 tokens), 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 pass. RUL-04, 08 and the RUL-05 test not verified (not built). |
| 2. Submission validity and evidence | PASS | Audited file valid, 600 IDs in order, non-blank, 2,205 code-built evidence lines; current cleaner leaves no bullet line and loses no leading value. |
| 3. Correctness and conventions | PASS (M1 scope) | No reader, marker or SQLite code changed; calc, spatial and composer checks belong to M2/M3. |
| 4. Reproducibility and licensing | PASS | Manifest, seed, temperature, digest, cache fingerprint; licences clean. |
| 5. Tests | PASS | 258 passed; the suite writes no tracked file. |
| 6. Layering and DRY | PASS | Evidence lines formatted only in `pipeline/evidence.py`; no comment block over 5 lines. |
| 7. Build and lint | PASS | BLD-01..04 pass. |
| 8. Milestone acceptance | PASS | All 20 tasks and all 10 DoD rows `[x]`; M1 `complete` confirmed by this audit. |

# Verification Report

**Milestone:** M1 - Baseline Submission
**Scope:** M1 tasks T-101..T-119 and all 10 Definition-of-Done rows, with a code re-read of everything changed since the second M1 audit (`32d777a..64cce8c`: `pipeline/answer.py`, `retrieval/bm25.py`, `tests/test_answer.py`, plus the tracker and `SUBMISSIONS.md` updates); every checklist item M1 code touches; Dimension 1 (RUL-01..RUL-19) in full. Branch `m1-baseline-submission` at `64cce8c`, clean apart from the long-standing untracked `.claude/` and `docs/CHALLENGE_BRIEF.md`.
**Run audited:** `20261125-2323-f27f59e` (Test, 600 questions; the uploaded file, Zindi `69RjT4Yb`, public 0.215775462). Produced by `f27f59e`, before T-110..T-119; those changes were checked against the code, the suite, edge-case probes and the 2-question dev run `20261126-143730-32d777a`.
**Date:** 2026-11-26
**Blockers:** 0
**Major:** 0
**Minor:** 1
**Verdict:** PASS
**Dimensions not verified:** RUL-04 (no embedder or reranker yet), RUL-08 (no shared generative preprocessing exists), RUL-05's import-scan test (`evaluation/judge.py` not built; test is T-303); COR-04/05/09/10/11 and LAY-03 (calc, spatial, composer, verifier, op Facts not built in M1); REP-05..07 (no local encoders yet; README is M3/M4). Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, challenge data in public git history; Zindi file-size limit (user evidence: Zindi scored the 795,356-byte file; no organiser figure yet).

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|
| 1 | minor | 2. Submission | `municipal-qa/src/municipal_qa/pipeline/answer.py:50-51`, `:64` | Two narrower cases still drop a leading number when folding lists. A numbered first line followed by a bullet counts as a list item: `"15. Fifteen routes changed.\n- two of them in 2024"` → `"Fifteen routes changed. two of them in 2024."`. A bullet whose text starts with `N.` loses both markers, because `_NUMBERED.sub` runs after `_BULLET.sub`: `"- 5. stops closed"` → `"stops closed."`. None of the 662 stored M1 answers has a numbered line, so neither case has occurred. | Treat a numbered line as an item only when a neighbouring line is also numbered (not a bullet), and strip only the marker that matched; add both probes to `test_bullets_fold_into_sentences`. |

## Commands run

```
$ git status --porcelain            # at start of run
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
64cce8c T-117..T-119 (S5): harden list folding and the passage cache fingerprint
$ git diff --stat 32d777a..HEAD
 SUBMISSIONS.md                                   |   2 +-
 docs/MILESTONES.md                               |   6 +-
 docs/TODO.md                                     |  12 +-
 docs/VERIFICATION.md                             | 166 +++++++++++++----------
 municipal-qa/src/municipal_qa/pipeline/answer.py |  21 ++-
 municipal-qa/src/municipal_qa/retrieval/bm25.py  |  30 +++-
 municipal-qa/tests/test_answer.py                |  37 +++--
 7 files changed, 176 insertions(+), 98 deletions(-)
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

RUL-05: exit 1 (`evaluation/` has no `judge.py`). RUL-11: exit 1. RUL-15: exit 1. RUL-18: exit 1.

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

RUL-14: new reads in `code_fingerprint` are package code (`readers/*.py`, `retrieval/bm25.py`, `schema.py`) and `importlib.metadata`; sources still resolve only through `data/manifest.py`.

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

SUB-01, SUB-04, SUB-08
```
1 warning(s), e.g. file is 795,356 bytes, above the unofficial 256 KiB guidance
valid: 600 Test IDs in order, 795,356 bytes
exit 0
e41ef0d2d5097cec
2205
2337          # file predates T-112/T-117; replay of current clean_answer removes every list line (see T-117 Change log)
```

SUB-02, SUB-07
```
16 passed in 0.24s       # tests/test_submission.py
14 passed in 0.38s       # tests/test_answer.py (six injected failures, never blank)
```

T-119 fingerprint inputs and Finding 1 probes
```
{'beautifulsoup4': '4.15.0', 'duckdb': '2.0.1', 'lxml': '6.1.3', 'openpyxl': '3.1.5', 'pandas': '3.1.1', 'pdfminer.six': '20260107', 'pdfplumber': '0.11.11', 'pillow': '13.0.0', 'python-docx': '1.2.0'}
fingerprint 759acd4dc3e84b5f          # equals passage_cache_code in run 20261126-143730-32d777a
'15. Fifteen routes changed.' -> '15. Fifteen routes changed.'
'Total:\n- \n- x\n*' -> 'Total: x.'
'Steps:\n1. Open\n2) Close' -> 'Steps: Open. Close.'
'- 5. stops closed' -> 'stops closed.'
'15. Fifteen routes changed.\n- two of them in 2024' -> 'Fifteen routes changed. two of them in 2024.'
```

Finding 1 location
```
50:    near = [lines[j] for j in (i - 1, i + 1) if 0 <= j < len(lines)]
51:    return bool(_NUMBERED.match(lines[i])) and any(
64:        item = _NUMBERED.sub("", _BULLET.sub("", ln), count=1).strip()
```

REP-01/02/03/04
```
.python-version
pyproject.toml
uv.lock
8:  options: {temperature: 0, seed: 42, num_ctx: 8192}
9:  think: false
20261125-2323-f27f59e f27f59e False 42 0 8f68893c685c None complete
20261126-143730-32d777a 32d777a True 42 0 8f68893c685c 759acd4dc3e84b5f complete
REP-04 exit 1
```

TST-01, TST-05 (and source tree after the suite)
```
258 passed in 13.77s
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
T-101[x] T-102[x] T-103[x] T-104[x] T-105[x] T-106[x] T-107[x] T-108[x] T-109[x] T-110[x] T-111[x] T-112[x] T-113[x] T-114[x] T-115[x] T-116[x] T-117[x] T-118[x] T-119[x]
DoD1[x] DoD2[x] DoD3[x] DoD4[x] DoD5[x] DoD6[x] DoD7[x] DoD8[x] DoD9[x] DoD10[x]
M1 status: awaiting verify
open blockers: B-003 only (blocks T-404, M4)
PR #2: OPEN
```

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | PASS | RUL-01, 03, 05 (scan), 06, 09 (600/600 rows: max 1 call, 600 tokens), 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 pass. RUL-04, 08 and the RUL-05 test not verified (not built). |
| 2. Submission validity and evidence | PASS with minor | Audited file valid, 600 IDs in order, non-blank, 2,205 code-built evidence lines. Second-pass finding 1 fixed for its probes; Finding 1 is a narrower residue. |
| 3. Correctness and conventions | PASS (M1 scope) | No reader, marker or SQLite code changed; calc, spatial and composer checks belong to M2/M3. |
| 4. Reproducibility and licensing | PASS | Second-pass finding 3 fixed: fingerprint covers reader code, `schema.py` and nine reader library versions, and the run manifest records it. |
| 5. Tests | PASS | 258 passed; the suite no longer writes tracked files (second-pass finding 2 fixed). |
| 6. Layering and DRY | PASS | Evidence lines formatted only in `pipeline/evidence.py`; no comment block over 5 lines. |
| 7. Build and lint | PASS | BLD-01..04 pass. |
| 8. Milestone acceptance | PASS | All 19 tasks and all 10 DoD rows `[x]`; leaderboard score recorded. |

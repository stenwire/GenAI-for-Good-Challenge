# Verification Report

**Milestone:** M0 - Foundation
**Scope:** M0 tasks T-001..T-023 and all 12 Definition-of-Done rows; every checklist item touched by M0 code (`config.py`, `rules.py`, `cli.py`, `schema.py`, `pipeline/context.py`, `data/`, `submission/`, `llm/budget.py`, `llm/client.py`, `notebooks/00_starter.ipynb`, tests, `pyproject.toml`, `uv.lock`, `.gitignore`, `LICENSE`); Dimension 1 (competition rules, RUL-01..RUL-19) in full; re-check of the 3 findings from the second M0 verify pass, plus edge-case probes of the code changed since (`_parse_chat_reply`, determinism validators). Branch `m0-foundation` at `57373a4`.
**Run audited:** none (no `municipal-qa/artifacts/runs/` exists; `mqa run` is not built in M0)
**Date:** 2026-11-25
**Blockers:** 0
**Major:** 0
**Minor:** 0
**Verdict:** PASS
**Dimensions not verified:** Run-based checks RUL-09, SUB-01 (on a run), SUB-04, SUB-08, REP-03 (no run exists). Checks on modules not built in M0: RUL-04, RUL-08, RUL-12, RUL-14, SUB-05..SUB-07, COR-02..COR-07, COR-09..COR-11, TST-05, LAY-03, LAY-06, REP-05..REP-07, ACC-03..ACC-05. Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, Zindi file-size limit, challenge data remaining in public git history.

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|

None. Leads examined and dropped after confirmation:

- `eval_count` above `num_predict` (99,999): recorded as reported, then `BudgetExceeded` stops further calls. This is the defensive behaviour card M6 asks for; the client cannot stop a server that ignores `num_predict`.
- `options.temperature: false` passes the validator (`False == 0`), but live Ollama rejects it: `500 {"error":"option \"temperature\" must be of type float32"}`. It fails loudly and cannot silently change sampling.

### Second pass's findings (re-checked)

| Prev # | Was | Now | Proof |
|---|---|---|---|
| 1 | blocker: malformed 200 reply uncharged | fixed (T-021) | JSON list ×3 → `(3, 2100, 'LLMError')`; 20 malformed → `(15, 10500, 'BudgetExceeded')`; `eval_count '12abc'` → `(1, 700, 'LLMError')` |
| 2 | minor: temperature, seed, think unvalidated | fixed (T-022) | `temperature: 0.7` refused; `think: true` refused; `temperature: 0.0` accepted |
| 3 | minor: RUL-13 flags `urllib.parse` | fixed (T-023) | RUL-13 now matches only `llm/client.py:8:import httpx` |

## Commands run

```
$ git status --porcelain            # at start of run
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
57373a4 T-023 (M0): narrow network-import check and return M0 to awaiting verify
$ ls municipal-qa/artifacts/runs
ls: municipal-qa/artifacts/runs: No such file or directory
```

RUL-01
```
model: qwen3:1.7b
digest: 8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7
parameters: 2,031,739,904 (cap 3,000,000,000) OK
quantization: Q4_K_M
exit 0
```

RUL-03 (both hits in the only module allowed to call the model)
```
municipal-qa/src/municipal_qa/llm/client.py:116:            data = self._post("/api/chat", payload)
municipal-qa/src/municipal_qa/llm/client.py:128:            raise LLMError(f"{purpose}: malformed /api/chat reply: {e}") from e
```

RUL-05: exit 1 (no hits). RUL-06: `tests/test_budget.py` 11 passed. RUL-11: exit 1. RUL-12: `municipal_qa.evaluation` not present → not verified (T-106, M1). RUL-15: exit 1.

RUL-10 (scanned `src`, `configs`, `tests`, `notebooks`; `llm/prompts/` does not exist yet)
```
needles=1355 files=27 hits=0
```

RUL-13 (narrowed pattern from T-023)
```
municipal-qa/src/municipal_qa/llm/client.py:8:import httpx
```

RUL-16
```
Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/
```

RUL-17
```
 Name                       Version     License
 fqdn                       1.6.0       MPL-2.0
 certifi                    2026.11.20  Mozilla Public License 2.0 (MPL 2.0)
```

RUL-18: both `git ls-files` scans exit 1 (no challenge data, `.env` or zip tracked).

RUL-19
```
(no lines above = pass)
0
```

Reply-parser and validator probes (read-only, from stdin, mock transport)
```
prev-blocker JSON list x3: (3, 2100, 'LLMError')
eval '12abc': (1, 700, 'LLMError')
eval 99999 (above caps): (1, 99999, 'BudgetExceeded')
eval null message null: (1, 700, None)
loop of 20 malformed: (15, 10500, 'BudgetExceeded')
refused {'llm': {'options': {'temperature': 0.7, 'seed': 42}}}
refused {'llm': {'think': True}}
ACCEPTED {'llm': {'options': {'temperature': 0.0, 'seed': 42}}}
ACCEPTED {'llm': {'options': {'temperature': False, 'seed': 42}}}
```

Live check of the `temperature: false` lead (one direct request to the local Ollama server)
```
temperature=false -> 500 {"error":"option \"temperature\" must be of type float32"}
```

Tests
```
tests/test_submission.py: 16 passed in 0.49s
tests/test_context.py: 33 passed in 0.83s
tests/test_client.py + tests/test_config.py: 61 passed in 0.16s
full suite: 137 passed in 1.12s
```

DoD 6 and DoD 8
```
verified 188 sources (size and SHA256) in /Users/Apple/Desktop/Lab/playground/GenAI-for-Good-Challenge/Data/sources [0.7s]
exit 0
600 warning(s), e.g. 0044bdd2-0acc-4876-a094-6257c2879406: no evidence lines
valid: 600 Test IDs in order, 182,042 bytes
exit 0
```

BLD-01..BLD-04
```
/Users/Apple/Desktop/Lab/playground/GenAI-for-Good-Challenge/main/GenAI-for-Good-Challenge/municipal-qa/.venv
All checks passed!
27 files already formatted
mqa --help commands matched: 9
nbstripout verify exit 0
.gitignore:230:municipal-qa/.venv/
.gitignore:231:municipal-qa/artifacts/
.gitignore:232:municipal-qa/.env
.gitignore:227:/Data/
.gitignore:81:.ipynb_checkpoints
municipal-qa/notebooks/00_starter.ipynb: filter: nbstripout
```

REP-01, REP-02, REP-04
```
uv.lock, pyproject.toml, .python-version tracked: 3; .venv tracked: none
8:  options: {temperature: 0, seed: 42, num_ctx: 8192}
9:  think: false
REP-04 (src, configs, notebooks): exit 1
```

LAY-01, LAY-02, LAY-04, LAY-07, LAY-08
```
LAY-07 blocks > 5 lines: none
LAY-01 schema AST == plan §5: True
LAY-02: exit 1; LAY-04: exit 1
LAY-08: rg in municipal-qa exit 1; git log (80 commits) exit 1; PR #1 body exit 1
```

ACC-01, ACC-02
```
M0 tasks not [x]: 0 of 23
M0 DoD rows [x]: 12 of 12
M0 status: awaiting verify
B-001, B-002, B-004, B-005, B-006 resolved; B-003 open (blocks T-404, M4)
```

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | PASS | RUL-01, 03, 05, 06, 10 (1,355 needles, 27 files, 0 hits), 11, 13, 15, 16, 17, 18, 19 pass; budget probes stop at 15 calls. Not verified: RUL-04, 08, 09, 12, 14 (not built / no run). |
| 2. Submission validity and evidence | PASS | SUB-02, SUB-03 pass; placeholder validates. SUB-01/04/08 on a run not verified; SUB-05..07 not built. |
| 3. Correctness and conventions | PASS (M0 scope) | COR-01, COR-08 pass. Others not built in M0. |
| 4. Reproducibility and licensing | PASS (M0 scope) | REP-01, 02, 04 pass; determinism now enforced in config. REP-03, 05..07 and Gaps rows not verified. |
| 5. Tests | PASS | 137 passed, nothing skipped with data present. TST-05 not built. |
| 6. Layering and DRY | PASS | LAY-01 AST identical; LAY-02, 04, 07, 08 pass. LAY-03, 06 not built. |
| 7. Build and lint | PASS | BLD-01..04 pass. |
| 8. Milestone acceptance | PASS | T-001..T-023 and DoD 1..12 all `[x]`; DoD proofs 6, 8, 9 re-run and pass. |

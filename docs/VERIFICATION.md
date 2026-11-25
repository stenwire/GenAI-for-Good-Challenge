# Verification Report

**Milestone:** M0 - Foundation
**Scope:** M0 tasks T-001..T-020 and all 12 Definition-of-Done rows; every checklist item touched by M0 code (`config.py`, `rules.py`, `cli.py`, `schema.py`, `pipeline/context.py`, `data/`, `submission/`, `llm/budget.py`, `llm/client.py`, `notebooks/00_starter.ipynb`, tests, `pyproject.toml`, `uv.lock`, `.gitignore`, `LICENSE`); Dimension 1 (competition rules, now including RUL-19) in full; re-check of all 7 findings from the previous M0 verify run. Branch `m0-foundation` at `0fb3a93`.
**Run audited:** none (no `municipal-qa/artifacts/runs/` exists; `mqa run` is not built in M0)
**Date:** 2026-11-25
**Blockers:** 1
**Major:** 0
**Minor:** 2
**Verdict:** FAIL
**Dimensions not verified:** Run-based checks RUL-09, SUB-01 (on a run), SUB-04, SUB-08, REP-03 (no run exists). Checks on modules not built in M0: RUL-04, RUL-08, RUL-12, RUL-14, SUB-05..SUB-07, COR-02..COR-07, COR-09..COR-11, TST-05, LAY-03, LAY-06, REP-05..REP-07, ACC-03..ACC-05. Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, Zindi file-size limit, challenge data remaining in public git history.

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|
| 1 | blocker | 1. Rules (RUL-07) | `municipal-qa/src/municipal_qa/llm/client.py:123-131` | A `/api/chat` reply that arrives with HTTP 200 but is not a JSON object (or carries a non-numeric `eval_count`/`prompt_eval_count`) raises inside `data.get(...)` / `int(...)` before `budget.record`, so the request is not charged and the error is not an `LLMError`. Confirmed: three such replies → `calls_used=0 tokens_used=0 error=AttributeError is_LLMError=False`. A caller that catches broad exceptions and retries could exceed 15 calls / 12,000 tokens. A standard Ollama server returns an object, so this needs a broken server or proxy, but it is the same class of uncharged post-send path as the previous run's finding 1. | After a response arrives, parse it inside one guarded block: require a `dict`, coerce counts, and on any failure charge one call at `num_predict` and raise `LLMError`. Add tests for a JSON list body and a non-numeric `eval_count` asserting `calls_used == 1`, `tokens_used == num_predict`. |
| 2 | minor | 4. Reproducibility (REP-02) | `municipal-qa/src/municipal_qa/config.py:29-32` | `LLMConfig.options` is an unchecked `dict[str, Any]` and `think` an unchecked `bool`: a config could set `temperature` above 0, drop `seed`, or turn thinking on without any error, unlike the caps hardened in T-015. Today's `configs/default.yaml` values are correct (`temperature: 0`, `seed: 42`, `think: false`), so there is no current breach. | Validate `options.temperature == 0`, `options.seed` present, and `think is False` (plan §2, §6.6), with tests. |
| 3 | minor | Process (checklist) | `.claude/skills/verify/references/checklist.md` RUL-13; hit at `municipal-qa/src/municipal_qa/rules.py:4` | The RUL-13 regex matches `from urllib.parse import urlsplit`, which only parses strings (opened: `rules.py:3-21` does no I/O). The check is correct in intent but produces a false lead every run. | Exclude `urllib.parse` from the RUL-13 pattern, e.g. `urllib\b(?!\.parse)`. |

### Previous run's findings (re-checked)

| Prev # | Was | Now | Proof |
|---|---|---|---|
| 1 | blocker: failed requests uncharged | fixed (T-014) | `F1 500: sent=15 calls_used=15 tokens_used=10500 stopped_by=BudgetExceeded`; same for timeout |
| 2 | major: caps configurable upward | fixed (T-015) | all four raised overrides refused; tampered `max_params` → `7,615,616,512 parameters > cap 3,000,000,000` |
| 3 | major: question precision after a comma | fixed (T-016) | `F3 ha: 2 general: 2` |
| 4 | minor: non-local `base_url` | fixed (T-017) | config override refused; tampered client → `refusing non-local model server` |
| 5 | minor: unknown source needs `source_ids` | fixed (T-018) | `tests/test_submission.py` 16 passed |
| 6 | minor: notebook acceptance, nbstripout | fixed (T-019) | filter installed, `git check-attr` → `nbstripout`, `--verify` exit 0 |
| 7 | minor: licensing gap not a check | fixed (T-020) | RUL-19 present, Licensing Gaps row removed; RUL-19 run passes |

## Commands run

```
$ git status --porcelain            # at start of run
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
0fb3a93 T-020 (M0): record licence check and return M0 to awaiting verify
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

RUL-03
```
municipal-qa/src/municipal_qa/llm/client.py:116:            data = self._post("/api/chat", payload)
```

RUL-05: `rg -n "judge" .../pipeline .../llm .../cli.py` → exit 1 (no hits; `tools/` does not exist yet)

RUL-06
```
tests/test_budget.py::test_defaults_are_competition_caps PASSED
tests/test_budget.py::test_15th_call_allowed_16th_refused PASSED
tests/test_budget.py::test_12000_tokens_allowed_12001_refused PASSED
tests/test_budget.py::test_can_call_uses_worst_case_not_expected_tokens PASSED
tests/test_budget.py::test_record_raises_when_tokens_exceeded PASSED
tests/test_budget.py::test_record_raises_on_16th_call PASSED
tests/test_budget.py::test_records_are_call_records_and_a_copy PASSED
tests/test_budget.py::test_budget_cannot_exceed_rule_caps[16-12000] PASSED
tests/test_budget.py::test_budget_cannot_exceed_rule_caps[15-12001] PASSED
tests/test_budget.py::test_budget_cannot_exceed_rule_caps[0-100] PASSED
tests/test_budget.py::test_budget_cannot_exceed_rule_caps[5-0] PASSED
11 passed in 0.01s
```

RUL-07 (finding 1), read-only probe from stdin with a mock transport
```
200 + JSON list x3 -> calls_used=0 tokens_used=0 error=AttributeError is_LLMError=False
```

RUL-10 (scanned `src`, `configs`, `tests`, `notebooks`; `llm/prompts/` does not exist yet)
```
needles=1355 files=27 hits=0
```

RUL-11: exit 1 (no hits). RUL-12: `No module named 'municipal_qa.evaluation'` → not verified (T-106, M1). RUL-15: exit 1 (no hits).

RUL-13
```
municipal-qa/src/municipal_qa/rules.py:4:from urllib.parse import urlsplit
municipal-qa/src/municipal_qa/llm/client.py:8:import httpx
```
`rules.py` opened: URL parsing only, no I/O (finding 3). `httpx` is in `llm/client.py` only.

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

RUL-18: `git ls-files municipal-qa | rg ...` → exit 1; `git ls-files | rg -i "^Data/|\.zip$"` → exit 1.

RUL-19
```
(awk filter printed no lines)
$ uv tree --no-dev --invert --package fqdn | grep -c "fqdn v"
0
```

Previous findings re-check (read-only probe from stdin)
```
F1 500: sent=15 calls_used=15 tokens_used=10500 stopped_by=BudgetExceeded
F1 timeout: sent=15 calls_used=15 tokens_used=10500 stopped_by=BudgetExceeded
F2/F4 refused {'llm': {'max_params': 8000000000}}
F2/F4 refused {'budget': {'max_calls': 99}}
F2/F4 refused {'budget': {'max_generated_tokens': 99999}}
F2/F4 refused {'llm': {'base_url': 'http://203.0.113.9:11434'}}
F2 tampered refused: qwen3:1.7b: 7,615,616,512 parameters > cap 3,000,000,000
F4 tampered refused: refusing non-local model server 'http://203.0.113.9:11434'
F3 ha: 2 general: 2
```

SUB-02, COR-01, TST-01
```
tests/test_submission.py: 16 passed in 0.56s
tests/test_context.py: 33 passed in 0.83s
full suite: 121 passed in 1.10s
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
.gitignore:230:municipal-qa/.venv/	municipal-qa/.venv
.gitignore:231:municipal-qa/artifacts/	municipal-qa/artifacts
.gitignore:232:municipal-qa/.env	municipal-qa/.env
.gitignore:227:/Data/	Data/sources
.gitignore:81:.ipynb_checkpoints	x/.ipynb_checkpoints/a
"/Users/Apple/.../municipal-qa/.venv/bin/python" -m nbstripout
municipal-qa/notebooks/00_starter.ipynb: filter: nbstripout
nbstripout verify exit 0
```

REP-01, REP-02, REP-04
```
municipal-qa/.python-version
municipal-qa/pyproject.toml
municipal-qa/uv.lock
8:  options: {temperature: 0, seed: 42, num_ctx: 8192}
9:  think: false
REP-04 (src, configs, notebooks): exit 1
```

LAY-01, LAY-02, LAY-04, LAY-07, LAY-08
```
LAY-07 blocks > 5 lines: none
LAY-01 schema AST == plan §5: True
LAY-02: exit 1; LAY-04: exit 1
LAY-08: rg in municipal-qa exit 1; git log (60 commits) exit 1; PR #1 body exit 1
```

ACC-01, ACC-02
```
T-001..T-020 all [x]
DoD1..DoD12 all [x]
M0 status: awaiting verify
B-001, B-002, B-004, B-005 resolved; B-003 open (blocks T-404, M4)
```

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | FAIL | Blocker 1 (malformed 200 reply uncharged). Pass: RUL-01, 03, 05, 06, 10 (1,355 needles, 27 files, 0 hits), 11, 13 (see finding 3), 15, 16, 17, 18, 19. Not verified: RUL-04, 08, 09, 12, 14 (not built / no run). |
| 2. Submission validity and evidence | PASS | SUB-02, SUB-03 pass; placeholder validates. SUB-01/04/08 on a run not verified; SUB-05..07 not built. |
| 3. Correctness and conventions | PASS (M0 scope) | COR-01 passes, previous comma finding fixed. COR-08 passes. Others not built in M0. |
| 4. Reproducibility and licensing | PASS with minor | REP-01, 02, 04 pass; finding 2 (options and `think` unvalidated). REP-03, 05..07 and Gaps rows not verified. |
| 5. Tests | PASS | 121 passed, nothing skipped with data present. TST-05 not built. |
| 6. Layering and DRY | PASS | LAY-01 AST identical; LAY-02, 04, 07, 08 pass. LAY-03, 06 not built. |
| 7. Build and lint | PASS | BLD-01..04 pass, including the nbstripout filter. |
| 8. Milestone acceptance | PASS | T-001..T-020 and DoD 1..12 all `[x]`; read-only DoD proofs (6, 8, 9) re-run and pass. |

# Verification Report

**Milestone:** M0 - Foundation
**Scope:** M0 tasks T-001..T-013 and all 12 Definition-of-Done rows; every checklist item touched by M0 code (`config.py`, `cli.py`, `schema.py`, `pipeline/context.py`, `data/`, `submission/`, `llm/budget.py`, `llm/client.py`, tests, `pyproject.toml`, `uv.lock`, `.gitignore`, `LICENSE`); Dimension 1 (competition rules) in full. Branch `m0-foundation` at `2f3f3ac`.
**Run audited:** none (no `municipal-qa/artifacts/runs/` exists; `mqa run` is not built in M0)
**Date:** 2026-11-25
**Blockers:** 1
**Major:** 2
**Minor:** 4
**Verdict:** FAIL
**Dimensions not verified:** Run-based checks RUL-09, SUB-01 (on a run), SUB-04, SUB-08, REP-03 (no run exists). Checks on modules not built in M0: RUL-04, RUL-08, RUL-12, RUL-14, SUB-05..SUB-07, COR-02..COR-07, COR-09..COR-11, TST-05, LAY-03, LAY-06, REP-05..REP-07, ACC-03..ACC-05. Checklist Gaps rows: Ollama pull in the rerun, rerun wall-clock limit, Zindi file-size limit, challenge data remaining in public git history (D-011 untracked it but did not purge history).

## Findings

| # | Severity | Dimension | Location | Finding | Remediation |
|---|---|---|---|---|---|
| 1 | blocker | 1. Rules (RUL-07) | `municipal-qa/src/municipal_qa/llm/client.py:34-46`, `:103-112` | A `/api/chat` request that reaches Ollama but fails afterwards (HTTP non-200, read timeout, dropped connection) raises before `budget.record`, so the call and its tokens are never charged. Confirmed: 20 such requests (HTTP 500, and `httpx.ReadTimeout`) left `calls_used=0 tokens_used=0` and `can_call(700)=True`. A caller that retries or falls back after `LLMError` can exceed 15 calls or 12,000 generated tokens for one question (brief §4.5 counts retries). The timeout also escapes as raw `httpx.ReadTimeout`, not `LLMError`. | Once the request may have reached the server (anything other than `ConnectError`/`ConnectTimeout` before sending), record a call charged at the worst case (`generated_tokens=num_predict`) before raising; wrap every `httpx.HTTPError` in `LLMError`. Add tests for HTTP 500, `ReadTimeout` and `RemoteProtocolError` asserting `calls_used == 1` and `tokens_used == num_predict`. |
| 2 | major | 1. Rules (RUL-01, RUL-06) | `municipal-qa/src/municipal_qa/config.py:26`, `:36-37`; `municipal-qa/src/municipal_qa/llm/client.py:55` | The competition caps are plain config values. Confirmed: `load_config(overrides={"llm": {"max_params": 8_000_000_000}, "budget": {"max_calls": 99, "max_generated_tokens": 99999}})` is accepted, and `check_model` then accepts a 7,615,616,512-parameter model. One YAML edit silently disables the 3B check; the budget caps will be equally loosenable once the pipeline reads them. | Pin rule constants in code (3,000,000,000 params; 15 calls; 12,000 tokens) and validate config values as `<=` those constants (Pydantic validators), or use `min(config, rule)`. Test that a raised config value is rejected. |
| 3 | major | 3. Correctness (COR-01); 5. Tests (TST-04) | `municipal-qa/src/municipal_qa/pipeline/context.py:85-104`, `:207-217`; `municipal-qa/tests/test_context.py:73-79`, `:92` | When the question sets a precision after a comma ("in hectares, rounded to two decimal places"), `_last_clause` drops the unit subject, so the precision becomes the general `decimals` and the Context's `decimals_overrides["ha"]=5` survives. Confirmed: Context "...except hectares to five decimals." + that question → `ha=5`, `decimals=2`. A hectare answer would be rounded to 5 places against the question's 2. Test Context #11 contains "except hectares to five decimals". Tests miss it: the override test has no comma, and the Train `06ffb5b9` check asserts `decimals == 2`, the default. | When the question states a precision, let it override Context overrides for units the question names (or for all units when it names none). Add a comma-phrased test and assert the effective hectare precision for Train `06ffb5b9`. |
| 4 | minor | 1. Rules (RUL-13) | `municipal-qa/src/municipal_qa/llm/client.py:27-31`; `municipal-qa/src/municipal_qa/config.py:24` | `base_url` is not restricted to a local host. Confirmed: `http://203.0.113.9:11434` is accepted. The default is `127.0.0.1`, so there is no current breach. | Reject non-loopback hosts in `LLMClient.__init__` (or in `LLMConfig` validation), with a test. |
| 5 | minor | 2. Submission (SUB-04) | `municipal-qa/src/municipal_qa/submission/validator.py:103-113` | Unknown `SRC_` IDs are only flagged when `source_ids` is also passed. `mqa validate` passes both, so the CLI is unaffected; direct callers passing only `manifest_ids` get no check. | Check `manifest_ids` independently of `source_ids`. |
| 6 | minor | 7. Build (BLD-04); 8. Acceptance (card M1) | plan §3.4, card M1; `municipal-qa/notebooks/` absent; `git config --get filter.nbstripout.clean` → exit 1 | Card M1's acceptance ("a notebook in VS Code runs `from municipal_qa.config import load_config` on the `.venv` kernel") is unproven, and the `nbstripout` git filter is not installed. No M0 task or DoD row covered it, and no notebook exists yet. | Queue in M1: add `notebooks/00_starter.ipynb` importing the package, run it on the `.venv` kernel, document `uv run nbstripout --install`. |
| 7 | minor | Process | `.claude/skills/verify/references/checklist.md` Gaps section | The Licensing gap (MPL-2.0 transitive dependencies) was settled by the user in D-024, but it is still listed as a gap and has no `[user]` check. | Move it into Dimension 1 as a `[user]` check citing D-024 (allowed: `certifi`, dev-only `fqdn`; no other copyleft), and remove the Gaps row. |

## Commands run

```
$ git status --porcelain            # at start of run
?? .claude/
?? docs/CHALLENGE_BRIEF.md
$ git log --oneline -1
2f3f3ac M0: mark Foundation awaiting verify
$ ls municipal-qa/artifacts/runs
ls: municipal-qa/artifacts/runs: No such file or directory
```

RUL-01
```
$ cd municipal-qa && uv run mqa check-model
model: qwen3:1.7b
digest: 8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7
parameters: 2,031,739,904 (cap 3,000,000,000) OK
quantization: Q4_K_M
exit 0
```

RUL-03
```
$ rg -n "/api/(chat|generate)|AutoModelFor(Causal|Seq2Seq)LM|\.generate\(|text-generation" municipal-qa/src
municipal-qa/src/municipal_qa/llm/client.py:104:        data = self._post("/api/chat", payload)
```

RUL-05
```
$ rg -n "judge" municipal-qa/src/municipal_qa/pipeline municipal-qa/src/municipal_qa/llm municipal-qa/src/municipal_qa/cli.py
(no output)
ls: municipal-qa/src/municipal_qa/tools: No such file or directory
```

RUL-06
```
tests/test_budget.py::test_defaults_are_competition_caps PASSED          [ 14%]
tests/test_budget.py::test_15th_call_allowed_16th_refused PASSED         [ 28%]
tests/test_budget.py::test_12000_tokens_allowed_12001_refused PASSED     [ 42%]
tests/test_budget.py::test_can_call_uses_worst_case_not_expected_tokens PASSED [ 57%]
tests/test_budget.py::test_record_raises_when_tokens_exceeded PASSED     [ 71%]
tests/test_budget.py::test_record_raises_on_16th_call PASSED             [ 85%]
tests/test_budget.py::test_records_are_call_records_and_a_copy PASSED    [100%]
```

RUL-07 (finding 1), read-only probe run from stdin with a mock transport
```
500: 20 sent /api/chat requests -> budget.calls_used=0 tokens_used=0 can_call(700)=True last_error=LLMError
timeout: 20 sent /api/chat requests -> budget.calls_used=0 tokens_used=0 can_call(700)=True last_error=ReadTimeout
```

RUL-10 (hard-coding scan; covered `src`, `configs` and `tests`; `llm/prompts/` does not exist yet)
```
needles=1355 files=25 hits=0
```

RUL-11
```
$ rg -n "Trainer|peft|lora|fine_?tune|\.fit\(|partial_fit|pseudo_?label|distil" municipal-qa/src
exit 1 (no hits)
```

RUL-12
```
No module named 'municipal_qa.evaluation'   -> not verified (T-106, M1)
```

RUL-13
```
$ rg -n "^\s*(import|from)\s+(requests|httpx|urllib|aiohttp|socket|http\.client)\b" municipal-qa/src
municipal-qa/src/municipal_qa/llm/client.py:8:import httpx
```

RUL-15
```
exit 1 (no hits)
```

RUL-16
```
$ head -3 LICENSE
Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/
```

RUL-17 (filtered for GPL/MPL/EPL/CDDL/UNKNOWN/Proprietary/EUPL/SSPL/CC-BY-SA/Artistic; 148 lines total)
```
 Name                       Version     License
 fqdn                       1.6.0       MPL-2.0
 certifi                    2026.11.20  Mozilla Public License 2.0 (MPL 2.0)
```
Both are allowed by D-024 (user-approved). No `UNKNOWN` entries.

RUL-18
```
$ git ls-files municipal-qa | rg -i "\.env$|\.zip$|Test\.csv|Train\.csv|SourceManifest|artifacts/|/sources/"
exit 1 (no hits)
$ git ls-files | rg -i "^Data/|\.zip$"
exit 1 (no hits)
```

RUL-01/RUL-06/RUL-13 (findings 2 and 4), read-only probe
```
config accepts: max_params 8000000000 | budget 99 99999 | base_url http://203.0.113.9:11434
8B-class model with raised cap -> 7615616512 accepted
```

SUB-02
```
tests/test_submission.py::test_round_trip PASSED
tests/test_submission.py::test_byte_compatible_with_starter PASSED
tests/test_submission.py::test_writer_refuses_bad_records PASSED
tests/test_submission.py::test_validator_catches_malformation[bad header] PASSED
tests/test_submission.py::test_validator_catches_malformation[missing ID] PASSED
tests/test_submission.py::test_validator_catches_malformation[wrong order] PASSED
tests/test_submission.py::test_validator_catches_malformation[blank answer] PASSED
tests/test_submission.py::test_validator_catches_malformation[## inside answer] PASSED
tests/test_submission.py::test_validator_catches_malformation[no evidence heading] PASSED
tests/test_submission.py::test_validator_catches_malformation[malformed evidence] PASSED
tests/test_submission.py::test_validator_catches_malformation[CRLF line endings] PASSED
tests/test_submission.py::test_non_utf8_rejected PASSED
tests/test_submission.py::test_foreign_source_is_warning_unknown_is_error PASSED
tests/test_submission.py::test_placeholder_record_is_valid PASSED
tests/test_submission.py::test_placeholder_for_all_600_test_ids PASSED
15 passed in 0.48s
```

DoD 8 (placeholder, not a run)
```
$ cd municipal-qa && uv run mqa validate artifacts/placeholder/Submission.md
600 warning(s), e.g. 0044bdd2-0acc-4876-a094-6257c2879406: no evidence lines
valid: 600 Test IDs in order, 182,042 bytes
exit 0
```

COR-01 and finding 3
```
$ cd municipal-qa && uv run pytest tests/test_context.py -q
30 passed in 0.84s
probe: a) ha precision: 5 | general: 2
probe: b) ha: None | general: 3
```

TST-01
```
$ cd municipal-qa && uv run pytest -q
87 passed in 1.05s
```

REP-01, REP-02, REP-04
```
municipal-qa/.python-version
municipal-qa/pyproject.toml
municipal-qa/uv.lock
8:  options: {temperature: 0, seed: 42, num_ctx: 8192}
9:  think: false
REP-04: exit 1 (no absolute paths in src or configs)
```

LAY-01, LAY-07 (read-only AST scan)
```
LAY-07 blocks > 5 lines: none
LAY-01 schema AST == plan §5: True
```

LAY-02, LAY-04, LAY-08
```
LAY-02: exit 1 (no hits)
LAY-04: exit 1 (no hits)
LAY-08: rg in municipal-qa exit 1; git log (50 commits) exit 1; PR #1 body exit 1
```

BLD-01..BLD-04
```
/Users/Apple/Desktop/Lab/playground/GenAI-for-Good-Challenge/main/GenAI-for-Good-Challenge/municipal-qa/.venv
All checks passed!
25 files already formatted
│ setup-data │ profile │ index │ check-model │ run │ evaluate │ submit │ validate │ show   (all 9 listed)
.gitignore:230:municipal-qa/.venv/	municipal-qa/.venv
.gitignore:231:municipal-qa/artifacts/	municipal-qa/artifacts
.gitignore:232:municipal-qa/.env	municipal-qa/.env
.gitignore:227:/Data/	Data/sources
.gitignore:81:.ipynb_checkpoints	x/.ipynb_checkpoints/a
ls: municipal-qa/notebooks: No such file or directory
git config --get filter.nbstripout.clean -> exit 1
```

DoD 6
```
$ cd municipal-qa && uv run mqa setup-data
verified 188 sources (size and SHA256) in /Users/Apple/Desktop/Lab/playground/GenAI-for-Good-Challenge/Data/sources [0.6s]
exit 0
```

ACC-01, ACC-02
```
T-001 [x] T-002 [x] T-003 [x] T-004 [x] T-005 [x] T-006 [x] T-007 [x] T-008 [x] T-009 [x] T-010 [x] T-011 [x] T-012 [x] T-013 [x]
DoD1 [x] DoD2 [x] DoD3 [x] DoD4 [x] DoD5 [x] DoD6 [x] DoD7 [x] DoD8 [x] DoD9 [x] DoD10 [x] DoD11 [x] DoD12 [x]
B-001 resolved; B-002 resolved; B-003 open (blocks T-404, M4); B-004 resolved
```

## Dimension summary

| Dimension | Result | Notes |
|---|---|---|
| 1. Competition-rule compliance | FAIL | Blocker 1 (failed requests uncharged). Major 2 (caps configurable upward). Minor 4 (non-local `base_url` accepted). Pass: RUL-01, 03, 05, 06, 10 (1,355 needles, 25 files, 0 hits), 11, 13, 15, 16, 17 (MPL allowed by D-024), 18. Not verified: RUL-04, 08, 09, 12, 14 (not built / no run). |
| 2. Submission validity and evidence | PASS with minor | SUB-02, SUB-03 pass; placeholder validates. Minor 5. SUB-01/04/08 on a run not verified (no run); SUB-05..07 not built. |
| 3. Correctness and conventions | FAIL (major) | COR-01: Major 3. COR-08 passes (`dtype=str`, `keep_default_na=False` in `data/manifest.py:30`, `data/questions.py:26`). Others not built in M0. |
| 4. Reproducibility and licensing | PASS (M0 scope) | REP-01, 02, 04 pass. REP-03, 05, 06, 07 not verified (no run; README and model licences are M3/M4). Gaps rows not verified. |
| 5. Tests | PASS with major | 87 passed, 0 collected-zero; data-backed tests skip cleanly without data. TST-04 weakness folded into Major 3. TST-05 not built. |
| 6. Layering and DRY | PASS | LAY-01 AST identical; LAY-02, 04, 07, 08 pass. LAY-03, 06 not built. LAY-05: rule-cap literals relate to Major 2. |
| 7. Build and lint | PASS with minor | BLD-01..04 pass; Minor 6 (notebook acceptance of card M1, `nbstripout` filter). |
| 8. Milestone acceptance | PASS | All 13 M0 tasks `[x]`, all 12 DoD rows `[x]` with Change log proof; read-only DoD proofs re-run (6, 8, 9) pass. ACC-03..05 not applicable to M0. |

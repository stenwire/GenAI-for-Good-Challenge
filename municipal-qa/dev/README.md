# Dev set

`dev_questions.csv` holds 62 dev questions written for this project from the 22 **Train**
sources only (`Source_Set == "Train"` in `SourceManifest.csv`). No Test question, Test source
or Test answer is used, and no challenge file is copied here: the Train questions themselves
stay in `Train.csv` under `MQA_DATA_DIR`.

Columns are those of `Train.csv` plus:

- `elements`: JSON list of the answer elements the scorer checks, each `number` (`value`,
  `tol`, optional `unit`), `date` (ISO `value`), `yes_no` (`value` yes/no) or `text`
  (`text`, optional `keywords`).
- `qtype`: the question type the row exercises.

`Evidence` uses the submission format, `SRC_<id> | locator | claim`.

## Regenerating

```bash
cd municipal-qa
uv run python dev/build_devset.py              # rewrites dev_questions.csv (deterministic)
uv run python -m municipal_qa.evaluation.devset_check
```

Every answer is computed in `build_devset.py` from named cells, rows or features, with
`decimal.Decimal` and half-up rounding, following each case's Context. Workbook cells are read
by the project's reader; on 2026-11-25 all 61 cited cells were cross-checked against an
independent parser (python-calamine) with no mismatch, and the zone-area method reproduces the
Train answer for `06ffb5b9` (3.48 ha). CSV and geometry answers use pandas and geopandas
directly.

## Rules

- Only Train sources. `devset_check.py` refuses any row whose `Source_IDs` include a Test or
  unknown source, or whose evidence cites a source outside the row's `Source_IDs`.
- Few-shot examples for prompts may only come from this file.
- Contexts are written in our own words, not copied from the challenge files.

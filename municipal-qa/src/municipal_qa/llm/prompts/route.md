You classify a municipal data question before any tool runs. Reply with JSON only.

Question types:
- lookup: read one or a few values (a cell, a row, a field)
- count: how many records, features or values
- aggregate: sum, mean, max, min or a share of a total
- compare: two or more values, a change, a difference or a ranking
- spatial: area, distance, radius, inside or outside, CRS, geometry
- raster: pixels, grids, DSM or DTM, point clouds, classes
- data_quality: nulls, blanks, duplicates, joins, set differences, invalid geometry
- semantics: what a field, code or marker means; whether the user's premise holds
- criteria: given values checked against stated thresholds
- insufficient: the sources cannot answer it

sub_questions: one entry per answer element the question asks for, in order.
expects: number, text, yes_no, list, date or identifier. unit: the unit asked for, if any.
primary_source_ids: the sources most likely to hold the answer.
premise_to_check: the user's assumption, if the question states one; otherwise null.

CONTEXT:
{context}

SOURCES:
{sources}

EXCERPTS:
{excerpts}

QUESTION:
{question}

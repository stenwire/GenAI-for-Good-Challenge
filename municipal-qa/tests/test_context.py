import pandas as pd
import pytest

from municipal_qa.pipeline.context import parse_conventions
from municipal_qa.schema import Conventions

# One case per row of the plan §6.3 rule table.
RULE_ROWS = [
    ("Use the numbers as they are visibly formatted in the workbook.", "value_mode", "displayed"),
    ("Report displayed values only.", "value_mode", "displayed"),
    ("Use the stored numeric cell values.", "value_mode", "stored"),
    ("Use the table at native precision.", "value_mode", "stored"),
    ("Round derived figures to three decimals at the end.", "decimals", 3),
    (
        "Round derived figures to two decimals, except hectares to five decimals.",
        "decimals_overrides",
        {"ha": 5},
    ),
    ("Midpoint rounding may use ties-to-even or half-up.", "rounding", "either"),
    ("Planar measures use EPSG:2056 metres.", "crs", "EPSG:2056"),
    ("Measure in British National Grid metres.", "crs", "EPSG:27700"),
    ("Radius boundaries included.", "radius_inclusive", True),
    ("Use unchanged source geometry.", "repair_geometry", False),
    ("Do not repair the polygon.", "repair_geometry", False),
    ("Counts mean records.", "counts_mean", "records"),
    ("Use literal timestamp labels without time shifting.", "time_shift", False),
    ("Question-defined rules are hypothetical.", "hypothetical_rules", True),
]


@pytest.mark.parametrize(("context", "field", "expected"), RULE_ROWS)
def test_rule_table_row(context, field, expected):
    assert getattr(parse_conventions(context), field) == expected


@pytest.mark.parametrize(
    ("phrase", "code"),
    [
        ("British National Grid", "EPSG:27700"),
        ("Swiss LV95", "EPSG:2056"),
        ("Lambert-93", "EPSG:2154"),
        ("RD New", "EPSG:28992"),
        ("ETRS-TM35FIN", "EPSG:3067"),
        ("Web Mercator", "EPSG:3857"),
        ("WGS 84", "EPSG:4326"),
    ],
)
def test_crs_aliases(phrase, code):
    assert parse_conventions(f"Coordinates use {phrase} metres.").crs == code


def test_projected_crs_beats_wgs84_when_both_named():
    c = parse_conventions("Projected coordinates use EPSG:3879; report WGS84 longitude, latitude.")
    assert c.crs == "EPSG:3879"


def test_unique_keys_only_when_question_asks():
    ctx = "Counts mean records unless unique keys are requested."
    assert parse_conventions(ctx).counts_mean == "records"
    assert parse_conventions(ctx, "How many records are there?").counts_mean == "records"
    assert (
        parse_conventions(ctx, "How many unique tree IDs are there?").counts_mean == "unique_keys"
    )


def test_question_overrides_context():
    ctx = "Round derived figures to two decimals."
    q = "Give the area in hectares to three decimal places, in British National Grid metres."
    c = parse_conventions(ctx, q)
    assert c.decimals_overrides["ha"] == 3
    assert c.crs == "EPSG:27700"
    assert c.raw_context == ctx


def test_step_precision_and_units():
    c = parse_conventions("Round areas to 0.1 km² and shares to 0.01 percentage points.")
    assert c.decimals_overrides["km²"] == 1
    assert c.decimals_overrides["pp"] == 2


def test_stored_wins_over_display_format_sentence():
    ctx = "Use the stored numeric cell values; the workbook's display format rounds them."
    assert parse_conventions(ctx).value_mode == "stored"


def test_no_signal_keeps_defaults():
    c = parse_conventions("Supplied snapshots of the register.")
    assert c.model_dump(exclude={"raw_context"}) == Conventions(raw_context="").model_dump(
        exclude={"raw_context"}
    )
    assert parse_conventions("").raw_context == ""


TRAIN_EXPECTED = {
    "06ffb5b9": {"crs": "EPSG:27700", "repair_geometry": False},
    "2e0fb2b9": {"value_mode": "displayed", "round_at_end": True},
    "0840316b": {"value_mode": "displayed"},
    "0b7af6de": {"value_mode": "stored", "round_at_end": True},
    "3d9a1704": {"value_mode": "stored"},
    "1d78cef6": {"value_mode": "unspecified", "repair_geometry": False},
}


def test_train_contexts(data_cfg):
    train = pd.read_csv(data_cfg.data_dir / "Train.csv")
    seen = set()
    for row in train.itertuples():
        c = parse_conventions(row.Context, row.Question)
        exp = TRAIN_EXPECTED.get(row.ID[:8])
        if exp:
            seen.add(row.ID[:8])
            for field, value in exp.items():
                assert getattr(c, field) == value, (row.ID, field)
    assert seen == set(TRAIN_EXPECTED)
    area_q = train.set_index(train.ID.str[:8]).loc["06ffb5b9"]
    assert parse_conventions(area_q.Context, area_q.Question).decimals_overrides["ha"] == 2
    pct = parse_conventions(train.set_index(train.ID.str[:8]).loc["2e0fb2b9", "Context"])
    assert pct.decimals_overrides == {"%": 2, "pp": 2}


def test_all_test_contexts_parse(data_cfg):
    test = pd.read_csv(data_cfg.data_dir / "Test.csv")
    contexts = test.Context.unique()
    assert len(contexts) == 58
    for ctx in contexts:
        assert isinstance(parse_conventions(ctx), Conventions)
    for row in test.itertuples():
        parse_conventions(row.Context, row.Question)


def test_question_precision_after_comma_overrides_context_unit():
    ctx = "Round derived figures to two decimals, except hectares to five decimals."
    c = parse_conventions(ctx, "Give the area in hectares, rounded to two decimal places.")
    assert c.decimals_overrides["ha"] == 2
    assert parse_conventions(ctx).decimals_overrides["ha"] == 5


def test_other_clause_does_not_capture_earlier_unit():
    c = parse_conventions(
        "Report hectares to five decimals; other derived figures to two decimals."
    )
    assert c.decimals_overrides == {"ha": 5}
    assert c.decimals == 2


def test_unitless_rounding_phrase_sets_general_precision():
    c = parse_conventions("", "What is it, to four decimal places?")
    assert c.decimals == 4
    assert c.decimals_overrides == {}

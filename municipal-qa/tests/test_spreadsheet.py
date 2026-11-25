from decimal import Decimal

import pytest

from municipal_qa.data.manifest import load_manifest
from municipal_qa.readers.base import UnsupportedFormat, get_reader
from municipal_qa.readers.spreadsheet import (
    SpreadsheetReader,
    cell_value,
    detect_header_row,
    get_cell,
    read_cells,
    read_workbook,
    render,
    sheet_frame,
)
from municipal_qa.schema import SourceInfo

MARKERS = ["[z]", "[x]", "[c]", "..", "-"]


def _src(path, sid="SRC_aaaaaaaaaaaa"):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=path.name,
        source_set="Train",
        publisher="",
        licence="",
        bytes=path.stat().st_size,
        sha256="",
        suffix=path.suffix,
    )


# ---------- number formats ----------


@pytest.mark.parametrize(
    ("value", "fmt", "shown"),
    [
        (1234.5678, "#,##0", "1,235"),
        (26307.67411959209, "#,##0", "26,308"),
        (2.345, "0.00", "2.35"),
        (2.355, "0.00", "2.36"),
        (-1234.5, "#,##0", "-1,235"),
        (0.12345, "0.00%", "12.35%"),
        (0.5, "0%", "50%"),
        (7, "0", "7"),
        (3.25, "0.0", "3.3"),
        (2019.0, "General", "2019"),
        ("[z]", "General", "[z]"),
        (True, "General", "TRUE"),
    ],
)
def test_render_known_formats_round_half_up(value, fmt, shown):
    assert render(value, fmt) == (shown, True)


def test_render_unknown_format_falls_back_and_flags():
    assert render(9.87, "0.000E+00") == ("9.87", False)


def test_render_date_with_locale_prefix():
    import datetime as dt

    assert render(dt.datetime(2024, 3, 5), "[$-409]d mmm yyyy") == ("5 Mar 2024", True)


# ---------- xlsx ----------


def test_xlsx_stored_and_displayed(xlsx_path):
    c = get_cell(xlsx_path, "Results", "B4")
    assert c.stored == 1234.5678 and c.displayed == "1,235" and c.number_format == "#,##0"
    assert (c.row, c.col, c.ref) == (4, 2, "B4")
    big = get_cell(xlsx_path, "Results", "B5")
    assert big.stored == 26307.67411959209 and big.displayed == "26,308"
    assert cell_value(big, "stored") == 26307.67411959209
    assert cell_value(big, "displayed") == "26,308"
    assert cell_value(big, "unspecified") == 26307.67411959209
    pct = get_cell(xlsx_path, "Results", "C4")
    assert (pct.stored, pct.displayed) == (0.12345, "12.35%")
    assert get_cell(xlsx_path, "Results", "D4").displayed == "2.35"
    date = get_cell(xlsx_path, "Results", "F4")
    assert (date.stored, date.displayed) == ("2024-03-05T00:00:00", "03-05-24")


def test_xlsx_markers_stay_strings(xlsx_path):
    for ref, marker in [("E4", "[z]"), ("E5", "..")]:
        c = get_cell(xlsx_path, "Results", ref)
        assert c.stored == marker and c.displayed == marker
        assert isinstance(c.stored, str)
    df = sheet_frame(xlsx_path, "Results")
    assert df["Marker"].tolist()[:2] == ["[z]", ".."]


def test_xlsx_merged_cell_keeps_position(xlsx_path):
    cells = {c.ref: c for c in read_cells(xlsx_path)["Results"]}
    assert cells["A6"].stored == "Merged note"
    assert "B6" not in cells and "C6" not in cells
    assert get_cell(xlsx_path, "Results", "B6").stored is None


def test_xlsx_unknown_format_warned(xlsx_path):
    wb = read_workbook(xlsx_path)
    assert any("not rendered" in w for w in wb.warnings)
    assert get_cell(xlsx_path, "Results", "G4").displayed == "9.87"
    assert get_cell(xlsx_path, "Results", "H4").displayed == "5 Mar 2024"


def test_xlsx_header_and_frame(xlsx_path):
    assert detect_header_row(read_cells(xlsx_path)["Results"]) == 3
    df = sheet_frame(xlsx_path, "Results", value_mode="displayed")
    assert list(df.columns)[:3] == ["City", "Total", "Share"]
    assert df.loc[4, "Total"] == "1,235"
    assert df.index.name == "sheet_row"


# ---------- ods ----------


def test_ods_stored_and_displayed(ods_path):
    c = get_cell(ods_path, "Data", "B3")
    assert (c.stored, c.displayed) == (1234.5678, "1,235")
    year = get_cell(ods_path, "Data", "A3")
    assert year.stored == 2019 and isinstance(year.stored, int)
    pct = get_cell(ods_path, "Data", "C3")
    assert (pct.stored, pct.displayed) == (0.12345, "12.3%")


def test_ods_text_spaces_and_paragraphs(ods_path):
    assert get_cell(ods_path, "Data", "D3").displayed == "North  East\nline two"


def test_ods_markers_stay_strings(ods_path):
    z = get_cell(ods_path, "Data", "B4")
    dots = get_cell(ods_path, "Data", "D4")
    assert (z.stored, z.displayed) == ("[z]", "[z]")
    assert (dots.stored, dots.displayed) == ("..", "..")


def test_ods_covered_cell_keeps_position(ods_path):
    assert get_cell(ods_path, "Data", "C4").stored is None
    assert get_cell(ods_path, "Data", "D4").col == 4


def test_ods_repeats_expanded_and_trailing_empties_capped(ods_path):
    for ref in ["B5", "C5", "D5"]:
        assert get_cell(ods_path, "Data", ref).stored == 7
    cells = read_cells(ods_path)["Data"]
    assert max(c.row for c in cells) == 5
    assert max(c.col for c in cells) == 4
    assert len(cells) < 30


def test_ods_header_frame_and_notes(ods_path):
    assert detect_header_row(read_cells(ods_path)["Data"]) == 2
    df = sheet_frame(ods_path, "Data")
    assert list(df.columns) == ["Year", "Trips", "Share", "Region"]
    assert df.loc[4, "Trips"] == "[z]"
    ps = SpreadsheetReader().passages(_src(ods_path))
    by_loc = {p.locator.text: p for p in ps}
    note = by_loc["sheet Notes, note 1"]
    assert note.kind == "notes_sheet" and "[z] means not applicable" in note.text
    assert by_loc["sheet Data, row 1"].kind == "header"
    assert "[z] = not applicable" in by_loc["sheet Data, row 1"].text
    assert by_loc["sheet Data, row 2"].text.startswith("Sheet Data columns: Year")
    assert all(p.passage_id.startswith("SRC_aaaaaaaaaaaa:") for p in ps)
    assert all(p.locator.source_id == "SRC_aaaaaaaaaaaa" for p in ps)


def test_reader_registry_and_profile(ods_path, xlsx_path):
    src = _src(ods_path)
    reader = get_reader(src)
    assert reader.name == "spreadsheet"
    prof = reader.profile(src)
    assert prof.kind == "workbook"
    data = next(t for t in prof.tables if t.table_name == "Data")
    assert (data.header_row, data.n_rows) == (2, 3)
    trips = next(c for c in data.columns if c.name == "Trips")
    assert trips.dtype == "mixed" and "[z]" in trips.examples
    assert len(prof.summary) <= 1200
    with pytest.raises(UnsupportedFormat):
        get_reader(_src(ods_path).model_copy(update={"suffix": ".xyz"}))


def test_files_are_not_modified(ods_path, xlsx_path):
    before = {p: p.read_bytes() for p in (ods_path, xlsx_path)}
    for p in before:
        SpreadsheetReader().passages(_src(p))
        SpreadsheetReader().profile(_src(p))
    assert {p: p.read_bytes() for p in before} == before


# ---------- real Train files (skip without MQA_DATA_DIR) ----------

BUS01 = "SRC_f9068c4ee468"
BUS04II = "SRC_8512b6d57ce4"
HSY = "SRC_d0ba40e73d2c"


def test_train_bus01_cells_reproduce_2e0fb2b9(data_cfg):
    path = load_manifest(data_cfg)[BUS01].path
    cells = {ref: get_cell(path, "BUS01a", ref) for ref in ["H23", "T23", "H29", "T29"]}
    assert {r: c.displayed for r, c in cells.items()} == {
        "H23": "2,198",
        "T23": "2,113",
        "H29": "1,821",
        "T29": "1,851",
    }
    assert all(isinstance(c.stored, float) for c in cells.values())
    assert cells["H23"].stored == pytest.approx(2197.786121)

    def pct(a: str, b: str) -> Decimal:
        num = Decimal(cells[a].displayed.replace(",", ""))
        den = Decimal(cells[b].displayed.replace(",", ""))
        return (num / den * 100).quantize(Decimal("0.01"))

    # Train answer: London 82.85%, England outside London 87.60% (visibly formatted values).
    assert (pct("H29", "H23"), pct("T29", "T23")) == (Decimal("82.85"), Decimal("87.60"))


def test_train_hsy_aa9_stored_vs_displayed(data_cfg):
    path = load_manifest(data_cfg)[HSY].path
    c = get_cell(path, "Tulokset", "AA9")
    assert cell_value(c, "stored") == 26307.67411959209
    assert cell_value(c, "displayed") == "26,308"
    share = Decimal(repr(get_cell(path, "Tulokset", "AA24").stored)) / Decimal(
        repr(get_cell(path, "Tulokset", "AA22").stored)
    )
    # Train 3d9a1704: Helsinki residential share 45.45% from stored values.
    assert (share * 100).quantize(Decimal("0.01")) == Decimal("45.45")


def test_train_bus04ii_note_and_shorthand_passages(data_cfg):
    src = load_manifest(data_cfg)[BUS04II]
    by_loc = {p.locator.text: p for p in get_reader(src).passages(src)}
    assert "cannot be split" in by_loc["sheet Notes, note 4"].text
    assert "[z] = not applicable" in by_loc["sheet BUS04aii, row 3"].text
    assert get_cell(src.path, "BUS04aii", "C26").stored == "[z]"


def test_all_spreadsheet_sources_read(data_cfg):
    manifest = load_manifest(data_cfg)
    sheets = [s for s in manifest.values() if s.suffix in (".ods", ".xlsx")]
    assert len(sheets) == 22
    for src in sheets:
        reader = get_reader(src)
        prof = reader.profile(src)
        assert prof.kind == "workbook" and prof.summary
        assert reader.passages(src) or prof.tables
    ub = next(s for s in sheets if s.original_file == "Lisa-1-UBN-hoonete-andmesisu-nouded.xlsx")
    assert any(w.startswith("openpyxl:") for w in get_reader(ub).profile(ub).warnings)

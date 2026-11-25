import pytest

from municipal_qa.config import Config, ConfigError, load_config


@pytest.fixture(scope="session")
def data_cfg() -> Config:
    """Real challenge data via MQA_DATA_DIR; tests using it skip on a machine without data."""
    try:
        cfg = load_config()
    except ConfigError as e:
        pytest.skip(str(e))
    if not (cfg.data_dir / "Test.csv").exists():
        pytest.skip(f"no Test.csv under {cfg.data_dir}")
    return cfg


ODS_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
)


def _s(text: str) -> str:
    return (
        f'<table:table-cell office:value-type="string"><text:p>{text}</text:p></table:table-cell>'
    )


def _f(value: str, shown: str, kind: str = "float", rep: int = 1) -> str:
    return (
        f'<table:table-cell office:value-type="{kind}" office:value="{value}" '
        f'table:number-columns-repeated="{rep}"><text:p>{shown}</text:p></table:table-cell>'
    )


def _row(*cells: str, rep: int = 1) -> str:
    return f'<table:table-row table:number-rows-repeated="{rep}">{"".join(cells)}</table:table-row>'


def _empty(rep: int) -> str:
    return f'<table:table-cell table:number-columns-repeated="{rep}"/>'


_REGION = (
    '<table:table-cell office:value-type="string"><text:p>North<text:s text:c="2"/>East'
    "</text:p><text:p>line two</text:p></table:table-cell>"
)
_NOTES = "".join(
    [
        _row(_s("Notes table")),
        _row(_s("Note number"), _s("Note text")),
        _row(_f("1", "1"), _s("[z] means not applicable.")),
    ]
)
_DATA = "".join(
    [
        _row(_s("Shorthand: [z] = not applicable")),
        _row(_s("Year"), _s("Trips"), _s("Share"), _s("Region")),
        _row(
            _f("2019", "2019"),
            _f("1234.5678", "1,235"),
            _f("0.12345", "12.3%", "percentage"),
            _REGION,
            _empty(16380),
        ),
        _row(_f("2020", "2020"), _s("[z]"), "<table:covered-table-cell/>", _s("..")),
        _row(_f("2021", "2021"), _f("7", "7", rep=3)),
        _row(_empty(16384), rep=1048570),
    ]
)
ODS_CONTENT = (
    f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ODS_NS}>'
    "<office:body><office:spreadsheet>"
    f'<table:table table:name="Notes">{_NOTES}</table:table>'
    f'<table:table table:name="Data">{_DATA}</table:table>'
    "</office:spreadsheet></office:body></office:document-content>"
)


@pytest.fixture(scope="session")
def ods_path(tmp_path_factory):
    import zipfile

    path = tmp_path_factory.mktemp("ods") / "sample.ods"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        zf.writestr("content.xml", ODS_CONTENT)
    return path


@pytest.fixture(scope="session")
def xlsx_path(tmp_path_factory):
    import datetime as dt

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Results"
    ws["A1"] = "Energy use (GWh)"
    for col, name in enumerate(["City", "Total", "Share", "Ratio", "Marker", "Date"], 1):
        ws.cell(row=3, column=col, value=name)
    rows = [
        ("Helsinki", 1234.5678, 0.12345, 2.345, "[z]", dt.datetime(2024, 3, 5)),
        ("Espoo", 26307.67411959209, 0.5, 2.355, "..", dt.datetime(2025, 12, 31)),
    ]
    fmts = ["General", "#,##0", "0.00%", "0.00", "General", "mm-dd-yy"]
    for r, values in enumerate(rows, 4):
        for c, (v, f) in enumerate(zip(values, fmts, strict=True), 1):
            ws.cell(row=r, column=c, value=v).number_format = f
    ws["G4"] = 9.87
    ws["G4"].number_format = "0.000E+00"
    ws["H4"] = dt.datetime(2024, 3, 5)
    ws["H4"].number_format = "[$-409]d mmm yyyy"
    ws.merge_cells("A6:C6")
    ws["A6"] = "Merged note"
    notes = wb.create_sheet("Notes")
    notes["A1"], notes["B1"] = "Note number", "Note text"
    notes["A2"], notes["B2"] = 1, "Figures are provisional."
    path = tmp_path_factory.mktemp("xlsx") / "sample.xlsx"
    wb.save(path)
    return path

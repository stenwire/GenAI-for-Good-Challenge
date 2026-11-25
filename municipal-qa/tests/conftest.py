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


def make_pdf(path, pages: list[str]) -> None:
    """Minimal valid PDF, one Helvetica text line per page (no PDF library needed)."""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream")
        content_no = len(objs)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_no} 0 R >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


@pytest.fixture(scope="session")
def docs_dir(tmp_path_factory):
    import docx

    d = tmp_path_factory.mktemp("docs")
    make_pdf(
        d / "report.pdf",
        [
            "Results: share was 12.5 percent [z].",
            "Methods are described on page two of the report.",
            "7",
        ],
    )
    (d / "SourceGuide.md").write_text(
        "# Source guide\n\nUse the frozen files.\nDo not refresh.\n\n"
        "## Analysis conventions\n\nCounts mean records.\n\n[z] means not applicable.\n"
    )
    (d / "Readme.txt").write_text(
        "==========\nBike data\n==========\n\nIntro line one.\nIntro line two.\n\n"
        "Field notes\n-----------\n\ncnt: count of rentals\n"
    )
    (d / "page.html").write_text(
        "<html><head><script>var x=1;</script><style>p{}</style></head><body>"
        "<div id='global-cookie-message'><p>We use cookies</p></div><nav><p>Menu</p></nav>"
        "<h2>Background</h2><p>Urban centres were mapped.</p>"
        "<h2>Results</h2><p>Travel time fell.</p><table><tr><th>Zone</th><th>Minutes</th></tr>"
        "<tr><td>A1</td><td>12</td></tr></table><footer><p>Footer text</p></footer></body></html>"
    )
    doc = docx.Document()
    doc.add_heading("Historical data", level=1)
    doc.add_paragraph("Specifications changed in 2005.")
    doc.add_paragraph("Severity is adjusted.")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Year", "Spec"
    t.cell(1, 0).text, t.cell(1, 1).text = "2005", "STATS19"
    doc.save(d / "guide.docx")
    return d

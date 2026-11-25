"""Publisher markers stay strings in every reader (M1 DoD 3); they are never 0 or NaN."""

import json
import sqlite3
import zipfile

import openpyxl
import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.readers.archive import ArchiveReader
from municipal_qa.readers.base import get_reader
from municipal_qa.schema import SourceInfo

MARKERS = ["[z]", "[x]", "[c]", "..", "-"]


def _src(path):
    return SourceInfo(
        source_id="SRC_eeeeeeeeeeee",
        path=str(path),
        original_file=path.name,
        source_set="Test",
        publisher="",
        licence="",
        bytes=path.stat().st_size,
        sha256="",
        suffix=path.suffix.lower(),
    )


def _xlsx(d):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Region", "Value"])
    for i, m in enumerate(MARKERS):
        ws.append([f"R{i}", m])
    ws.append(["Rn", 12.5])
    p = d / "t.xlsx"
    wb.save(p)
    return p


def _csv(d):
    p = d / "t.csv"
    p.write_text(
        "Region,Value\n" + "".join(f"R{i},{m}\n" for i, m in enumerate(MARKERS)) + "Rn,12.5\n"
    )
    return p


def _json(d):
    p = d / "t.json"
    rows = [{"Region": f"R{i}", "Value": m} for i, m in enumerate(MARKERS)]
    p.write_text(json.dumps(rows + [{"Region": "Rn", "Value": 12.5}]))
    return p


def _sqlite(d):
    p = d / "t.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE t (Region TEXT, Value)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(f"R{i}", m) for i, m in enumerate(MARKERS)])
    con.execute("INSERT INTO t VALUES ('Rn', 12.5)")
    con.commit()
    con.close()
    return p


@pytest.mark.parametrize(
    "make", [_xlsx, _csv, _json, _sqlite], ids=["xlsx", "csv", "json", "sqlite"]
)
def test_markers_stay_strings_in_tables(tmp_path, make):
    src = _src(make(tmp_path))
    df = get_reader(src).load_table(src)
    values = df["Value"].tolist()[: len(MARKERS)]
    assert values == MARKERS
    assert all(isinstance(v, str) for v in values)


def test_markers_stay_strings_in_ods(ods_path):
    src = _src(ods_path)
    df = get_reader(src).load_table(src, "Data")
    assert df.loc[4, "Trips"] == "[z]" and df.loc[4, "Region"] == ".."


def test_markers_stay_strings_in_zip_members(tmp_path):
    z = tmp_path / "t.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(_csv(tmp_path), "t.csv")
    df = ArchiveReader(root=tmp_path / "x").load_table(_src(z), "t.csv")
    assert df["Value"].tolist()[: len(MARKERS)] == MARKERS


def test_markers_stay_in_document_passages(tmp_path):
    p = tmp_path / "Notes.md"
    p.write_text("# Shorthand\n\n" + "; ".join(f"{m} = code" for m in MARKERS) + "\n")
    (ps,) = get_reader(_src(p)).passages(_src(p))
    assert all(m in ps.text for m in MARKERS)

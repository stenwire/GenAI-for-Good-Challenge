import sqlite3
import time
import zipfile

import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.data.manifest import load_manifest
from municipal_qa.readers import archive, delimited
from municipal_qa.readers.archive import ArchiveReader, ImageReader
from municipal_qa.readers.base import UnsupportedFormat, get_reader
from municipal_qa.readers.delimited import (
    DelimitedReader,
    TooLargeForPandas,
    read_frame,
    row_locator_text,
    sniff,
)
from municipal_qa.readers.sqlite import SqliteReader, connect_ro
from municipal_qa.schema import SourceInfo


def _src(path, sid="SRC_bbbbbbbbbbbb", name=None):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=name or path.name,
        source_set="Test",
        publisher="",
        licence="",
        bytes=path.stat().st_size,
        sha256="",
        suffix=path.suffix.lower(),
    )


@pytest.fixture
def bom_semicolon_csv(tmp_path):
    p = tmp_path / "records.csv"
    text = "﻿code;name;value;geom\n007;Nord;[z];POINT (1 2)\n010;Sud;12.5;POINT (3 4)\n011;;..;\n"
    p.write_bytes(text.encode("utf-8"))
    return p


def test_bom_and_semicolon_sniffed_values_stay_strings(bom_semicolon_csv):
    d = sniff(bom_semicolon_csv)
    assert (d.delimiter, d.encoding, d.tabular) == (";", "utf-8", True)
    assert any("byte-order mark" in w for w in d.warnings)
    df = read_frame(bom_semicolon_csv)
    assert list(df.columns) == ["code", "name", "value", "geom"]
    assert df.loc[1, "code"] == "007"
    assert df.loc[1, "value"] == "[z]" and df.loc[3, "value"] == ".."
    assert df.loc[3, "name"] == ""
    assert df.index.name == "data_row" and df.index[0] == 1


def test_profile_types_nulls_and_wkt(bom_semicolon_csv):
    prof = DelimitedReader().profile(_src(bom_semicolon_csv))
    t = prof.tables[0]
    cols = {c.name: c for c in t.columns}
    assert cols["code"].dtype == "int" and cols["code"].examples[0] == "007"
    assert cols["value"].dtype == "str" and "[z]" in cols["value"].examples
    assert cols["name"].n_null == 1
    assert cols["geom"].dtype == "wkt" and t.geometry_type == "POINT"
    assert t.n_rows == 3 and t.header_row == 1
    assert any("WKT geometry" in w for w in prof.warnings)


def test_header_passage_and_row_locator(bom_semicolon_csv):
    ps = DelimitedReader().passages(_src(bom_semicolon_csv))
    assert ps[0].text == "records.csv columns: code, name, value, geom"
    assert ps[0].kind == "header" and ps[0].locator.text == "header row"
    assert row_locator_text(412, "Height") == "data row 412, column Height"


def test_double_bom_and_cp1252(tmp_path):
    p = tmp_path / "hours.csv"
    p.write_bytes("﻿﻿UIC;Gare\n1;Lyon\n".encode())
    assert list(read_frame(p).columns) == ["UIC", "Gare"]
    assert any("2 byte-order mark" in w for w in sniff(p).warnings)
    q = tmp_path / "trees.csv"
    q.write_bytes("Essence,Arrond\nÉrable rouge,Outremont\n".encode("cp1252"))
    assert read_frame(q).loc[1, "Essence"] == "Érable rouge"
    assert sniff(q).encoding == "cp1252"


def test_txt_dispatch_table_vs_prose(tmp_path):
    table = tmp_path / "stops.txt"
    table.write_text("stop_id,stop_name,lat\n1,A,1.0\n2,B,2.0\n3,C,3.0\n")
    prose = tmp_path / "SourceGuide.txt"
    prose.write_text(
        "Frozen extract, version 3, published 2024.\nUse the guide, not a live feed.\n\n"
        "Counts mean records; see the notes, below, for exceptions.\n"
    )
    assert get_reader(_src(table)).name == "delimited"
    assert sniff(prose).tabular is False
    assert DelimitedReader().accepts(_src(prose)) is False
    assert DelimitedReader().accepts(_src(table)) is True


def test_large_file_goes_through_duckdb(bom_semicolon_csv, monkeypatch):
    small = DelimitedReader().profile(_src(bom_semicolon_csv)).tables[0]
    monkeypatch.setattr(delimited, "LARGE_BYTES", 10)
    with pytest.raises(TooLargeForPandas):
        read_frame(bom_semicolon_csv)
    prof = DelimitedReader().profile(_src(bom_semicolon_csv))
    big = prof.tables[0]
    assert big.n_rows == small.n_rows == 3
    assert [c.name for c in big.columns] == [c.name for c in small.columns]
    assert {c.name: c.n_null for c in big.columns} == {c.name: c.n_null for c in small.columns}
    assert big.geometry_type == "POINT"
    assert any("DuckDB" in w for w in prof.warnings)


def test_malformed_wkt_reported_not_repaired(tmp_path):
    p = tmp_path / "zones.csv"
    rows = ["ref,wkt", 'Z1,"524 393"""'] + [f"Z{i},POINT ({i} {i})" for i in range(2, 40)]
    p.write_text("\n".join(rows) + "\n")
    prof = DelimitedReader().profile(_src(p))
    assert prof.tables[0].geometry_type == "POINT"
    assert any("not valid WKT (first at data row 1)" in w for w in prof.warnings)
    assert read_frame(p).loc[1, "wkt"] == '524 393"'


# ---------- sqlite ----------


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "graph.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE nodes (id TEXT, label TEXT, height REAL)")
    con.executemany(
        "INSERT INTO nodes VALUES (?,?,?)",
        [("n1", "Building", 12.5), ("n2", "OsmRoad", None), ("007", "Building", 3.0)],
    )
    con.commit()
    con.close()
    return p


def test_sqlite_read_only_profile_and_passages(db_path):
    before = db_path.read_bytes()
    con = connect_ro(db_path)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO nodes VALUES ('x','y',1)")
    con.close()
    src = _src(db_path)
    prof = SqliteReader().profile(src)
    t = prof.tables[0]
    assert (t.table_name, t.n_rows, prof.kind) == ("nodes", 3, "database")
    height = next(c for c in t.columns if c.name == "height")
    assert height.n_null == 1 and height.dtype == "real"
    ps = SqliteReader().passages(src)
    assert ps[0].locator.text == "table nodes" and "id, label, height" in ps[0].text
    df = SqliteReader().load_table(src, "nodes")
    assert df.loc[df["id"] == "007", "rowid"].item() == 3
    assert db_path.read_bytes() == before


# ---------- archive and image ----------


@pytest.fixture
def gtfs_zip(tmp_path):
    p = tmp_path / "feed.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("stops.txt", "stop_id,stop_name\nS1,Terminal One\nS2,Nilai\n")
        zf.writestr("routes.txt", "route_id,route_short_name\nR1,A\n")
    return p


def test_archive_members_with_prefixed_locators(gtfs_zip, tmp_path):
    before = gtfs_zip.read_bytes()
    reader = ArchiveReader(root=tmp_path / "extracted")
    src = _src(gtfs_zip)
    ps = reader.passages(src)
    assert ps[0].text == "feed.zip contains: stops.txt, routes.txt"
    stops = next(p for p in ps if p.locator.text.startswith("member stops.txt"))
    assert stops.locator.text == "member stops.txt, header row"
    assert stops.text == "stops.txt columns: stop_id, stop_name"
    prof = reader.profile(src)
    assert {t.table_name for t in prof.tables} == {"stops.txt", "routes.txt"}
    assert reader.load_table(src, "stops.txt").loc[2, "stop_name"] == "Nilai"
    assert (tmp_path / "extracted" / src.source_id / "stops.txt").exists()
    assert gtfs_zip.read_bytes() == before


def test_archive_refuses_unsafe_members(tmp_path):
    p = tmp_path / "evil.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="unsafe"):
        archive.extract(_src(p), tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_image_metadata_only(tmp_path):
    from PIL import Image

    p = tmp_path / "map.jpg"
    Image.new("RGB", (40, 30), "white").save(p)
    src = _src(p)
    ps = ImageReader().passages(src)
    assert "width: 40" in ps[0].text and "height: 30" in ps[0].text
    assert ImageReader().profile(src).kind == "binary"
    with pytest.raises(UnsupportedFormat):
        ImageReader().load_table(src)


# ---------- real sources (skip without MQA_DATA_DIR) ----------


def _by_name(manifest, name):
    return next(s for s in manifest.values() if s.original_file == name)


def test_real_large_csv_profiles_fast(data_cfg):
    src = _by_name(load_manifest(data_cfg), "urban_building_stock_datasets_21022024.csv")
    assert src.bytes > 130_000_000
    t0 = time.perf_counter()
    prof = get_reader(src).profile(src)
    assert time.perf_counter() - t0 < 60
    assert prof.tables[0].n_rows == 812636 and len(prof.tables[0].columns) == 31


def test_real_formats_dispatch_and_quirks(data_cfg):
    m = load_manifest(data_cfg)
    trees = get_reader(_by_name(m, "tpo-trees.csv")).profile(_by_name(m, "tpo-trees.csv"))
    assert trees.tables[0].geometry_type == "POINT"
    hours = _by_name(m, "station-hours.csv")
    assert get_reader(hours).profile(hours).tables[0].columns[0].name == "UIC"
    outremont = _by_name(m, "outremont.csv")
    assert sniff(outremont.path).encoding == "cp1252"
    gtfs = [s for s in m.values() if s.original_file == "stops.txt"]
    assert gtfs and all(get_reader(s).name == "delimited" for s in gtfs)
    guides = [s for s in m.values() if s.original_file == "SourceGuide.txt"]
    assert guides and not any(sniff(s.path).tabular for s in guides)
    db = _by_name(m, "helsinki_graph.sqlite")
    assert get_reader(db).profile(db).tables
    zips = [s for s in m.values() if s.suffix == ".zip"]
    for z in zips:
        ps = get_reader(z).passages(z)
        assert any(p.locator.text.startswith("member ") for p in ps)

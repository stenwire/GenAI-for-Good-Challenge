"""CSV and tabular .txt (plan §7.3). Values stay strings; files over 50 MB go through DuckDB."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

from municipal_qa.readers.base import PassageBuilder, is_documentation_name, register
from municipal_qa.schema import ColumnProfile, Passage, SourceInfo, SourceProfile, TableProfile

LARGE_BYTES = 50 * 1024 * 1024
SNIFF_BYTES = 64 * 1024
DELIMS = ",;\t|"
BOM = "﻿"
WKT = re.compile(
    r"^\s*(MULTI)?(POINT|LINESTRING|POLYGON|GEOMETRYCOLLECTION)\s*(Z|M|ZM)?\s*\(", re.I
)
_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


class TooLargeForPandas(Exception): ...


@dataclass
class Dialect:
    encoding: str
    delimiter: str
    header: list[str]
    tabular: bool
    warnings: list[str] = field(default_factory=list)


def _decode(raw: bytes) -> tuple[str, str, list[str]]:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        notes = [] if enc == "utf-8" else [f"not valid UTF-8; decoded as {enc}"]
        return text, enc, notes
    raise AssertionError("latin-1 decodes any bytes")


def _clip_partial_line(raw: bytes, full: bool) -> bytes:
    if full:
        return raw
    cut = raw.rfind(b"\n")
    return raw[:cut] if cut > 0 else raw


@lru_cache(maxsize=256)
def _sniff(path_str: str) -> Dialect:
    path = Path(path_str)
    size = path.stat().st_size
    with open(path, "rb") as f:
        raw = _clip_partial_line(f.read(SNIFF_BYTES), size <= SNIFF_BYTES)
    text, enc, notes = _decode(raw)
    n_bom = len(text) - len(text.lstrip(BOM))
    if n_bom:
        notes.append(f"{n_bom} byte-order mark(s) stripped from the start")
        text = text.lstrip(BOM)
    lines = [ln for ln in text.splitlines() if ln.strip()][:40]
    if not lines:
        return Dialect(enc, ",", [], False, notes)
    counts = {d: [len(r) for r in csv.reader(lines, delimiter=d)] for d in DELIMS}
    # Best delimiter: most rows agreeing with the header's field count, then most fields.
    best = max(
        DELIMS,
        key=lambda d: (sum(c == counts[d][0] for c in counts[d][1:]), counts[d][0]),
    )
    header = next(csv.reader(lines[:1], delimiter=best))
    data = counts[best][1:]
    agree = sum(c == len(header) for c in data)
    tabular = len(header) >= 2 and (
        (data and agree >= max(1, 0.8 * len(data))) or (not data and len(header) >= 2)
    )
    tabular = tabular and all(len(h) <= 120 for h in header)
    if len(set(header)) != len(header):
        notes.append("duplicate column names in header; pandas suffixes repeats with .1, .2")
    return Dialect(enc, best, header, bool(tabular), notes)


def sniff(path: str | Path) -> Dialect:
    return _sniff(str(Path(path).resolve()))


def row_locator_text(n: int, column: str | None = None) -> str:
    return f"data row {n}" + (f", column {column}" if column else "")


def _is_large(path: str | Path) -> bool:
    return Path(path).stat().st_size > LARGE_BYTES


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def duck_source(path: str | Path) -> str:
    """A DuckDB table expression that reads the file with every column as text."""
    d = sniff(path)
    return (
        f"read_csv({_sql_str(str(path))}, header=true, all_varchar=true, "
        f"delim={_sql_str(d.delimiter)}, quote='\"', escape='\"')"
    )


@lru_cache(maxsize=1)
def _duck() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def read_frame(path: str | Path) -> pd.DataFrame:
    if _is_large(path):
        raise TooLargeForPandas(f"{Path(path).name} is over 50 MB; use duck_source()")
    d = sniff(path)
    raw = Path(path).read_bytes()
    text = _decode(raw)[0].lstrip(BOM)
    df = pd.read_csv(
        io.StringIO(text), sep=d.delimiter, dtype=str, keep_default_na=False, na_filter=False
    )
    df.index = pd.RangeIndex(1, len(df) + 1, name="data_row")
    return df


def _infer(values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if not present:
        return "empty"
    if sum(bool(WKT.match(v)) for v in present) >= 0.95 * len(present):
        return "wkt"
    for name, pat in (("int", _INT), ("float", _FLOAT), ("date", _DATE)):
        if all(pat.match(v) for v in present):
            return name
    if all(_INT.match(v) or _FLOAT.match(v) for v in present):
        return "float"
    return "str"


def _wkt_type(values: list[str]) -> str | None:
    for v in values:
        m = WKT.match(v)
        if m:
            return ((m.group(1) or "") + m.group(2)).upper()
    return None


def _bad_wkt(name: str, vals: list[str]) -> list[str]:
    bad = [i for i, v in enumerate(vals, 1) if v != "" and not WKT.match(v)]
    if not bad:
        return []
    return [f"column {name}: {len(bad)} value(s) are not valid WKT (first at data row {bad[0]})"]


def _profile_frame(df: pd.DataFrame) -> tuple[list[ColumnProfile], str | None, list[str]]:
    cols, geom, notes = [], None, []
    for name in df.columns:
        vals = df[name].tolist()
        distinct = list(dict.fromkeys(v for v in vals if v != ""))
        dtype = _infer(vals[:5000])
        if dtype == "wkt":
            geom = geom or _wkt_type(vals[:50])
            notes += _bad_wkt(str(name), vals)
        cols.append(
            ColumnProfile(
                name=str(name),
                dtype=dtype,
                n_null=sum(v == "" for v in vals),
                n_unique=len(distinct),
                examples=[v[:80] for v in distinct[:5]],
            )
        )
    return cols, geom, notes


def _profile_large(path: str | Path) -> tuple[int, list[ColumnProfile], str | None]:
    con, src = _duck(), duck_source(path)
    names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    q = lambda c: '"' + c.replace('"', '""') + '"'  # noqa: E731
    aggs = ", ".join(
        f"count(*) FILTER (WHERE {q(c)} IS NULL OR {q(c)} = ''), approx_count_distinct({q(c)})"
        for c in names
    )
    row = con.execute(f"SELECT count(*), {aggs} FROM {src}").fetchone()
    sample = con.execute(f"SELECT * FROM {src} LIMIT 5000").fetchall()
    cols, geom = [], None
    for i, name in enumerate(names):
        vals = ["" if r[i] is None else r[i] for r in sample]
        dtype = _infer(vals)
        if dtype == "wkt":
            geom = geom or _wkt_type(vals[:50])
        distinct = list(dict.fromkeys(v for v in vals if v != ""))
        cols.append(
            ColumnProfile(
                name=name,
                dtype=dtype,
                n_null=row[1 + 2 * i],
                n_unique=row[2 + 2 * i],
                examples=[v[:80] for v in distinct[:5]],
            )
        )
    return row[0], cols, geom


class DelimitedReader:
    name = "delimited"
    suffixes = (".csv", ".tsv", ".txt")

    def accepts(self, src: SourceInfo) -> bool:
        return src.suffix.lower() != ".txt" or sniff(src.path).tabular

    def profile(self, src: SourceInfo) -> SourceProfile:
        d = sniff(src.path)
        warnings = list(d.warnings)
        if _is_large(src.path):
            n_rows, cols, geom = _profile_large(src.path)
            warnings.append("over 50 MB: profiled with DuckDB; unique counts are approximate")
        else:
            df = read_frame(src.path)
            cols, geom, notes = _profile_frame(df)
            n_rows, warnings = len(df), warnings + notes
        if geom:
            warnings.append("WKT geometry column detected; CRS is not stated in the file")
        table = TableProfile(
            table_name=src.original_file,
            n_rows=n_rows,
            columns=cols,
            header_row=1,
            geometry_type=geom,
        )
        names = ", ".join(c.name for c in cols)
        summary = (
            f"{src.original_file}: table, {n_rows:,} data rows x {len(cols)} columns "
            f"(delimiter {d.delimiter!r}, {d.encoding}). Columns: {names}"
        )
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="table",
            tables=[table],
            summary=summary[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=warnings,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        header = sniff(src.path).header
        pb.add(
            f"{src.original_file} columns: " + ", ".join(h.lstrip(BOM) for h in header),
            "header",
            "column",
            "header row",
        )
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        return read_frame(src.path)


register(DelimitedReader())

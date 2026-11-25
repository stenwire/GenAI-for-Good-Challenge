"""Cell-level .xlsx / .ods reader keeping stored AND displayed values (plan §7.2)."""

from __future__ import annotations

import datetime as dt
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd
from lxml import etree
from pydantic import BaseModel

from municipal_qa.readers.base import PassageBuilder, is_documentation_name, register
from municipal_qa.schema import ColumnProfile, Passage, SourceInfo, SourceProfile, TableProfile

ValueMode = Literal["stored", "displayed", "unspecified"]

MAX_FILLED_REPEAT = 1000
DOC_SHEET = re.compile(
    r"note|cover|contents|readme|about|info|definition|metadata|guidance|glossary", re.I
)
_REF = re.compile(r"^([A-Z]+)(\d+)$")
_NUM_FMT = re.compile(r"^(#,##)?0(?:\.(0+))?$")
_DATE_TOKENS = [
    ("yyyy", "%Y"),
    ("yy", "%y"),
    ("mmmm", "%B"),
    ("mmm", "%b"),
    ("mm", "%m"),
    ("dd", "%d"),
    ("hh", "%H"),
    ("ss", "%S"),
]


class Cell(BaseModel):
    sheet: str
    ref: str
    row: int
    col: int
    stored: float | int | str | bool | None
    displayed: str | None
    number_format: str | None


@dataclass
class Workbook:
    sheets: dict[str, list[Cell]]
    warnings: list[str] = field(default_factory=list)


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_ref(ref: str) -> tuple[int, int]:
    m = _REF.match(ref.upper())
    if not m:
        raise ValueError(f"not an A1 reference: {ref!r}")
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


def _dec(value: float | int) -> Decimal:
    # repr gives the shortest round-trip digits, close to what a spreadsheet shows.
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def _general(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}" if isinstance(value, float) else str(value)


def _date_fmt(fmt: str) -> str | None:
    fmt = re.sub(r"\[[^\]]*\]", "", fmt)
    if not re.search(r"[ymd]", fmt, re.I) or re.search(r"[#0]", fmt):
        return None
    out = fmt.lower().replace("\\", "").replace('"', "")
    for token, code in _DATE_TOKENS:
        out = out.replace(token, code)
    out = re.sub(r"(?<!%)\bm\b", "%-m", out)
    out = re.sub(r"(?<!%)\bd\b", "%-d", out)
    return out


def render(value: object, fmt: str | None) -> tuple[str | None, bool]:
    """(displayed text, format understood) for an xlsx value under its number format."""
    if value is None:
        return None, True
    if isinstance(value, str):
        return value, True
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), True
    fmt = (fmt or "General").split(";")[0].strip()
    if isinstance(value, dt.datetime | dt.date):
        code = _date_fmt(fmt)
        if code:
            return value.strftime(code), True
        return value.isoformat(), False
    if fmt in ("General", "@", ""):
        return _general(value), True
    if fmt.endswith("%") and _NUM_FMT.match(fmt[:-1]):
        places = len(_NUM_FMT.match(fmt[:-1]).group(2) or "")
        q = (_dec(value) * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return f"{q:,.{places}f}%" if fmt.startswith("#,##") else f"{q:.{places}f}%", True
    m = _NUM_FMT.match(fmt)
    if m:
        places = len(m.group(2) or "")
        q = _dec(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return (f"{q:,.{places}f}" if m.group(1) else f"{q:.{places}f}"), True
    return _general(value), False


def _read_xlsx(path: Path) -> Workbook:
    import openpyxl

    big = path.stat().st_size > 30_000_000
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        wb = openpyxl.load_workbook(path, data_only=True, read_only=big)
    out = Workbook(sheets={}, warnings=sorted({f"openpyxl: {w.message}" for w in caught}))
    unknown: set[str] = set()
    for ws in wb.worksheets:
        cells: list[Cell] = []
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None:
                    continue
                shown, ok = render(v, c.number_format)
                if not ok:
                    unknown.add(c.number_format)
                if isinstance(v, dt.datetime | dt.date | dt.time):
                    v = v.isoformat()
                cells.append(
                    Cell(
                        sheet=ws.title,
                        ref=f"{col_letter(c.column)}{c.row}",
                        row=c.row,
                        col=c.column,
                        stored=v,
                        displayed=shown,
                        number_format=c.number_format,
                    )
                )
        out.sheets[ws.title] = cells
    if big:
        out.warnings.append("read-only mode: merged-cell ranges not resolved")
    for f in sorted(unknown):
        out.warnings.append(f"number format {f!r} not rendered; displayed uses the general form")
    wb.close()
    return out


def _ods_text(el: etree._Element, X: str) -> str:
    parts = [el.text or ""]
    for ch in el:
        tag = etree.QName(ch).localname if isinstance(ch.tag, str) else ""
        if tag == "s":
            parts.append(" " * int(ch.get(X + "c", "1")))
        elif tag == "tab":
            parts.append("\t")
        elif tag == "line-break":
            parts.append("\n")
        elif tag != "annotation":
            parts.append(_ods_text(ch, X))
        parts.append(ch.tail or "")
    return "".join(parts)


def _ods_number(s: str) -> int | float:
    return int(s) if re.fullmatch(r"-?\d+", s) else float(s)


def _read_ods(path: Path) -> Workbook:
    with zipfile.ZipFile(path) as zf:
        root = etree.fromstring(zf.read("content.xml"))
    ns = root.nsmap
    T, OFF, X = (f"{{{ns[k]}}}" for k in ("table", "office", "text"))
    out = Workbook(sheets={})
    for table in root.iter(T + "table"):
        name = table.get(T + "name")
        cells: list[Cell] = []
        r = 0
        for row in table.iter(T + "table-row"):
            rrep = int(row.get(T + "number-rows-repeated", "1"))
            filled: list[tuple[int, object, str | None]] = []
            c = 0
            for cell in row:
                if not isinstance(cell.tag, str):
                    continue
                crep = int(cell.get(T + "number-columns-repeated", "1"))
                vt = cell.get(OFF + "value-type")
                paras = cell.findall(X + "p")
                shown = "\n".join(_ods_text(p, X) for p in paras) if paras else None
                if vt in ("float", "percentage", "currency"):
                    stored: object = _ods_number(cell.get(OFF + "value"))
                elif vt == "date":
                    stored = cell.get(OFF + "date-value")
                elif vt == "time":
                    stored = cell.get(OFF + "time-value")
                elif vt == "boolean":
                    stored = cell.get(OFF + "boolean-value") == "true"
                else:
                    stored = cell.get(OFF + "string-value") or shown
                if stored not in (None, "") or shown:
                    if crep > MAX_FILLED_REPEAT:
                        out.warnings.append(
                            f"sheet {name}: a filled cell repeated {crep} times capped at "
                            f"{MAX_FILLED_REPEAT}"
                        )
                    for k in range(min(crep, MAX_FILLED_REPEAT)):
                        filled.append((c + k + 1, stored, shown))
                c += crep
            if filled:
                if rrep > MAX_FILLED_REPEAT:
                    out.warnings.append(
                        f"sheet {name}: a filled row repeated {rrep} times capped at "
                        f"{MAX_FILLED_REPEAT}"
                    )
                for k in range(min(rrep, MAX_FILLED_REPEAT)):
                    for col, stored, shown in filled:
                        cells.append(
                            Cell(
                                sheet=name,
                                ref=f"{col_letter(col)}{r + k + 1}",
                                row=r + k + 1,
                                col=col,
                                stored=stored,
                                displayed=shown,
                                number_format=None,
                            )
                        )
            r += rrep
        out.sheets[name] = cells
    return out


@lru_cache(maxsize=16)
def _workbook(path_str: str) -> Workbook:
    path = Path(path_str)
    if path.suffix.lower() == ".ods":
        return _read_ods(path)
    return _read_xlsx(path)


def read_workbook(path: str | Path) -> Workbook:
    return _workbook(str(Path(path).resolve()))


def read_cells(path: str | Path) -> dict[str, list[Cell]]:
    return read_workbook(path).sheets


def get_cell(path: str | Path, sheet: str, ref: str) -> Cell:
    row, col = parse_ref(ref)
    cells = read_cells(path)
    if sheet not in cells:
        raise KeyError(f"no sheet {sheet!r}; sheets: {list(cells)}")
    for c in cells[sheet]:
        if c.row == row and c.col == col:
            return c
    return Cell(
        sheet=sheet,
        ref=ref.upper(),
        row=row,
        col=col,
        stored=None,
        displayed=None,
        number_format=None,
    )


def cell_value(cell: Cell, value_mode: ValueMode = "unspecified") -> object:
    return cell.displayed if value_mode == "displayed" else cell.stored


def cell_locator_text(sheet: str, ref: str) -> str:
    return f"sheet {sheet}, cell {ref}"


def _rows(cells: list[Cell]) -> dict[int, list[Cell]]:
    rows: dict[int, list[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row, []).append(c)
    for r in rows.values():
        r.sort(key=lambda c: c.col)
    return dict(sorted(rows.items()))


def _is_number(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def detect_header_row(cells: list[Cell]) -> int | None:
    rows = _rows(cells)
    nums = list(rows)
    for i, r in enumerate(nums[:60]):
        row = rows[r]
        if len(row) < 2 or not all(isinstance(c.stored, str) for c in row):
            continue
        following = nums[i + 1 : i + 4]
        if any(any(_is_number(c.stored) for c in rows[n]) for n in following):
            return r
    for r in nums[:60]:
        if len(rows[r]) >= 2:
            return r
    return None


def sheet_frame(
    path: str | Path,
    sheet: str,
    header_row: int | None = None,
    value_mode: ValueMode = "unspecified",
) -> pd.DataFrame:
    cells = read_cells(path)[sheet]
    header_row = header_row or detect_header_row(cells)
    rows = _rows(cells)
    if header_row is None:
        return pd.DataFrame()
    names = {
        c.col: str(c.displayed if c.displayed is not None else c.stored).strip()
        for c in rows.get(header_row, [])
    }
    data_rows = [r for r in rows if r > header_row]
    cols = sorted({c.col for r in data_rows for c in rows[r]} | set(names))
    labels = [names.get(c) or f"column {col_letter(c)}" for c in cols]
    records = []
    for r in data_rows:
        by_col = {c.col: cell_value(c, value_mode) for c in rows[r]}
        records.append([by_col.get(c) for c in cols])
    df = pd.DataFrame.from_records(records, columns=labels)
    df.index = pd.Index(data_rows, name="sheet_row")
    return df


def _dtype(values: list[object]) -> str:
    kinds = {
        "bool"
        if isinstance(v, bool)
        else "int"
        if isinstance(v, int)
        else "float"
        if isinstance(v, float)
        else "str"
        for v in values
    }
    if kinds <= {"int"}:
        return "int"
    if kinds <= {"int", "float"}:
        return "float"
    return kinds.pop() if len(kinds) == 1 else "mixed"


def _column_profiles(df: pd.DataFrame) -> list[ColumnProfile]:
    out = []
    for i, name in enumerate(df.columns):
        vals = df.iloc[:, i].tolist()
        present = [v for v in vals if v is not None and v != ""]
        distinct = list(dict.fromkeys(str(v) for v in present))
        out.append(
            ColumnProfile(
                name=str(name),
                dtype=_dtype(present) if present else "empty",
                n_null=len(vals) - len(present),
                n_unique=len(distinct),
                examples=distinct[:5],
            )
        )
    return out


class SpreadsheetReader:
    name = "spreadsheet"
    suffixes = (".xlsx", ".xlsm", ".ods")

    def profile(self, src: SourceInfo) -> SourceProfile:
        wb = read_workbook(src.path)
        tables: list[TableProfile] = []
        lines = []
        for sheet, cells in wb.sheets.items():
            if DOC_SHEET.search(sheet) or not cells:
                lines.append(f"{sheet} (text)")
                continue
            header = detect_header_row(cells)
            df = sheet_frame(src.path, sheet, header)
            tables.append(
                TableProfile(
                    table_name=sheet,
                    n_rows=len(df),
                    columns=_column_profiles(df),
                    header_row=header,
                )
            )
            lines.append(f"{sheet} ({len(df)} rows x {df.shape[1]} cols, header row {header})")
        summary = f"{src.original_file}: workbook, {len(wb.sheets)} sheets: " + "; ".join(lines)
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="workbook",
            tables=tables,
            summary=summary[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=list(wb.warnings),
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        for sheet, cells in read_cells(src.path).items():
            rows = _rows(cells)
            if DOC_SHEET.search(sheet):
                self._doc_sheet(pb, sheet, rows)
                continue
            header = detect_header_row(cells)
            for r, row in rows.items():
                if header is not None and r > header:
                    break
                text = " | ".join(str(c.displayed or c.stored) for c in row)
                prefix = f"Sheet {sheet} columns: " if r == header else ""
                pb.add(prefix + text, "header", "row", f"sheet {sheet}, row {r}")
        return pb.items

    @staticmethod
    def _doc_sheet(pb: PassageBuilder, sheet: str, rows: dict[int, list[Cell]]) -> None:
        note_col = None
        for r, row in rows.items():
            texts = [str(c.displayed or c.stored) for c in row]
            low = [t.strip().lower() for t in texts]
            if note_col is None and len(row) >= 2 and low[0].startswith("note"):
                note_col = row[0].col
                continue
            first = row[0]
            if note_col is not None and first.col == note_col and len(row) >= 2:
                number = str(first.displayed or first.stored).strip()
                body = " | ".join(texts[1:])
                pb.add(
                    f"Note {number}: {body}", "notes_sheet", "row", f"sheet {sheet}, note {number}"
                )
            else:
                pb.add(" | ".join(texts), "notes_sheet", "row", f"sheet {sheet}, row {r}")

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        sheets = read_cells(src.path)
        if table is None:
            table = next((s for s in sheets if not DOC_SHEET.search(s) and sheets[s]), None)
            if table is None:
                raise KeyError(f"{src.source_id}: no data sheet")
        return sheet_frame(src.path, table)


register(SpreadsheetReader())

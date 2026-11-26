"""IFC as ISO 10303-21 STEP text, parsed directly (D-006: no ifcopenshell)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    is_documentation_name,
    register,
)
from municipal_qa.schema import ColumnProfile, Passage, SourceInfo, SourceProfile, TableProfile

_ENTITY = re.compile(
    r"#(\d+)\s*=\s*([A-Z][A-Z0-9_]*)\s*\((.*?)\)\s*;(?=\s*(?:#\d+\s*=|ENDSEC))", re.S
)
_TOKEN = re.compile(
    r"'(?:[^']|'')*'|#\d+|\.[A-Z0-9_]+\.|[A-Z][A-Z0-9_]*\s*\(|"
    r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?|\$|\*|\(|\)|,"
)
_X2 = re.compile(r"\\X2\\((?:[0-9A-F]{4})+)\\X0\\")
_X = re.compile(r"\\X\\([0-9A-F]{2})")
_S = re.compile(r"\\S\\(.)")
NAMES = {
    t.upper(): t
    for t in [
        "IfcProject",
        "IfcSite",
        "IfcBuilding",
        "IfcBuildingStorey",
        "IfcSpace",
        "IfcZone",
        "IfcWall",
        "IfcWallStandardCase",
        "IfcWindow",
        "IfcDoor",
        "IfcSlab",
        "IfcRoof",
        "IfcColumn",
        "IfcBeam",
        "IfcStair",
        "IfcStairFlight",
        "IfcRailing",
        "IfcCovering",
        "IfcCurtainWall",
        "IfcPlate",
        "IfcMember",
        "IfcOpeningElement",
        "IfcFurnishingElement",
        "IfcBuildingElementProxy",
        "IfcFlowTerminal",
        "IfcPropertySet",
        "IfcElementQuantity",
        "IfcPropertySingleValue",
        "IfcQuantityArea",
        "IfcQuantityLength",
        "IfcQuantityVolume",
        "IfcSIUnit",
    ]
}
PRODUCTS = ("IFCSPACE", "IFCBUILDINGSTOREY", "IFCBUILDING", "IFCSITE", "IFCZONE")


def type_name(t: str) -> str:
    return NAMES.get(t, "Ifc" + t[3:].title() if t.startswith("IFC") else t)


class Ref(int):
    """`#n` entity reference, distinct from a plain integer."""


def decode(s: str) -> str:
    s = s.replace("''", "'")
    s = _X2.sub(lambda m: bytes.fromhex(m.group(1)).decode("utf-16-be"), s)
    s = _X.sub(lambda m: bytes.fromhex(m.group(1)).decode("latin-1"), s)
    s = _S.sub(lambda m: chr(ord(m.group(1)) + 128), s)
    return s.replace("\\\\", "\\")


def parse_args(text: str) -> list[Any]:
    """STEP argument list → Python values; typed values become (TYPE, [args])."""
    stack: list[list] = [[]]
    typed: list[str | None] = [None]
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        c = tok[0]
        if c == "'":
            stack[-1].append(decode(tok[1:-1]))
        elif c == "#":
            stack[-1].append(Ref(tok[1:]))
        elif c == "." and tok[-1] == "." and len(tok) > 2 and tok[1].isalpha():
            stack[-1].append(tok[1:-1])
        elif c == "(" or tok.endswith("("):
            stack.append([])
            typed.append(tok[:-1].strip() if tok != "(" else None)
        elif c == ")":
            items, t = stack.pop(), typed.pop()
            stack[-1].append((t, items) if t else items)
        elif c == "$":
            stack[-1].append(None)
        elif c == "*":
            stack[-1].append("*")
        elif c != ",":
            stack[-1].append(float(tok) if any(x in tok for x in ".Ee") else int(tok))
    return stack[0]


def plain(v: Any) -> Any:
    """Unwrap a typed value such as IFCLABEL('x') or IFCAREAMEASURE(3.5)."""
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], list) and len(v[1]) == 1:
        return v[1][0]
    return v


class IfcModel:
    def __init__(self, text: str):
        head, _, data = text.partition("DATA;")
        m = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", head)
        self.schema = m.group(1) if m else None
        self.raw: dict[int, tuple[str, str]] = {}
        self.types: dict[str, list[int]] = defaultdict(list)
        for m in _ENTITY.finditer(data):
            i = int(m.group(1))
            self.raw[i] = (m.group(2), m.group(3))
            self.types[m.group(2)].append(i)
        self._parsed: dict[int, list[Any]] = {}
        self._props: dict[int, dict[str, Any]] | None = None

    def args(self, i: int) -> list[Any]:
        if i not in self._parsed:
            self._parsed[i] = parse_args(self.raw[i][1])
        return self._parsed[i]

    def type_of(self, i: int) -> str:
        return self.raw[i][0]

    def by_type(self, t: str) -> list[int]:
        return self.types.get(t.upper(), [])

    def counts(self) -> Counter:
        return Counter({t: len(v) for t, v in self.types.items()})

    def _definition(self, i: int) -> dict[str, Any]:
        t, a = self.type_of(i), self.args(i)
        out: dict[str, Any] = {}
        if t == "IFCPROPERTYSET":
            for p in a[4] or []:
                if self.type_of(p) == "IFCPROPERTYSINGLEVALUE":
                    pa = self.args(p)
                    out[f"{a[2]}.{pa[0]}"] = plain(pa[2])
        elif t == "IFCELEMENTQUANTITY":
            for q in a[5] or []:
                qa = self.args(q)
                if self.type_of(q).startswith("IFCQUANTITY") and len(qa) > 3:
                    out[f"{a[2]}.{qa[0]}"] = qa[3]
        return out

    def properties(self, i: int) -> dict[str, Any]:
        """Property-set values and quantities of entity `i`, keyed `Set.Name`."""
        if self._props is None:
            self._props = defaultdict(dict)
            for rel in self.by_type("IFCRELDEFINESBYPROPERTIES"):
                a = self.args(rel)
                if not isinstance(a[5], Ref) or a[5] not in self.raw:
                    continue
                values = self._definition(a[5])
                for obj in a[4] or []:
                    self._props[obj].update(values)
        return dict(self._props.get(i, {}))

    def entity_frame(self, t: str) -> pd.DataFrame:
        rows = []
        for i in self.by_type(t):
            a = self.args(i)
            row = {
                "step_id": f"#{i}",
                "GlobalId": a[0] if a else None,
                "Name": a[2] if len(a) > 2 else None,
                "Description": a[3] if len(a) > 3 else None,
                "ObjectType": a[4] if len(a) > 4 else None,
            }
            if t.upper() in PRODUCTS and len(a) > 7:
                row["LongName"] = a[7]
            if t.upper() == "IFCBUILDINGSTOREY" and len(a) > 9:
                row["Elevation"] = a[9]
            row.update(self.properties(i))
            rows.append(row)
        return pd.DataFrame(rows)

    def units(self) -> list[str]:
        out = []
        for i in self.by_type("IFCSIUNIT"):
            a = self.args(i)
            out.append(f"{a[1]}={(a[2] or '')}{a[3]}")
        return list(dict.fromkeys(out))


@lru_cache(maxsize=2)
def _model(path: str) -> IfcModel:
    return IfcModel(Path(path).read_text("latin-1"))


def load_model(path: str | Path) -> IfcModel:
    return _model(str(Path(path).resolve()))


def entity_locator(i: int, t: str, name: Any) -> str:
    return f"#{i} {type_name(t)} Name={name}"


def _table(name: str, df: pd.DataFrame) -> TableProfile:
    cols = []
    for c in df.columns:
        text = df[c].dropna().astype(str)
        cols.append(
            ColumnProfile(
                name=str(c),
                dtype=str(df[c].dtype),
                n_null=int(df[c].isna().sum()),
                n_unique=int(text.nunique()),
                examples=[v[:80] for v in text.drop_duplicates().head(5)],
            )
        )
    return TableProfile(table_name=name, n_rows=len(df), columns=cols)


class IfcReader:
    name = "ifc"
    suffixes = (".ifc",)

    def profile(self, src: SourceInfo) -> SourceProfile:
        m = load_model(src.path)
        tables = [_table(type_name(t), m.entity_frame(t)) for t in PRODUCTS if m.by_type(t)]
        top = ", ".join(f"{type_name(t)} {n:,}" for t, n in m.counts().most_common(15))
        storeys = m.entity_frame("IFCBUILDINGSTOREY")
        st = ""
        if "Elevation" in storeys:
            st = "; ".join(f"{r.Name} (elevation {r.Elevation})" for r in storeys.itertuples())
        summary = (
            f"{src.original_file}: IFC {m.schema}, {len(m.raw):,} entities; units "
            f"{', '.join(m.units())}; storeys: {st}; most common: {top}"
        )
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="bim",
            tables=tables,
            summary=summary[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=["IFC parsed as STEP text: attributes and property sets, no geometry kernel"],
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        m = load_model(src.path)
        pb = PassageBuilder(src.source_id)
        pb.add(self.profile(src).summary, "metadata", "ifc_entity", "file header and entity counts")
        for t in PRODUCTS:
            for i in m.by_type(t):
                a = m.args(i)
                props = m.properties(i)
                vals = "; ".join(f"{k}={v}" for k, v in props.items() if v not in (None, ""))
                long = f" {a[7]}" if t in PRODUCTS and len(a) > 7 and a[7] else ""
                pb.add(
                    f"{type_name(t)} {a[2]}{long}: {vals}"[:1200],
                    "body",
                    "ifc_entity",
                    entity_locator(i, t, a[2]),
                )
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        t = (table or "IfcSpace").upper()
        m = load_model(src.path)
        if not m.by_type(t):
            have = [type_name(x) for x in PRODUCTS if m.by_type(x)]
            raise UnsupportedFormat(f"{src.original_file}: no {table} entities; tables: {have}")
        return m.entity_frame(t)


register(IfcReader())

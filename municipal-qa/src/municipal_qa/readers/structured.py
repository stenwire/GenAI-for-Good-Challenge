"""JSON, XML and XSD → records, metadata passages and field definitions (plan §7.5)."""

from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from lxml import etree

from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    is_documentation_name,
    register,
)
from municipal_qa.schema import ColumnProfile, Passage, SourceInfo, SourceProfile, TableProfile

MAX_PASSAGES = 2000
RECORD_PASSAGE_LIMIT = 50
LONG_VALUE = 60
LARGE_XML_ELEMENTS = 20_000
XS = "{http://www.w3.org/2001/XMLSchema}"
DICT_KEYS = ("field", "fields", "schema", "columns", "attributes", "properties")


def pointer(parts: list[str | int]) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


@lru_cache(maxsize=16)
def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_json(path: str | Path) -> Any:
    return _load_json(str(Path(path).resolve()))


def is_geojson(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and data.get("type") == "FeatureCollection"
        and isinstance(data.get("features"), list)
    )


def _flatten(obj: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        elif isinstance(v, list):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = v
    return out


def records_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_records([_flatten(r) for r in records])
    df.index = pd.RangeIndex(0, len(df), name="json_index")
    return df


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, str | int | float | bool)


def _show(v: Any) -> str:
    return "null" if v is None else str(v)


def _walk(pb: PassageBuilder, obj: Any, parts: list, kind: str) -> None:
    """Short scalars grouped per object; long strings and nested objects get their own passage."""
    if len(pb.items) >= MAX_PASSAGES:
        return
    if isinstance(obj, dict):
        here = kind
        if (
            parts
            and str(parts[-1]).lower() in DICT_KEYS
            or (len(parts) >= 2 and str(parts[-2]).lower() in DICT_KEYS)
        ):
            here = "dictionary"
        short = []
        for k, v in obj.items():
            if _scalar(v) and len(_show(v)) <= LONG_VALUE:
                short.append(f"{k}: {_show(v)}")
            elif _scalar(v):
                pb.add(f"{k}: {v}", here, "json_pointer", pointer(parts + [k]))
            else:
                _walk(pb, v, parts + [k], here)
        if short:
            pb.add("\n".join(short), here, "json_pointer", pointer(parts) if parts else "/")
    elif isinstance(obj, list):
        if obj and all(_scalar(v) for v in obj):
            label = f"{parts[-1]}: " if parts and isinstance(parts[-1], str) else ""
            values = ", ".join(_show(v) for v in obj)[:2400]
            pb.add(label + values, kind, "json_pointer", pointer(parts))
        else:
            for i, v in enumerate(obj):
                _walk(pb, v, parts + [i], kind)


def _profile_records(name: str, records: list[dict]) -> TableProfile:
    df = records_frame(records)
    cols = []
    for c in df.columns:
        vals = [v for v in df[c].tolist() if v is not None and v == v and v != ""]
        types = {type(v).__name__ for v in vals}
        cols.append(
            ColumnProfile(
                name=str(c),
                dtype=types.pop() if len(types) == 1 else ("mixed" if types else "empty"),
                n_null=len(df) - len(vals),
                n_unique=len({str(v) for v in vals}),
                examples=list(dict.fromkeys(str(v)[:80] for v in vals))[:5],
            )
        )
    return TableProfile(table_name=name, n_rows=len(df), columns=cols)


# ---------- XML ----------


@lru_cache(maxsize=8)
def _load_xml(path: str) -> etree._ElementTree:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    return etree.parse(path, parser)


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _xml_path(el: etree._Element) -> str:
    parts = []
    while el is not None and isinstance(el.tag, str):
        parts.append(_local(el))
        el = el.getparent()
    return "/" + "/".join(reversed(parts))


def _xml_passages(pb: PassageBuilder, root: etree._Element, kind: str) -> None:
    for el in root.iter():
        if len(pb.items) >= MAX_PASSAGES or not isinstance(el.tag, str):
            continue
        lines = [f"@{etree.QName(a).localname}: {v}" for a, v in el.attrib.items()]
        text = (el.text or "").strip()
        if text:
            lines.append(text)
        children = [c for c in el if isinstance(c.tag, str)]
        if lines and (text or not children):
            pb.add(f"{_local(el)}: " + " | ".join(lines), kind, "xpath", _xml_path(el))


def _xsd_passages(pb: PassageBuilder, root: etree._Element) -> None:
    for el in root.iter(XS + "element", XS + "attribute"):
        name = el.get("name") or el.get("ref")
        if not name:
            continue
        docs = " ".join(
            " ".join(d.itertext()).strip() for d in el.iter(XS + "documentation")
        ).strip()
        typ = el.get("type", "")
        bits = [f"{_local(el)} {name}"]
        if typ:
            bits.append(f"type {typ}")
        for attr in ("minOccurs", "maxOccurs", "nillable"):
            if el.get(attr):
                bits.append(f"{attr} {el.get(attr)}")
        text = ", ".join(bits) + (f": {docs}" if docs else "")
        pb.add(text, "dictionary", "xpath", f"//xs:{_local(el)}[@name='{name}']")


def _xml_summary(root: etree._Element) -> tuple[Counter, int]:
    counts: Counter = Counter()
    n = 0
    for el in root.iter():
        if isinstance(el.tag, str):
            counts[_local(el)] += 1
            n += 1
    return counts, n


class StructuredReader:
    name = "structured"
    suffixes = (".json", ".xml", ".xsd")

    def _kind(self, src: SourceInfo) -> str:
        return (
            "metadata"
            if is_documentation_name(src.original_file) or src.suffix != ".json"
            else "body"
        )

    def profile(self, src: SourceInfo) -> SourceProfile:
        tables, warnings = [], []
        if src.suffix == ".json":
            data = load_json(src.path)
            if is_geojson(data):
                feats = data["features"]
                tables.append(
                    _profile_records(src.original_file, [f.get("properties") or {} for f in feats])
                )
                geoms = Counter((f.get("geometry") or {}).get("type") for f in feats)
                summary = (
                    f"{src.original_file}: GeoJSON, {len(feats)} features, geometry {dict(geoms)}"
                )
                warnings.append(
                    "GeoJSON read as properties only; spatial ops need the vector reader"
                )
            elif isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
                tables.append(_profile_records(src.original_file, data))
                summary = (
                    f"{src.original_file}: JSON list of {len(data)} records; fields: "
                    + ", ".join(c.name for c in tables[0].columns)
                )
            else:
                keys = list(data)[:30] if isinstance(data, dict) else []
                summary = f"{src.original_file}: JSON object; top-level keys: {', '.join(keys)}"
        else:
            root = _load_xml(src.path).getroot()
            counts, n = _xml_summary(root)
            top = ", ".join(f"{k} {v}" for k, v in counts.most_common(12))
            summary = (
                f"{src.original_file}: {src.suffix[1:].upper()} root {_local(root)}, "
                f"{n:,} elements; most common: {top}"
            )
            if n > LARGE_XML_ELEMENTS:
                warnings.append(f"{n:,} elements: passages limited to an element summary")
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="structured",
            tables=tables,
            summary=summary[:1200],
            is_documentation=is_documentation_name(src.original_file) or src.suffix == ".xsd",
            warnings=warnings,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        kind = self._kind(src)
        if src.suffix == ".json":
            data = load_json(src.path)
            if is_geojson(data):
                feats = data["features"]
                props = sorted({k for f in feats for k in (f.get("properties") or {})})
                pb.add(
                    f"{src.original_file}: {len(feats)} features; properties: " + ", ".join(props),
                    "header",
                    "json_pointer",
                    "/features",
                )
                _walk(pb, {k: v for k, v in data.items() if k != "features"}, [], "metadata")
                if len(feats) <= RECORD_PASSAGE_LIMIT:
                    for i, f in enumerate(feats):
                        _walk(pb, f.get("properties") or {}, ["features", i, "properties"], kind)
            elif isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
                fields = list(dict.fromkeys(k for r in data[:500] for k in _flatten(r)))
                pb.add(
                    f"{src.original_file}: {len(data)} records; fields: " + ", ".join(fields),
                    "header",
                    "json_pointer",
                    "/",
                )
                if len(data) <= RECORD_PASSAGE_LIMIT:
                    for i, r in enumerate(data):
                        _walk(pb, r, [i], kind)
            else:
                _walk(pb, data, [], kind)
        else:
            root = _load_xml(src.path).getroot()
            counts, n = _xml_summary(root)
            if src.suffix == ".xsd":
                _xsd_passages(pb, root)
            elif n > LARGE_XML_ELEMENTS:
                pb.add(
                    f"{src.original_file}: root {_local(root)}, {n:,} elements: "
                    + ", ".join(f"{k} {v}" for k, v in counts.most_common(40)),
                    "metadata",
                    "xpath",
                    "/" + _local(root),
                )
            else:
                _xml_passages(pb, root, kind)
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        if src.suffix != ".json":
            raise UnsupportedFormat(f"{src.original_file}: XML has no table interface yet")
        data = load_json(src.path)
        if is_geojson(data):
            return records_frame([f.get("properties") or {} for f in data["features"]])
        if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
            return records_frame(data)
        raise UnsupportedFormat(f"{src.original_file}: not a list of records")


register(StructuredReader())

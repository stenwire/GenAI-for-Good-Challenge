"""GeoJSON, GeoPackage and GML layers (plan §7.6). Geometry is never repaired here."""

from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from lxml import etree

from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    is_documentation_name,
    register,
)
from municipal_qa.schema import ColumnProfile, Passage, SourceInfo, SourceProfile, TableProfile

FEATURE_PASSAGE_LIMIT = 50
ID_HINTS = ("reference", "id", "gml_id", "baumnummer", "gisid", "objectid", "name")
GML = "http://www.opengis.net/gml"


def crs_label(crs) -> str | None:
    if crs is None:
        return None
    try:
        from pyproj import CRS

        c = CRS.from_user_input(crs)
        epsg = c.to_epsg()
        return f"EPSG:{epsg}" if epsg else c.name
    except Exception:  # noqa: BLE001
        return str(crs)[:80]


def is_citygml(path: str | Path) -> bool:
    with open(path, "rb") as f:
        head = f.read(4096)
    return b"CityModel" in head or b"citygml" in head.lower()


@lru_cache(maxsize=32)
def _layers(path: str) -> tuple[str, ...]:
    return tuple(str(n) for n, _ in pyogrio.list_layers(path))


def layer_names(src: SourceInfo) -> list[str]:
    """Public names: a single-layer GeoJSON is named after its file."""
    real = _layers(str(Path(src.path).resolve()))
    if src.suffix in (".geojson", ".json") and len(real) == 1:
        return [src.original_file]
    return list(real)


def _real_layer(src: SourceInfo, layer: str | None) -> str:
    real = _layers(str(Path(src.path).resolve()))
    names = layer_names(src)
    if layer is None:
        if len(real) == 1:
            return real[0]
        raise ValueError(f"{src.original_file} has layers {names}; name one")
    if layer in real:
        return layer
    if layer in names:
        return real[names.index(layer)]
    raise KeyError(f"no layer {layer!r} in {src.original_file}; layers: {names}")


@lru_cache(maxsize=16)
def _read(path: str, layer: str) -> gpd.GeoDataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pyogrio.read_dataframe(path, layer=layer)


def load_layer(src: SourceInfo, layer: str | None = None) -> gpd.GeoDataFrame:
    if src.suffix == ".gml" and is_citygml(src.path):
        return citygml_buildings(src.path)
    return _read(str(Path(src.path).resolve()), _real_layer(src, layer)).copy()


def id_column(columns: list[str]) -> str | None:
    low = {c.lower(): c for c in columns}
    for hint in ID_HINTS:
        if hint in low:
            return low[hint]
    return None


def feature_locator(layer: str, key: str | None, value: object, index: int) -> str:
    if key is None:
        return f"layer {layer}, feature {index + 1}"
    return f"layer {layer}, feature {key}={value}"


def _xyz(text: str, dim: int) -> list[tuple[float, ...]]:
    nums = [float(x) for x in text.split()]
    return [tuple(nums[i : i + dim]) for i in range(0, len(nums) - dim + 1, dim)]


@lru_cache(maxsize=4)
def _citygml(path: str) -> pd.DataFrame:
    rows = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    tree = etree.parse(path, parser)
    for el in tree.iter():
        if not isinstance(el.tag, str) or etree.QName(el).localname != "Building":
            continue
        zs = []
        for pos in el.iter(f"{{{GML}}}posList", f"{{{GML}}}pos"):
            dim = int(pos.get("srsDimension") or 3)
            zs += [p[2] for p in _xyz(pos.text or "", dim) if len(p) > 2]
        mh = next((c.text for c in el.iter() if etree.QName(c).localname == "measuredHeight"), None)
        rows.append(
            {
                "gml_id": el.get(f"{{{GML}}}id"),
                "measuredHeight": mh,
                "min_z": min(zs) if zs else None,
                "max_z": max(zs) if zs else None,
                "n_coords": len(zs),
            }
        )
    return pd.DataFrame(rows, columns=["gml_id", "measuredHeight", "min_z", "max_z", "n_coords"])


def citygml_buildings(path: str | Path) -> pd.DataFrame:
    return _citygml(str(Path(path).resolve())).copy()


def _columns(df: pd.DataFrame) -> list[ColumnProfile]:
    out = []
    for c in df.columns:
        if c == "geometry":
            continue
        s = df[c]
        text = s.dropna().astype(str)
        out.append(
            ColumnProfile(
                name=str(c),
                dtype=str(s.dtype),
                n_null=int(s.isna().sum()),
                n_unique=int(text.nunique()),
                examples=[v[:80] for v in text.drop_duplicates().head(5)],
            )
        )
    return out


def validity(gdf: gpd.GeoDataFrame) -> tuple[int, int]:
    geoms = gdf.geometry.values
    present = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
    invalid = present & ~shapely.is_valid(geoms)
    return int(invalid.sum()), int((~present).sum())


class VectorReader:
    name = "vector"
    suffixes = (".geojson", ".gpkg", ".gml", ".json")

    def accepts(self, src: SourceInfo) -> bool:
        if src.suffix != ".json":
            return True
        from municipal_qa.readers.structured import is_geojson, load_json

        data = load_json(src.path)
        return is_geojson(data) and any(
            isinstance(f, dict) and "geometry" in f for f in data["features"][:20]
        )

    def _citygml_profile(self, src: SourceInfo) -> SourceProfile:
        df = citygml_buildings(src.path)
        t = TableProfile(table_name="Building", n_rows=len(df), columns=_columns(df))
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="vector",
            tables=[t],
            summary=f"{src.original_file}: CityGML, {len(df):,} Building objects",
            is_documentation=False,
            warnings=["CityGML read with lxml: building ids, measuredHeight and Z range only"],
        )

    def profile(self, src: SourceInfo) -> SourceProfile:
        if src.suffix == ".gml" and is_citygml(src.path):
            return self._citygml_profile(src)
        tables, warns, parts = [], [], []
        path = str(Path(src.path).resolve())
        for name, real in zip(layer_names(src), _layers(path), strict=True):
            gdf = _read(path, real)
            crs = crs_label(gdf.crs)
            if crs is None:
                warns.append(f"layer {name}: no CRS recorded in the file")
            gtypes = sorted({str(g) for g in gdf.geom_type.dropna().unique()})
            bad, missing = validity(gdf)
            if bad:
                warns.append(f"layer {name}: {bad} invalid geometries, kept as published")
            if missing:
                warns.append(f"layer {name}: {missing} features without geometry")
            bounds = None if gdf.empty or missing == len(gdf) else gdf.total_bounds
            tables.append(
                TableProfile(
                    table_name=name,
                    n_rows=len(gdf),
                    columns=_columns(gdf),
                    crs=crs,
                    geometry_type="/".join(gtypes) or None,
                    bounds=[float(b) for b in bounds] if bounds is not None else None,
                )
            )
            parts.append(f"layer {name}: {len(gdf):,} features, {'/'.join(gtypes)}, {crs}")
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="vector",
            tables=tables,
            summary=(f"{src.original_file}: " + "; ".join(parts))[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=warns,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        if src.suffix == ".gml" and is_citygml(src.path):
            df = citygml_buildings(src.path)
            pb.add(
                f"{src.original_file}: CityGML with {len(df):,} buildings; fields: "
                + ", ".join(df.columns),
                "header",
                "layer",
                "layer Building",
            )
            return pb.items
        for t in self.profile(src).tables:
            cols = ", ".join(c.name for c in t.columns)
            pb.add(
                f"{src.original_file} layer {t.table_name}: {t.n_rows:,} features, "
                f"{t.geometry_type or 'no geometry'} in {t.crs or 'unknown CRS'}; fields: {cols}",
                "header",
                "layer",
                f"layer {t.table_name}",
            )
            if t.n_rows > FEATURE_PASSAGE_LIMIT:
                continue
            gdf = load_layer(src, t.table_name)
            key = id_column([str(c) for c in gdf.columns])
            for i, row in gdf.drop(columns="geometry").reset_index(drop=True).iterrows():
                vals = "; ".join(f"{k}={v}" for k, v in row.items() if pd.notna(v))
                loc = feature_locator(t.table_name, key, row.get(key) if key else None, i)
                pb.add(vals, "body", "feature", loc)
        if src.suffix in (".geojson", ".json"):
            from municipal_qa.readers.structured import _walk, load_json

            data = load_json(src.path)
            _walk(pb, {k: v for k, v in data.items() if k != "features"}, [], "metadata")
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> gpd.GeoDataFrame:
        try:
            return load_layer(src, table)
        except (KeyError, ValueError) as e:
            raise UnsupportedFormat(str(e)) from e


register(VectorReader())

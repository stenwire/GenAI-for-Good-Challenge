"""Raster and point-cloud ops (plan §6.5, card S4): nodata masked, valid pairs only."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

import numpy as np
import rasterio
from pyproj import CRS, Geod
from rasterio.features import geometry_mask

from municipal_qa.readers import pointcloud as pc
from municipal_qa.readers.raster import read_band
from municipal_qa.readers.vector import crs_label
from municipal_qa.schema import Locator
from municipal_qa.tools.calc import convert
from municipal_qa.tools.registry import OpError, op
from municipal_qa.tools.table_ops import WHERE, count_fact, num_fact, ok, str_fact, where_text

S = {"type": "string"}
NUM = {"type": "number"}
CMP = ["<", "<=", ">", ">=", "=", "!="]
COND = {"type": "object", "properties": {"op": {"type": "string", "enum": CMP}, "value": NUM}}
AREA_UNITS = ["m²", "ha", "km²"]
MASK = {"mask_source": S, "mask_table": S, "mask_where": WHERE}


def _cmp(a: np.ndarray, op_: str, v: float) -> np.ndarray:
    return {"<": a < v, "<=": a <= v, ">": a > v, ">=": a >= v, "=": a == v, "!=": a != v}[op_]


def _meta(path: str) -> dict:
    with rasterio.open(path) as r:
        return {
            "transform": r.transform,
            "crs": r.crs,
            "shape": (r.height, r.width),
            "nodata": r.nodata,
        }


def _linear_unit(crs) -> str:
    c = CRS.from_user_input(crs)
    name = (c.axis_info[0].unit_name or "metre").lower() if c.axis_info else "metre"
    return "ftUS" if "us survey" in name else "ft" if "foot" in name else "m"


@lru_cache(maxsize=8)
def _row_cell_areas(path: str) -> np.ndarray:
    """Area in m² of one cell in each row; geodesic for geographic rasters."""
    m = _meta(path)
    t, (h, _) = m["transform"], m["shape"]
    crs = CRS.from_user_input(m["crs"]) if m["crs"] else None
    if crs is not None and crs.is_geographic:
        geod = Geod(ellps="WGS84")
        out = np.empty(h)
        for r in range(h):
            x0, y0 = t @ (0, r)
            x1, y1 = t @ (1, r + 1)
            out[r] = abs(geod.polygon_area_perimeter([x0, x1, x1, x0], [y0, y0, y1, y1])[0])
        return out
    unit = _linear_unit(crs) if crs else "m"
    cell = abs(t.a * t.e - t.b * t.d)
    return np.full(
        h, float(convert(Decimal(repr(cell)), unit + "²" if unit != "m" else "m²", "m²"))
    )


def _area(path: str, mask: np.ndarray) -> Decimal:
    rows = mask.sum(axis=1).astype(float)
    return Decimal(repr(float((rows * _row_cell_areas(path)).sum())))


def _polygon_mask(ctx, path, mask_source, mask_table, mask_where) -> tuple[np.ndarray | None, str]:
    if not mask_source:
        return None, ""
    from municipal_qa.tools.spatial_ops import _geo
    from municipal_qa.tools.table_ops import apply_where

    gdf, _ = _geo(ctx, mask_source, ctx.table_name(mask_source, mask_table))
    sel = apply_where(gdf, mask_where)
    if sel.empty:
        raise OpError(f"mask: no polygons in {mask_source}{where_text(mask_where)}")
    m = _meta(path)
    geoms = sel.to_crs(m["crs"]).geometry.values if m["crs"] and sel.crs else sel.geometry.values
    inside = geometry_mask(geoms, out_shape=m["shape"], transform=m["transform"], invert=True)
    return (
        inside,
        f" inside {len(sel):,} polygon(s) of {ctx.source(mask_source).original_file}"
        f"{where_text(mask_where)} (cell centres)",
    )


def _band(ctx, sid, band):
    src = ctx.source(sid)
    if ctx.profile(sid).kind != "raster":
        raise OpError(f"{sid} is not a raster")
    return src.path, read_band(src.path, band)


@op(
    "raster_info",
    "size, CRS, pixel size, nodata and valid-pixel count of a raster band",
    {"source_id": S, "band": {"type": "integer"}},
    ["source_id"],
    group="raster",
)
def raster_info(ctx, source_id, band=1):
    path, arr = _band(ctx, source_id, band)
    m = _meta(path)
    loc = [Locator(source_id=source_id, kind="raster", text=f"band {band}, raster header")]
    valid = int(arr.count())
    return ok(
        "raster_info",
        {},
        [
            str_fact("crs", crs_label(m["crs"]), f"raster CRS is {crs_label(m['crs'])}", loc),
            str_fact(
                "size", f"{m['shape'][1]} x {m['shape'][0]}", "raster width x height in pixels", loc
            ),
            str_fact(
                "pixel_size",
                f"{abs(m['transform'].a):g} x {abs(m['transform'].e):g}",
                "pixel size",
                loc,
            ),
            str_fact("nodata", m["nodata"], f"nodata value is {m['nodata']}", loc),
            count_fact(
                "valid_pixels", valid, "pixels", f"{valid:,} valid pixels in band {band}", loc
            ),
        ],
    )


@op(
    "raster_stats",
    "stats of valid pixels; optional condition, class values and polygon mask",
    {
        "source_id": S,
        "band": {"type": "integer"},
        "condition": COND,
        "classes": {"type": "array"},
        "unit": {"type": "string", "enum": AREA_UNITS},
        **MASK,
    },
    ["source_id"],
    group="raster",
)
def raster_stats(
    ctx,
    source_id,
    band=1,
    condition=None,
    classes=None,
    unit="m²",
    mask_source=None,
    mask_table=None,
    mask_where=None,
):
    path, arr = _band(ctx, source_id, band)
    valid = ~np.ma.getmaskarray(arr)
    poly, where = _polygon_mask(ctx, path, mask_source, mask_table, mask_where)
    if poly is not None:
        valid &= poly
    data = arr.data
    n = int(valid.sum())
    loc = [Locator(source_id=source_id, kind="raster", text=f"band {band}, valid pixels{where}")]
    facts = [
        count_fact("valid_pixels", n, "pixels", f"{n:,} valid pixels in band {band}{where}", loc)
    ]
    if n:
        v = data[valid].astype("float64")
        for k, x in (("min", v.min()), ("max", v.max()), ("mean", v.mean())):
            f = num_fact(k, Decimal(repr(float(x))), ctx, None, "", loc)
            f.claim = f"{k} of valid pixels{where} is {f.display}"
            facts.append(f)
        a = num_fact("valid_area", convert(_area(path, valid), "m²", unit), ctx, unit, "", loc)
        a.claim = f"valid pixels{where} cover {a.display}"
        facts.append(a)
    if condition:
        hit = valid & _cmp(data, condition["op"], float(condition["value"]))
        facts += _hits(
            ctx, path, hit, n, f"value {condition['op']} {condition['value']:g}{where}", unit, loc
        )
    for c in classes or []:
        hit = valid & (data == float(c))
        facts += _hits(ctx, path, hit, n, f"class {c}{where}", unit, loc, prefix=f"class_{c}.")
    return ok("raster_stats", {}, facts)


def _hits(ctx, path, hit, n, what, unit, loc, prefix=""):
    k = int(hit.sum())
    f = count_fact(prefix + "count", k, "pixels", f"{k:,} of {n:,} valid pixels have {what}", loc)
    out = [f]
    if n:
        s = num_fact(prefix + "share", Decimal(k) * 100 / Decimal(n), ctx, "%", "", loc)
        s.claim = f"{s.display} of valid pixels have {what}"
        out.append(s)
    a = num_fact(prefix + "area", convert(_area(path, hit), "m²", unit), ctx, unit, "", loc)
    a.claim = f"pixels with {what} cover {a.display}"
    out.append(a)
    return out


@op(
    "raster_pair_diff",
    "A minus B on aligned grids (e.g. DSM - DTM) over valid pairs only",
    {
        "source_a": S,
        "source_b": S,
        "band": {"type": "integer"},
        "condition": COND,
        "unit": {"type": "string", "enum": AREA_UNITS},
        **MASK,
    },
    ["source_a", "source_b"],
    group="raster",
)
def raster_pair_diff(
    ctx,
    source_a,
    source_b,
    band=1,
    condition=None,
    unit="m²",
    mask_source=None,
    mask_table=None,
    mask_where=None,
):
    pa, a = _band(ctx, source_a, band)
    pb, b = _band(ctx, source_b, band)
    ma, mb = _meta(pa), _meta(pb)
    if ma["shape"] != mb["shape"] or ma["transform"] != mb["transform"] or ma["crs"] != mb["crs"]:
        raise OpError(
            "rasters are not aligned (shape, transform or CRS differ); no pixel pairing done"
        )
    valid = ~np.ma.getmaskarray(a) & ~np.ma.getmaskarray(b)
    poly, where = _polygon_mask(ctx, pa, mask_source, mask_table, mask_where)
    if poly is not None:
        valid &= poly
    diff = a.data.astype("float64") - b.data.astype("float64")
    n = int(valid.sum())
    names = f"{ctx.source(source_a).original_file} minus {ctx.source(source_b).original_file}"
    loc = [
        Locator(source_id=s, kind="raster", text=f"band {band}, valid pairs{where}")
        for s in (source_a, source_b)
    ]
    facts = [
        count_fact(
            "valid_pairs", n, "pixel pairs", f"{n:,} pixel pairs valid in both rasters{where}", loc
        )
    ]
    if n:
        d = diff[valid]
        for k, x in (("mean", d.mean()), ("min", d.min()), ("max", d.max())):
            f = num_fact(k, Decimal(repr(float(x))), ctx, None, "", loc)
            f.claim = f"{k} of {names} over valid pairs{where} is {f.display}"
            facts.append(f)
    if condition:
        hit = valid & _cmp(diff, condition["op"], float(condition["value"]))
        facts += _hits(
            ctx, pa, hit, n, f"{names} {condition['op']} {condition['value']:g}{where}", unit, loc
        )
    return ok("raster_pair_diff", {}, facts)


def _cloud(ctx, sid):
    if ctx.profile(sid).kind != "pointcloud":
        raise OpError(f"{sid} is not a point cloud")
    return ctx.source(sid).path


@op(
    "pointcloud_info",
    "point count, CRS and header x/y/z extrema (and the extent rectangle area)",
    {"source_id": S},
    ["source_id"],
    group="raster",
)
def pointcloud_info(ctx, source_id):
    h = pc.header(_cloud(ctx, source_id))
    loc = [Locator(source_id=source_id, kind="pointcloud", text="header extrema")]
    w = Decimal(repr(h["maxs"][0])) - Decimal(repr(h["mins"][0]))
    d = Decimal(repr(h["maxs"][1])) - Decimal(repr(h["mins"][1]))
    facts = [
        count_fact(
            "points", h["points"], "points", f"{h['points']:,} point records in the header", loc
        ),
        str_fact("crs", h["crs"] or "not recorded", f"point cloud CRS: {h['crs']}", loc),
    ]
    for i, axis in enumerate("xyz"):
        for k in ("min", "max"):
            v = Decimal(repr(h[k + "s"][i]))
            facts.append(
                num_fact(f"{axis}_{k}", v, ctx, None, f"header {axis} {k} {v}", loc, decimals=3)
            )
    area = num_fact("extent_area", w * d, ctx, None, "", loc)
    area.claim = f"rectangle between header x/y extrema is {area.display} square CRS units"
    facts.append(area)
    return ok("pointcloud_info", {}, facts)


@op(
    "pointcloud_class_counts",
    "points per classification code (all points, read in chunks)",
    {"source_id": S, "classes": {"type": "array"}},
    ["source_id"],
    group="raster",
)
def pointcloud_class_counts(ctx, source_id, classes=None):
    counts = pc.class_counts(_cloud(ctx, source_id))
    total = sum(counts.values())
    facts = [
        count_fact(
            "total",
            total,
            "points",
            f"{total:,} points in all classes",
            [Locator(source_id=source_id, kind="pointcloud", text="all points")],
        )
    ]
    for c in [int(x) for x in classes] if classes else list(counts):
        n = counts.get(c, 0)
        loc = [
            Locator(source_id=source_id, kind="pointcloud", text=f"classification {c}, all points")
        ]
        f = count_fact(f"class_{c}", n, "points", f"{n:,} points have classification {c}", loc)
        pct = num_fact("s", Decimal(n) * 100 / Decimal(total or 1), ctx, "%", "", loc)
        f.note = f"share {pct.display}"
        facts.append(f)
    return ok("pointcloud_class_counts", {}, facts)


@op(
    "pointcloud_z_stats",
    "min, max and mean Z of points, optionally for some classes only",
    {"source_id": S, "classes": {"type": "array"}},
    ["source_id"],
    group="raster",
)
def pointcloud_z_stats(ctx, source_id, classes=None):
    path = _cloud(ctx, source_id)
    want = [int(c) for c in classes] if classes else None
    n, total, lo, hi = 0, 0.0, np.inf, -np.inf
    for chunk in pc.iter_chunks(path):
        z = np.asarray(chunk.z, dtype="float64")
        if want is not None:
            z = z[np.isin(np.asarray(chunk.classification), want)]
        if len(z):
            n += len(z)
            total += float(z.sum())
            lo, hi = min(lo, float(z.min())), max(hi, float(z.max()))
    what = f"classification {', '.join(map(str, want))}" if want else "all points"
    loc = [Locator(source_id=source_id, kind="pointcloud", text=what)]
    if not n:
        raise OpError(f"no points with {what}")
    facts = [count_fact("count", n, "points", f"{n:,} points ({what})", loc)]
    for k, v in (("min_z", lo), ("max_z", hi), ("mean_z", total / n)):
        f = num_fact(k, Decimal(repr(v)), ctx, None, "", loc)
        f.claim = f"{k.replace('_', ' ')} of {what} is {f.display}"
        facts.append(f)
    return ok("pointcloud_z_stats", {}, facts)

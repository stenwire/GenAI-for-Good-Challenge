"""Spatial ops (plan §6.5, card S4): measure in the Context CRS, never repair unless allowed."""

from __future__ import annotations

from decimal import Decimal

import geopandas as gpd
import numpy as np
import shapely
from pyproj import CRS

from municipal_qa.readers.vector import crs_label, feature_locator, id_column
from municipal_qa.schema import Locator
from municipal_qa.tools.calc import convert
from municipal_qa.tools.numbers import to_decimal
from municipal_qa.tools.registry import ExecContext, OpError, op
from municipal_qa.tools.table_ops import (
    WHERE,
    apply_where,
    count_fact,
    num_fact,
    ok,
    scope,
    str_fact,
    text,
    val,
    where_text,
)

S = {"type": "string"}
NUM = {"type": "number"}
SEL = {"source_id": S, "table": S, "where": WHERE, "key_column": S, "value": {}, "crs": S}
SEL_B = {"source_b": S, "table_b": S, "where_b": WHERE, "key_column_b": S, "value_b": {}}
AREA_UNITS = ["m²", "ha", "km²"]
MAX_FEATURES = 20


def _geo(ctx: ExecContext, sid: str, table: str | None, source_crs: str | None = None):
    df = ctx.frame(sid, table)
    if isinstance(df, gpd.GeoDataFrame) and "geometry" in df:
        return df, []
    wkt = next(
        (c for c in df.columns if "wkt" in str(c).lower() or str(c).lower() == "geometry"), None
    )
    if wkt is None:
        raise OpError(f"{sid} table {table} has no geometry")
    geoms = shapely.from_wkt(df[wkt].map(lambda x: x or None), on_invalid="ignore")
    crs = source_crs or ctx.conventions.crs
    gdf = gpd.GeoDataFrame(df.drop(columns=[wkt]), geometry=geoms, crs=crs)
    return gdf, [f"geometry from WKT column {wkt}; CRS {crs or 'not stated'} assumed"]


def _select(ctx, sid, table, where=None, key_column=None, value=None):
    gdf, notes = _geo(ctx, sid, table)
    if key_column is not None and value is not None:
        where = [*(where or []), {"column": key_column, "op": "=", "value": val(value)}]
    sel = apply_where(gdf, where)
    if sel.empty:
        raise OpError(f"no features in {scope(ctx, sid, table)}{where_text(where)}")
    return sel, where, notes


def target_crs(ctx: ExecContext, gdf: gpd.GeoDataFrame, crs: str | None) -> tuple[CRS, str | None]:
    """Explicit arg, then Context CRS, then the layer's projected CRS, else a local UTM zone."""
    want = crs or ctx.conventions.crs
    if want:
        return CRS.from_user_input(want), None
    if gdf.crs is None:
        raise OpError("layer has no CRS and none is stated; pass crs")
    if CRS.from_user_input(gdf.crs).is_projected:
        return CRS.from_user_input(gdf.crs), None
    utm = gdf.estimate_utm_crs()
    return utm, f"no planar CRS stated; measured in {crs_label(utm)} (local UTM)"


def _to(gdf: gpd.GeoDataFrame, crs: CRS) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        raise OpError("layer has no CRS recorded; cannot reproject")
    return gdf.to_crs(crs)


def _locs(ctx, sid, table, sel, key_column=None, where=None) -> list[Locator]:
    key = key_column or id_column([str(c) for c in sel.columns])
    if len(sel) <= 3:
        return [Locator(source_id=sid, kind="feature",
                        text=feature_locator(table, key, text(r[key]) if key else None, int(i)))
                for i, r in sel.iterrows()]  # fmt: skip
    return [Locator(source_id=sid, kind="layer", text=f"layer {table}{where_text(where)}")]


def _validity_note(sel: gpd.GeoDataFrame, repair: bool) -> str | None:
    bad = int((~sel.geometry.is_valid & sel.geometry.notna()).sum())
    if not bad:
        return None
    return f"{bad} invalid geometr{'y' if bad == 1 else 'ies'} " + (
        "repaired with make_valid (Context allows repair)"
        if repair
        else "measured as published, not repaired"
    )


def _prepared(ctx, sel):
    if ctx.conventions.repair_geometry:
        sel = sel.copy()
        sel.geometry = sel.geometry.make_valid()
    return sel


def _dec(x: float) -> Decimal:
    return Decimal(repr(float(x)))


@op(
    "layer_info",
    "feature count, geometry types, CRS, bounds and fields of a vector layer",
    {"source_id": S, "table": S},
    ["source_id"],
    group="spatial",
)
def layer_info(ctx, source_id, table=None):
    gdf, notes = _geo(ctx, source_id, table)
    loc = [Locator(source_id=source_id, kind="layer", text=f"layer {table}")]
    gt = ", ".join(f"{k} {v}" for k, v in gdf.geom_type.value_counts().items())
    facts = [
        count_fact(
            "features",
            len(gdf),
            "features",
            f"{len(gdf):,} features in {scope(ctx, source_id, table)}",
            loc,
        ),
        str_fact(
            "crs",
            crs_label(gdf.crs) or "not recorded",
            f"layer {table} CRS is {crs_label(gdf.crs)}",
            loc,
        ),
        str_fact("geometry_types", gt or "none", f"geometry types in {table}: {gt}", loc),
    ]
    return ok("layer_info", {}, facts, "; ".join(notes) or None)


@op(
    "count_features",
    "number of features, optionally filtered by attributes",
    {"source_id": S, "table": S, "where": WHERE},
    ["source_id"],
    group="spatial",
)
def count_features(ctx, source_id, table=None, where=None):
    gdf, _ = _geo(ctx, source_id, table)
    sel = apply_where(gdf, where)
    missing = int(sel.geometry.isna().sum() + sel.geometry.is_empty.sum())
    loc = [Locator(source_id=source_id, kind="layer", text=f"layer {table}{where_text(where)}")]
    facts = [
        count_fact(
            "count",
            len(sel),
            "features",
            f"{len(sel):,} features in {scope(ctx, source_id, table)}{where_text(where)}",
            loc,
        )
    ]
    if missing:
        facts.append(
            count_fact(
                "without_geometry", missing, "features", f"{missing:,} have no geometry", loc
            )
        )
    return ok("count_features", {}, facts)


@op(
    "feature_lookup",
    "attributes of the feature(s) where key_column equals value",
    {"source_id": S, "table": S, "key_column": S, "value": {}, "columns": {"type": "array"}},
    ["source_id", "key_column", "value"],
    group="spatial",
)
def feature_lookup(ctx, source_id, key_column, value, table=None, columns=None):
    sel, _, _ = _select(ctx, source_id, table, key_column=key_column, value=value)
    cols = columns or [c for c in sel.columns if c not in ("geometry", key_column)][:12]
    facts = []
    for i, row in list(sel.iterrows())[:3]:
        base = feature_locator(table, key_column, text(row[key_column]), int(i))
        for c in cols:
            loc = Locator(source_id=source_id, kind="feature", text=f"{base}, {c}")
            facts.append(
                str_fact(
                    c, row[c], f"{key_column}={text(row[key_column])} has {c} {text(row[c])}", [loc]
                )
            )
        facts.append(
            str_fact(
                "geometry_type",
                row.geometry.geom_type if row.geometry is not None else None,
                f"geometry of {key_column}={text(row[key_column])}",
                [Locator(source_id=source_id, kind="feature", text=base)],
            )
        )
    return ok("feature_lookup", {}, facts)


@op(
    "area",
    "area of the selected polygons (summed), reprojected to the Context CRS first",
    {**SEL, "unit": {"type": "string", "enum": AREA_UNITS}, "per_feature": {"type": "boolean"}},
    ["source_id"],
    group="spatial",
)
def area(
    ctx,
    source_id,
    table=None,
    where=None,
    key_column=None,
    value=None,
    crs=None,
    unit="m²",
    per_feature=False,
):
    sel, where, notes = _select(ctx, source_id, table, where, key_column, value)
    tcrs, note = target_crs(ctx, sel, crs)
    proj = _to(_prepared(ctx, sel), tcrs)
    areas = proj.geometry.area
    total = convert(_dec(areas.sum()), "m²", unit)
    label = crs_label(tcrs)
    vnote = _validity_note(sel, ctx.conventions.repair_geometry)
    note_s = "; ".join(x for x in [note, vnote, *notes] if x) or None
    what = (
        f"{key_column}={text(val(value))}"
        if key_column and value is not None
        else f"{len(sel):,} features{where_text(where)}"
    )
    f = num_fact(
        "area", total, ctx, unit, "", _locs(ctx, source_id, table, sel, key_column, where), note_s
    )
    f.claim = (
        f"area of {what} in {scope(ctx, source_id, table)} is {f.display}, measured in {label}"
    )
    facts = [f]
    if per_feature:
        key = key_column or id_column([str(c) for c in sel.columns])
        for i, a in list(areas.items())[:MAX_FEATURES]:
            g = num_fact(
                text(sel.loc[i, key]) if key else str(i),
                convert(_dec(a), "m²", unit),
                ctx,
                unit,
                "",
                [
                    Locator(
                        source_id=source_id,
                        kind="feature",
                        text=feature_locator(
                            table, key, text(sel.loc[i, key]) if key else None, int(i)
                        ),
                    )
                ],
            )
            g.claim = f"feature {g.name} area {g.display} in {label}"
            facts.append(g)
    return ok("area", {}, facts)


@op(
    "length",
    "total length of the selected lines (or polygon perimeters) in metres, Context CRS",
    {**SEL, "unit": {"type": "string", "enum": ["m", "km"]}},
    ["source_id"],
    group="spatial",
)
def length(ctx, source_id, table=None, where=None, key_column=None, value=None, crs=None, unit="m"):
    sel, where, _ = _select(ctx, source_id, table, where, key_column, value)
    tcrs, note = target_crs(ctx, sel, crs)
    total = convert(_dec(_to(sel, tcrs).geometry.length.sum()), "m", unit)
    f = num_fact(
        "length", total, ctx, unit, "", _locs(ctx, source_id, table, sel, key_column, where), note
    )
    f.claim = (
        f"length of {len(sel):,} features in {scope(ctx, source_id, table)} is {f.display}, "
        f"measured in {crs_label(tcrs)}"
    )
    return ok("length", {}, [f])


def _feature_b(ctx, source_b, table_b, where_b, key_column_b, value_b):
    if not source_b:
        raise OpError("give source_b with key_column_b/value_b or where_b")
    return _select(ctx, source_b, table_b, where_b, key_column_b, value_b)


@op(
    "distance",
    "shortest distance between selection A and selection B (metres, Context CRS)",
    {**SEL, **SEL_B},
    ["source_id", "source_b"],
    group="spatial",
)
def distance(
    ctx,
    source_id,
    source_b,
    table=None,
    where=None,
    key_column=None,
    value=None,
    crs=None,
    table_b=None,
    where_b=None,
    key_column_b=None,
    value_b=None,
):
    a, _, _ = _select(ctx, source_id, table, where, key_column, value)
    b, _, _ = _feature_b(ctx, source_b, table_b, where_b, key_column_b, value_b)
    tcrs, note = target_crs(ctx, a, crs)
    ga, gb = (
        shapely.union_all(_to(a, tcrs).geometry.values),
        shapely.union_all(_to(b, tcrs).geometry.values),
    )
    d = _dec(shapely.distance(ga, gb))
    locs = _locs(ctx, source_id, table, a, key_column, where) + _locs(
        ctx, source_b, table_b, b, key_column_b, where_b
    )
    f = num_fact("distance", d, ctx, "m", "", locs, note)
    f.claim = f"shortest distance is {f.display} in {crs_label(tcrs)}"
    return ok("distance", {}, [f])


@op(
    "within_radius",
    "count features within r metres of a centre feature; inclusive follows Context",
    {
        **SEL,
        "radius": NUM,
        "inclusive": {"type": "boolean"},
        "targets": {
            "type": "array",
            "items": {"type": "object", "properties": {"source_id": S, "table": S}},
        },
    },
    ["source_id", "radius"],
    group="spatial",
)
def within_radius(
    ctx,
    source_id,
    radius,
    table=None,
    where=None,
    key_column=None,
    value=None,
    crs=None,
    inclusive=None,
    targets=None,
):
    centre, where, _ = _select(ctx, source_id, table, where, key_column, value)
    tcrs, note = target_crs(ctx, centre, crs)
    c = shapely.union_all(_to(centre, tcrs).geometry.values)
    inc = inclusive if inclusive is not None else ctx.conventions.radius_inclusive is not False
    r = float(radius)
    total, facts, locs = 0, [], _locs(ctx, source_id, table, centre, key_column, where)
    for t in targets or [{"source_id": source_id, "table": table}]:
        sid = t.get("source_id") or source_id
        ctx.source(sid)
        tname = ctx.table_name(sid, t.get("table"))
        gdf, _ = _geo(ctx, sid, tname)
        d = _to(gdf, tcrs).geometry.distance(c)
        n = int((d <= r).sum() if inc else (d < r).sum())
        total += n
        facts.append(
            count_fact(
                f"{sid}:{tname}",
                n,
                "features",
                f"{n:,} features of {scope(ctx, sid, tname)} lie within {r:g} m",
                [Locator(source_id=sid, kind="layer", text=f"layer {tname}")],
            )
        )
    rule = "boundary included (distance <= r)" if inc else "boundary excluded (distance < r)"
    f = count_fact(
        "count",
        total,
        "features",
        f"{total:,} features within {r:g} m of the centre, {rule}, measured in {crs_label(tcrs)}",
        locs + [x for g in facts for x in g.locators],
        note,
    )
    return ok("within_radius", {}, [f, *facts])


def _pairs(ctx, a, b, crs, predicate):
    tcrs, note = target_crs(ctx, a, crs)
    pa, pb = _to(a, tcrs), _to(b, tcrs)
    idx = gpd.sjoin(pa[["geometry"]], pb[["geometry"]], predicate=predicate, how="inner")
    return idx, tcrs, note


@op(
    "point_in_polygon",
    "how many features of A lie in the polygons of B (boundary counts as inside)",
    {**SEL, **SEL_B},
    ["source_id", "source_b"],
    group="spatial",
)
def point_in_polygon(
    ctx,
    source_id,
    source_b,
    table=None,
    where=None,
    key_column=None,
    value=None,
    crs=None,
    table_b=None,
    where_b=None,
    key_column_b=None,
    value_b=None,
):
    a, _, _ = _select(ctx, source_id, table, where, key_column, value)
    b, _, _ = _feature_b(ctx, source_b, table_b, where_b, key_column_b, value_b)
    idx, tcrs, note = _pairs(ctx, a, b, crs, "covered_by")
    n = int(idx.index.nunique())
    locs = _locs(ctx, source_b, table_b, b, key_column_b, where_b)
    f = count_fact(
        "inside",
        n,
        "features",
        f"{n:,} of {len(a):,} features of {scope(ctx, source_id, table)} lie "
        f"in the selected polygons of {scope(ctx, source_b, table_b)}",
        locs,
        note,
    )
    return ok(
        "point_in_polygon",
        {},
        [f, count_fact("outside", len(a) - n, "features", f"{len(a) - n:,} lie outside", locs)],
    )


@op(
    "intersects",
    "features of A that intersect B, and the area of A that overlaps B",
    {**SEL, **SEL_B, "unit": {"type": "string", "enum": AREA_UNITS}},
    ["source_id", "source_b"],
    group="spatial",
)
def intersects(
    ctx,
    source_id,
    source_b,
    table=None,
    where=None,
    key_column=None,
    value=None,
    crs=None,
    table_b=None,
    where_b=None,
    key_column_b=None,
    value_b=None,
    unit="m²",
):
    a, _, _ = _select(ctx, source_id, table, where, key_column, value)
    b, _, _ = _feature_b(ctx, source_b, table_b, where_b, key_column_b, value_b)
    idx, tcrs, note = _pairs(ctx, a, b, crs, "intersects")
    ua = shapely.union_all(_to(_prepared(ctx, a), tcrs).geometry.values)
    ub = shapely.union_all(_to(_prepared(ctx, b), tcrs).geometry.values)
    inter = convert(_dec(shapely.intersection(ua, ub).area), "m²", unit)
    locs = _locs(ctx, source_id, table, a, key_column, where) + _locs(
        ctx, source_b, table_b, b, key_column_b, where_b
    )
    n = int(idx.index.nunique())
    g = num_fact("overlap_area", inter, ctx, unit, "", locs, note)
    g.claim = f"overlap area is {g.display} in {crs_label(tcrs)}"
    return ok(
        "intersects",
        {},
        [count_fact("count", n, "features", f"{n:,} of {len(a):,} features intersect", locs), g],
    )


@op(
    "bbox_filter",
    "count features inside a bounding box [minx, miny, maxx, maxy] in the given CRS",
    {"source_id": S, "table": S, "bbox": {"type": "array", "items": NUM}, "crs": S},
    ["source_id", "bbox"],
    group="spatial",
)
def bbox_filter(ctx, source_id, bbox, table=None, crs=None):
    gdf, _ = _geo(ctx, source_id, table)
    if len(bbox) != 4:
        raise OpError("bbox needs 4 numbers: minx, miny, maxx, maxy")
    box = gpd.GeoSeries([shapely.box(*map(float, bbox))], crs=crs or ctx.conventions.crs or gdf.crs)
    n = int(gdf.geometry.intersects(box.to_crs(gdf.crs).iloc[0]).sum())
    loc = [Locator(source_id=source_id, kind="layer", text=f"layer {table}, bbox {list(bbox)}")]
    return ok(
        "bbox_filter",
        {},
        [count_fact("count", n, "features", f"{n:,} features intersect the box", loc)],
    )


@op(
    "geometry_validity",
    "count invalid or empty geometries and why; never repairs",
    {"source_id": S, "table": S, "where": WHERE, "key_column": S},
    ["source_id"],
    group="spatial",
)
def geometry_validity(ctx, source_id, table=None, where=None, key_column=None):
    gdf, _ = _geo(ctx, source_id, table)
    sel = apply_where(gdf, where)
    g = sel.geometry
    present = g.notna() & ~g.is_empty
    bad = sel[present & ~g.is_valid]
    key = key_column or id_column([str(c) for c in sel.columns])
    reasons = shapely.is_valid_reason(bad.geometry.values) if len(bad) else []
    loc = [Locator(source_id=source_id, kind="layer", text=f"layer {table}{where_text(where)}")]
    facts = [
        count_fact(
            "invalid",
            len(bad),
            "features",
            f"{len(bad):,} of {len(sel):,} geometries in "
            f"{scope(ctx, source_id, table)} are invalid as published",
            loc,
        ),
        count_fact(
            "missing_or_empty",
            int((~present).sum()),
            "features",
            f"{int((~present).sum()):,} features have no or empty geometry",
            loc,
        ),
    ]
    for (i, row), why in list(zip(bad.iterrows(), reasons, strict=True))[:5]:
        floc = Locator(
            source_id=source_id,
            kind="feature",
            text=feature_locator(table, key, text(row[key]) if key else None, int(i)),
        )
        facts.append(
            str_fact(f"reason@{text(row[key]) if key else i}", why, f"invalid: {why}", [floc])
        )
    return ok("geometry_validity", {}, facts)


@op(
    "union_footprint",
    "area of the union of the selected polygons (overlaps counted once)",
    {**SEL, "unit": {"type": "string", "enum": AREA_UNITS}},
    ["source_id"],
    group="spatial",
)
def union_footprint(
    ctx, source_id, table=None, where=None, key_column=None, value=None, crs=None, unit="m²"
):
    sel, where, _ = _select(ctx, source_id, table, where, key_column, value)
    tcrs, note = target_crs(ctx, sel, crs)
    try:
        u = shapely.union_all(_to(_prepared(ctx, sel), tcrs).geometry.values)
    except shapely.errors.GEOSException as e:
        raise OpError(
            f"union failed on invalid geometry ({e}); repair is not allowed by Context"
        ) from e
    total = convert(_dec(u.area), "m²", unit)
    f = num_fact(
        "area", total, ctx, unit, "", _locs(ctx, source_id, table, sel, key_column, where), note
    )
    f.claim = f"union of {len(sel):,} features covers {f.display} in {crs_label(tcrs)}"
    summed = convert(_dec(_to(sel, tcrs).geometry.area.sum()), "m²", unit)
    g = num_fact("summed_area", summed, ctx, unit, "", f.locators)
    g.claim = f"feature areas summed without dissolving overlaps: {g.display}"
    return ok("union_footprint", {}, [f, g])


@op(
    "centroid",
    "centroid of the selection, reported in the Context CRS (or crs)",
    {**SEL},
    ["source_id"],
    group="spatial",
)
def centroid(ctx, source_id, table=None, where=None, key_column=None, value=None, crs=None):
    sel, where, _ = _select(ctx, source_id, table, where, key_column, value)
    tcrs, note = target_crs(ctx, sel, crs)
    c = shapely.union_all(_to(sel, tcrs).geometry.values).centroid
    locs = _locs(ctx, source_id, table, sel, key_column, where)
    facts = [
        num_fact(
            k,
            _dec(v),
            ctx,
            None,
            f"centroid {k} = {v:.3f} in {crs_label(tcrs)}",
            locs,
            note,
            decimals=3,
        )
        for k, v in (("x", c.x), ("y", c.y))
    ]
    return ok("centroid", {}, facts)


@op(
    "height_span",
    "max Z minus min Z over the selected features' coordinates",
    {**SEL},
    ["source_id"],
    group="spatial",
)
def height_span(ctx, source_id, table=None, where=None, key_column=None, value=None, crs=None):
    df = ctx.frame(source_id, table)
    if not isinstance(df, gpd.GeoDataFrame) and {"min_z", "max_z"} <= set(df.columns):
        if key_column is not None and value is not None:
            where = [*(where or []), {"column": key_column, "op": "=", "value": val(value)}]
        sel = apply_where(df, where)
        lo = min(d for d in (to_decimal(x) for x in sel["min_z"]) if d is not None)
        hi = max(d for d in (to_decimal(x) for x in sel["max_z"]) if d is not None)
    else:
        sel, where, _ = _select(ctx, source_id, table, where, key_column, value)
        coords = shapely.get_coordinates(sel.geometry.values, include_z=True)
        zs = coords[:, 2][~np.isnan(coords[:, 2])] if len(coords) else np.array([])
        if not len(zs):
            raise OpError("the selected geometries have no Z coordinates")
        lo, hi = _dec(zs.min()), _dec(zs.max())
    locs = [Locator(source_id=source_id, kind="layer", text=f"layer {table}{where_text(where)}")]
    f = num_fact("span", hi - lo, ctx, "m", "", locs)
    f.claim = f"height span (max Z {hi} minus min Z {lo}) is {f.display}"
    return ok(
        "height_span",
        {},
        [
            f,
            num_fact("min_z", lo, ctx, "m", f"min Z {lo}", locs),
            num_fact("max_z", hi, ctx, "m", f"max Z {hi}", locs),
        ],
    )


@op(
    "reproject",
    "coordinates of the selected point feature(s) in another CRS",
    {**SEL, "to_crs": S},
    ["source_id", "to_crs"],
    group="spatial",
)
def reproject(
    ctx, source_id, to_crs, table=None, where=None, key_column=None, value=None, crs=None
):
    sel, where, _ = _select(ctx, source_id, table, where, key_column, value)
    out = _to(sel, CRS.from_user_input(to_crs))
    facts = []
    for i, g in list(zip(sel.index, out.geometry, strict=True))[:5]:
        p = g.representative_point() if g.geom_type != "Point" else g
        loc = _locs(ctx, source_id, table, sel.loc[[i]], key_column)
        facts.append(
            str_fact(
                f"xy@{i}",
                f"{p.x:.3f}, {p.y:.3f}",
                f"feature coordinates in {to_crs}: {p.x:.3f}, {p.y:.3f}",
                loc,
            )
        )
    return ok("reproject", {}, facts)

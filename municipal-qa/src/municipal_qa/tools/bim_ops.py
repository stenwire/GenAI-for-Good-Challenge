"""BIM ops (plan §6.5) on the STEP-text IFC model: footprints, extents, model-world Z."""

from __future__ import annotations

from decimal import Decimal

from municipal_qa.readers.ifc import entity_locator, load_model, space_geometry, type_name
from municipal_qa.schema import Locator
from municipal_qa.tools.calc import convert
from municipal_qa.tools.registry import OpError, loose_name, op
from municipal_qa.tools.table_ops import num_fact, ok

S = {"type": "string"}


def _find(m, entity: str, name: str) -> int:
    t = entity.upper()
    hits = [i for i in m.by_type(t) if loose_name(str(m.args(i)[2])) == loose_name(str(name))]
    if not hits:
        long = [
            i
            for i in m.by_type(t)
            if len(m.args(i)) > 7 and loose_name(str(m.args(i)[7])) == loose_name(name)
        ]
        hits = long
    if len(hits) != 1:
        names = sorted({str(m.args(i)[2]) for i in m.by_type(t)})[:60]
        why = "no" if not hits else f"{len(hits)}"
        raise OpError(f"{why} {type_name(t)} named {name!r}; names: {names}")
    return hits[0]


@op("ifc_footprint_area", "IFC space footprint area, model-world bounding rectangle and Z range",
    {"source_id": S, "name": S, "entity": S, "unit": {"type": "string", "enum": ["m²", "ha"]}},
    ["source_id", "name"], group="bim")  # fmt: skip
def ifc_footprint_area(ctx, source_id, name, entity="IfcSpace", unit="m²"):
    if ctx.profile(source_id).kind != "bim":
        raise OpError(f"{source_id} is not an IFC model")
    m = load_model(ctx.source(source_id).path)
    i = _find(m, entity, str(name))
    try:
        g = space_geometry(m, i)
    except ValueError as e:
        raise OpError(str(e)) from e
    base = entity_locator(i, m.type_of(i), m.args(i)[2])
    loc = [Locator(source_id=source_id, kind="ifc_entity", text=f"{base}, {g['source']}")]
    body = [Locator(source_id=source_id, kind="ifc_entity", text=f"{base}, Body representation")]

    def dec(x: float) -> Decimal:
        return Decimal(repr(x))

    fp = num_fact("footprint_area", convert(dec(g["footprint_m2"]), "m²", unit), ctx, unit, "", loc)
    fp.claim = f"{type_name(m.type_of(i))} {name} modelled horizontal footprint is {fp.display}"
    bb = num_fact("bbox_area", convert(dec(g["bbox_m2"]), "m²", unit), ctx, unit, "", loc)
    bb.claim = (f"model-world axis-aligned rectangle {g['bbox_w_m']:.2f} m x {g['bbox_d_m']:.2f} m "
                f"= {bb.display}")  # fmt: skip
    facts = [fp, bb]
    for k, label in (("z_min_m", "lowest"), ("z_max_m", "highest")):
        f = num_fact(k, dec(g[k]), ctx, "m", "", body)
        f.claim = f"{label} model-world Z of {name}'s body is {f.display}"
        facts.append(f)
    return ok("ifc_footprint_area", {}, facts)

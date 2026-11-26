import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.profiling.profile import ProfileStore
from municipal_qa.schema import Conventions, SourceInfo, Step
from municipal_qa.tools import registry
from municipal_qa.tools.registry import ExecContext, run_step

SPATIAL_OPS = [
    "layer_info", "reproject", "area", "length", "distance", "within_radius", "point_in_polygon",
    "intersects", "bbox_filter", "count_features", "feature_lookup", "geometry_validity",
    "union_footprint", "centroid", "height_span",
]  # fmt: skip
P, T = "SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb"


def _info(sid, path):
    return SourceInfo(source_id=sid, path=str(path), original_file=path.name, source_set="Train",
                      publisher="", licence="", bytes=path.stat().st_size, sha256=path.name,
                      suffix=path.suffix.lower())  # fmt: skip


@pytest.fixture
def make_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    # 100 m x 100 m square in British National Grid, a bowtie and two overlapping squares.
    sq = Polygon([(500000, 200000), (500100, 200000), (500100, 200100), (500000, 200100)])
    bow = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    a = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    b = Polygon([(10, 0), (30, 0), (30, 20), (10, 20)])
    polys = gpd.GeoDataFrame(
        {"reference": ["SQ", "BOW", "A", "B"]}, geometry=[sq, bow, a, b], crs=27700
    )
    polys.to_crs(4326).to_file(tmp_path / "zones.geojson", driver="GeoJSON")
    pts = gpd.GeoDataFrame(
        {"id": ["c", "on", "in", "out"]},
        geometry=[Point(1000, 1000), Point(1020, 1000), Point(1010, 1005), Point(1020.001, 1000)],
        crs=27700,
    )
    pts.to_file(tmp_path / "trees.gpkg", layer="trees")
    manifest = {P: _info(P, tmp_path / "zones.geojson"), T: _info(T, tmp_path / "trees.gpkg")}
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    store = ProfileStore(cfg, manifest)

    def ctx(**conv):
        return ExecContext(store, Conventions(raw_context="", **conv), list(manifest))

    return ctx


def run(ctx, op, **args):
    r = run_step(Step(op=op, args=args, save_as="x"), {}, ctx)
    assert r.ok, r.error
    return {f.name: f for f in r.facts}


def test_every_spatial_op_is_registered():
    ops = registry.load_all()
    assert all(ops[n].group == "spatial" for n in SPATIAL_OPS)


def test_square_reprojected_from_wgs84_is_one_hectare(make_ctx):
    ctx = make_ctx(crs="EPSG:27700", decimals_overrides={"ha": 5})
    f = run(ctx, "area", source_id=P, key_column="reference", value="SQ", unit="ha")["area"]
    assert f.display == "1.00000 ha" and "measured in EPSG:27700" in f.claim
    assert f.locators[0].text == "layer zones.geojson, feature reference=SQ"
    # without a stated CRS, a geographic layer is measured in a local UTM zone, and says so
    g = run(make_ctx(), "area", source_id=P, key_column="reference", value="SQ")["area"]
    assert "local UTM" in g.note and "EPSG:326" in g.claim


def test_invalid_geometry_measured_as_published_unless_repair_allowed(make_ctx):
    ctx = make_ctx(crs="EPSG:27700")
    f = run(ctx, "area", source_id=P, key_column="reference", value="BOW")["area"]
    assert f.value == pytest.approx(0, abs=1e-3)
    assert f.note == "1 invalid geometry measured as published, not repaired"
    fix = make_ctx(crs="EPSG:27700", repair_geometry=True)
    r = run(fix, "area", source_id=P, key_column="reference", value="BOW")["area"]
    assert r.value == pytest.approx(50, rel=1e-3) and "repaired" in r.note
    v = run(ctx, "geometry_validity", source_id=P)
    assert v["invalid"].value == 1 and "Self-intersection" in v["reason@BOW"].value


def test_within_radius_inclusive_boundary(make_ctx):
    args = {"source_id": T, "key_column": "id", "value": "c", "radius": 20}
    assert run(make_ctx(radius_inclusive=True), "within_radius", **args)["count"].value == 3
    assert run(make_ctx(radius_inclusive=False), "within_radius", **args)["count"].value == 2
    assert run(make_ctx(), "within_radius", **args, inclusive=False)["count"].value == 2
    f = run(make_ctx(radius_inclusive=True), "within_radius", **args)["count"]
    assert "boundary included (distance <= r)" in f.claim and "EPSG:27700" in f.claim


def test_union_overlap_distance_and_points(make_ctx):
    ctx = make_ctx(crs="EPSG:27700")
    w = [{"column": "reference", "op": "in", "value": ["A", "B"]}]
    u = run(ctx, "union_footprint", source_id=P, where=w)
    assert u["area"].value == pytest.approx(600, rel=1e-3) and u[
        "summed_area"
    ].value == pytest.approx(800, rel=1e-3)
    i = run(ctx, "intersects", source_id=P, key_column="reference", value="A", source_b=P,
            key_column_b="reference", value_b="B")  # fmt: skip
    assert i["overlap_area"].value == pytest.approx(200, rel=1e-3)
    d = run(ctx, "distance", source_id=T, key_column="id", value="c", source_b=T, key_column_b="id",
            value_b="on")  # fmt: skip
    assert d["distance"].display == "20.00 m"
    n = run(ctx, "count_features", source_id=T, where=[{"column": "id", "op": "!=", "value": "c"}])
    assert n["count"].value == 3
    lk = run(ctx, "feature_lookup", source_id=T, key_column="id", value="out", columns=["id"])
    assert lk["geometry_type"].value == "Point"
    info = run(ctx, "layer_info", source_id=T)
    assert info["crs"].value == "EPSG:27700" and info["features"].value == 4


def test_train_06ffb5b9_zone_area_is_3_48_ha(data_cfg):
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions
    from municipal_qa.pipeline.context import parse_conventions

    m = load_manifest(data_cfg)
    q = next(q for q in load_questions(data_cfg, "train") if q.id.startswith("06ffb5b9"))
    zones = next(s for s in q.source_ids if m[s].original_file == "zones-2026-06-23.geojson")
    ctx = ExecContext(
        ProfileStore(data_cfg, m), parse_conventions(q.context, q.question), q.source_ids
    )
    f = run(ctx, "area", source_id=zones, key_column="reference", value="1912/A1", unit="ha")[
        "area"
    ]
    assert f.display == "3.48 ha" and "EPSG:27700" in f.claim
    assert f.locators[0].text == "layer zones-2026-06-23.geojson, feature reference=1912/A1"

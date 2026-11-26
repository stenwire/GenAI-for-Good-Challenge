import geopandas as gpd
import laspy
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.profiling.profile import ProfileStore
from municipal_qa.schema import Conventions, SourceInfo, Step
from municipal_qa.tools import registry
from municipal_qa.tools.registry import ExecContext, run_step

RASTER_OPS = [
    "raster_info", "raster_stats", "raster_pair_diff", "pointcloud_info", "pointcloud_class_counts",
    "pointcloud_z_stats",
]  # fmt: skip
DSM, DTM, SHIFTED, LC, PC, POLY = (f"SRC_{c * 12}" for c in "abcdef")


def _info(sid, path):
    return SourceInfo(source_id=sid, path=str(path), original_file=path.name, source_set="Train",
                      publisher="", licence="", bytes=path.stat().st_size, sha256=path.name,
                      suffix=path.suffix.lower())  # fmt: skip


def _tif(path, data, transform, crs="EPSG:27700", nodata=-9999.0):
    h, w = data.shape
    meta = {"driver": "GTiff", "width": w, "height": h, "count": 1, "dtype": data.dtype}
    with rasterio.open(path, "w", **meta, crs=crs, transform=transform, nodata=nodata) as dst:
        dst.write(data, 1)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    t = from_origin(1000, 2000, 0.5, 0.5)
    dsm = np.array([[10, 11, -9999], [12, 5, 8]], dtype="float32")
    dtm = np.array([[9, 12, 3], [-9999, 6, 7]], dtype="float32")
    _tif(tmp_path / "surface.tif", dsm, t)
    _tif(tmp_path / "terrain.tif", dtm, t)
    _tif(tmp_path / "shifted.tif", dtm, from_origin(1000.5, 2000, 0.5, 0.5))
    lc = np.array([[95, 10], [95, 0]], dtype="uint8")
    _tif(
        tmp_path / "cover.tif",
        lc,
        from_origin(30.0, 0.001, 0.001, 0.001),
        crs="EPSG:4326",
        nodata=0,
    )
    hdr = laspy.LasHeader(point_format=1, version="1.2")
    hdr.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(hdr)
    las.x, las.y = np.array([0.0, 10, 20, 30]), np.array([0.0, 5, 10, 20])
    las.z = np.array([1.0, 2.0, 30.0, -4.0])
    las.classification = np.array([2, 2, 5, 7], dtype=np.uint8)
    las.write(tmp_path / "survey.laz")
    gpd.GeoDataFrame(
        {"name": ["left"]}, geometry=[box(1000, 1999, 1000.6, 2000)], crs=27700
    ).to_file(tmp_path / "areas.geojson", driver="GeoJSON")
    names = [
        "surface.tif",
        "terrain.tif",
        "shifted.tif",
        "cover.tif",
        "survey.laz",
        "areas.geojson",
    ]
    ids = [DSM, DTM, SHIFTED, LC, PC, POLY]
    manifest = {s: _info(s, tmp_path / n) for s, n in zip(ids, names, strict=True)}
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    return ExecContext(ProfileStore(cfg, manifest), Conventions(raw_context=""), ids)


def run(ctx, op, **args):
    r = run_step(Step(op=op, args=args, save_as="x"), {}, ctx)
    assert r.ok, r.error
    return {f.name: f for f in r.facts}


def test_every_raster_op_is_registered():
    ops = registry.load_all()
    assert all(ops[n].group == "raster" for n in RASTER_OPS)


def test_dsm_minus_dtm_over_valid_pairs_only(ctx):
    cond = {"op": "<", "value": 0}
    r = run(ctx, "raster_pair_diff", source_a=DSM, source_b=DTM, condition=cond)
    # pairs valid in both: (10,9) (11,12) (5,6) (8,7) -> diffs 1, -1, -1, 1
    assert r["valid_pairs"].value == 4 and r["count"].value == 2
    assert r["share"].display == "50.00%" and r["mean"].value == 0
    assert r["area"].value == 0.5  # 2 cells x 0.25 m²
    assert r["count"].locators[0].text == "band 1, valid pairs"
    step = Step(op="raster_pair_diff", args={"source_a": DSM, "source_b": SHIFTED}, save_as="x")
    bad = run_step(step, {}, ctx)
    assert not bad.ok and "not aligned" in bad.error


def test_raster_stats_nodata_condition_and_polygon_mask(ctx):
    s = run(ctx, "raster_stats", source_id=DSM, condition={"op": ">=", "value": 10})
    assert s["valid_pixels"].value == 5 and s["max"].value == 12 and s["count"].value == 3
    assert s["valid_area"].value == 1.25
    m = run(ctx, "raster_stats", source_id=DSM, mask_source=POLY)
    # polygon spans x 1000..1000.6: only column 0 (centre x 1000.25) has its centre inside
    assert (
        m["valid_pixels"].value == 2
        and "inside 1 polygon(s) of areas.geojson" in m["valid_pixels"].claim
    )


def test_geographic_raster_class_area_is_geodesic(ctx):
    r = run(ctx, "raster_stats", source_id=LC, classes=[95], unit="ha")
    assert r["valid_pixels"].value == 3 and r["class_95.count"].value == 2
    # a 0.001 degree cell at the equator is about 110.57 m x 111.32 m = 1.2309 ha; two cells
    assert r["class_95.area"].value == pytest.approx(2 * 1.2309, rel=1e-3)
    assert r["class_95.share"].display == "66.67%"


def test_pointcloud_counts_z_stats_and_header(ctx):
    c = run(ctx, "pointcloud_class_counts", source_id=PC)
    assert (c["total"].value, c["class_2"].value, c["class_5"].value) == (4, 2, 1)
    assert c["class_2"].note == "share 50.00%"
    assert c["class_7"].locators[0].text == "classification 7, all points"
    z = run(ctx, "pointcloud_z_stats", source_id=PC, classes=[2, 5])
    assert (z["count"].value, z["min_z"].value, z["max_z"].value) == (3, 1, 30) and z[
        "mean_z"
    ].value == 11
    info = run(ctx, "pointcloud_info", source_id=PC)
    assert info["points"].value == 4 and info["extent_area"].value == 600  # 30 x 20
    none = run(ctx, "pointcloud_class_counts", source_id=PC, classes=[21])
    assert none["class_21"].value == 0

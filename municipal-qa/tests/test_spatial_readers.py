import json

import geopandas as gpd
import laspy
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point

import municipal_qa.readers  # noqa: F401
from municipal_qa.readers import ifc, pointcloud, raster, vector
from municipal_qa.readers.base import get_reader
from municipal_qa.schema import SourceInfo


def _src(path, sid="SRC_aaaaaaaaaaaa"):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=path.name,
        source_set="Train",
        publisher="p",
        licence="l",
        bytes=path.stat().st_size,
        sha256="0" * 64,
        suffix=path.suffix.lower(),
    )


BOWTIE = [[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]


def test_geojson_keeps_invalid_geometry_crs_and_feature_locators(tmp_path):
    fc = {
        "type": "FeatureCollection",
        "name": "zones",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"reference": "Z1", "kind": "[z]"},
                "geometry": {"type": "Polygon", "coordinates": [BOWTIE]},
            },
            {
                "type": "Feature",
                "properties": {"reference": "Z2", "kind": "area"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]],
                },
            },
        ],
    }
    path = tmp_path / "zones.geojson"
    path.write_text(json.dumps(fc))
    src = _src(path)
    reader = get_reader(src)
    assert reader.name == "vector"
    prof = reader.profile(src)
    t = prof.tables[0]
    assert (t.table_name, t.n_rows, t.crs, t.geometry_type) == (
        "zones.geojson",
        2,
        "EPSG:27700",
        "Polygon",
    )
    assert "1 invalid geometries, kept as published" in prof.warnings[0]
    gdf = reader.load_table(src, "zones.geojson")
    assert not gdf.geometry.iloc[0].is_valid
    assert gdf.geometry.iloc[0].area == 0.0  # bowtie measured as published, not repaired
    assert gdf.loc[1, "kind"] == "area" and gdf.loc[0, "kind"] == "[z]"
    locs = [p.locator.text for p in reader.passages(src) if p.locator.kind == "feature"]
    assert locs == [
        "layer zones.geojson, feature reference=Z1",
        "layer zones.geojson, feature reference=Z2",
    ]


def test_gpkg_lists_every_layer_and_warns_on_missing_crs(tmp_path):
    path = tmp_path / "two.gpkg"
    gpd.GeoDataFrame(
        {"gisid": ["007", "008"]}, geometry=[Point(1, 2), Point(3, 4)], crs=27700
    ).to_file(path, layer="parks")
    gpd.GeoDataFrame({"name": ["a"]}, geometry=[Point(0, 0)]).to_file(path, layer="nocrs")
    src = _src(path)
    prof = get_reader(src).profile(src)
    assert [(t.table_name, t.n_rows, t.crs) for t in prof.tables] == [
        ("parks", 2, "EPSG:27700"),
        ("nocrs", 1, None),
    ]
    assert "layer nocrs: no CRS recorded in the file" in prof.warnings
    parks = get_reader(src).load_table(src, "parks")
    assert parks["gisid"].tolist() == ["007", "008"]
    with pytest.raises(Exception, match="layers"):
        get_reader(src).load_table(src)


CITYGML = """<?xml version="1.0"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml">
 <core:cityObjectMember><bldg:Building gml:id="B1">
  <bldg:measuredHeight uom="m">12.5</bldg:measuredHeight>
  <gml:posList srsDimension="3">0 0 10 1 0 10 1 1 22.5 0 0 10</gml:posList>
 </bldg:Building></core:cityObjectMember>
 <core:cityObjectMember><bldg:Building gml:id="B2">
  <gml:pos srsDimension="3">5 5 3</gml:pos><gml:pos srsDimension="3">5 6 7</gml:pos>
 </bldg:Building></core:cityObjectMember>
</core:CityModel>"""


def test_citygml_buildings_heights_and_z_range(tmp_path):
    path = tmp_path / "city.gml"
    path.write_text(CITYGML)
    src = _src(path)
    df = get_reader(src).load_table(src)
    recs = df.to_dict("records")
    assert recs[0] == {
        "gml_id": "B1",
        "measuredHeight": "12.5",
        "min_z": 10.0,
        "max_z": 22.5,
        "n_coords": 4,
    }
    assert recs[1]["gml_id"] == "B2" and (recs[1]["min_z"], recs[1]["max_z"]) == (3.0, 7.0)
    assert df["measuredHeight"].isna().tolist() == [False, True]
    assert get_reader(src).profile(src).tables[0].table_name == "Building"


def test_raster_masks_nodata_and_nan(tmp_path):
    path = tmp_path / "dsm.tif"
    data = np.array([[1.0, -9999.0], [np.nan, 4.5]], dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:27700",
        transform=from_origin(100, 200, 0.5, 0.5),
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
        dst.update_tags(legend="10 Tree cover")
    src = _src(path)
    band = raster.read_band(path)
    assert band.count() == 2 and float(band.sum()) == 5.5
    prof = get_reader(src).profile(src)
    assert (
        prof.kind == "raster"
        and "nodata -9999.0, CRS EPSG:27700, pixel size 0.5 x 0.5" in prof.summary
    )
    assert "legend: 10 Tree cover" in prof.summary
    locs = [p.locator.text for p in get_reader(src).passages(src)]
    assert locs == ["raster header", "tag legend"]


def test_pointcloud_chunked_class_counts_match_full_read(tmp_path):
    path = tmp_path / "pc.laz"
    hdr = laspy.LasHeader(point_format=1, version="1.2")
    hdr.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(hdr)
    n = 1000
    las.x = np.arange(n) * 0.1
    las.y = np.arange(n) * 0.2
    las.z = np.linspace(-5, 20, n)
    las.classification = np.array([2] * 600 + [5] * 390 + [21] * 10, dtype=np.uint8)
    las.write(path)
    counts = {}
    for chunk in pointcloud.iter_chunks(path, size=137):
        for c in np.asarray(chunk.classification).tolist():
            counts[c] = counts.get(c, 0) + 1
    assert counts == pointcloud.class_counts(path) == {2: 600, 5: 390, 21: 10}
    h = pointcloud.header(path)
    assert h["points"] == 1000 and h["mins"][2] == pytest.approx(-5) and h["crs"] is None
    prof = get_reader(_src(path)).profile(_src(path))
    assert "classification counts 2: 600, 5: 390, 21: 10" in prof.summary
    assert "no CRS recorded in the LAS header" in prof.warnings


IFC = """ISO-10303-21;
HEADER;FILE_SCHEMA(('IFC2X3'));ENDSEC;
DATA;
#1= IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#10= IFCBUILDINGSTOREY('g1',#2,'1.korrus',$,$,#3,$,$,.ELEMENT.,0.);
#20= IFCSPACE('g2',#2,'119',$,$,#3,#4,'KORIDOR',.ELEMENT.,.INTERNAL.,$);
#21= IFCSPACE('g3',#2,'3-1',$,$,#3,#4,'ELUTUBA+K\\X2\\00D600D6\\X0\\K',
  .ELEMENT.,.INTERNAL.,$);
#30= IFCPROPERTYSINGLEVALUE('Heated',$,IFCBOOLEAN(.T.),$);
#31= IFCPROPERTYSINGLEVALUE('Area',$,IFCAREAMEASURE(63.8),$);
#32= IFCPROPERTYSINGLEVALUE('Label',$,IFCLABEL('it''s'),$);
#33= IFCPROPERTYSET('p1',#2,'AR_Ruum',$,(#30,#31,#32));
#40= IFCQUANTITYAREA('NetFloorArea','',$,61.25);
#41= IFCELEMENTQUANTITY('q1',#2,'BaseQuantities',$,$,(#40));
#50= IFCRELDEFINESBYPROPERTIES('r1',#2,$,$,(#20,#21),#33);
#51= IFCRELDEFINESBYPROPERTIES('r2',#2,$,$,(#20),#41);
ENDSEC;
END-ISO-10303-21;
"""


def test_ifc_step_parser_spaces_properties_and_locators(tmp_path):
    path = tmp_path / "m.ifc"
    path.write_text(IFC, encoding="latin-1")
    src = _src(path)
    m = ifc.load_model(path)
    assert m.schema == "IFC2X3" and len(m.by_type("IfcSpace")) == 2
    df = get_reader(src).load_table(src, "IfcSpace")
    r119 = df[df.Name == "119"].iloc[0]
    assert r119["LongName"] == "KORIDOR" and r119["step_id"] == "#20"
    assert r119["AR_Ruum.Heated"] == "T" and r119["AR_Ruum.Area"] == 63.8
    assert r119["AR_Ruum.Label"] == "it's" and r119["BaseQuantities.NetFloorArea"] == 61.25
    assert df[df.Name == "3-1"].iloc[0]["LongName"] == "ELUTUBA+KÖÖK"
    storeys = get_reader(src).load_table(src, "IfcBuildingStorey")
    assert storeys.iloc[0]["Elevation"] == 0.0
    prof = get_reader(src).profile(src)
    assert prof.kind == "bim" and "LENGTHUNIT=MILLIMETRE" in prof.summary
    locs = [p.locator.text for p in get_reader(src).passages(src)]
    assert "#20 IfcSpace Name=119" in locs and "#10 IfcBuildingStorey Name=1.korrus" in locs
    assert ifc.parse_args("1.E-05,(#3,$),.F.,'a''b'") == [1e-05, [3, None], "F", "a'b"]


def test_real_spatial_sources_all_have_readers(data_cfg):
    from municipal_qa.data.manifest import load_manifest

    m = load_manifest(data_cfg)
    kinds = {
        ".geojson": "vector",
        ".gpkg": "vector",
        ".gml": "vector",
        ".tif": "raster",
        ".laz": "pointcloud",
        ".ifc": "bim",
    }
    seen = 0
    for s in m.values():
        if s.suffix in kinds:
            prof = get_reader(s).profile(s)
            assert prof.kind == kinds[s.suffix], s.original_file
            if prof.kind == "vector":
                assert all(t.crs for t in prof.tables), s.original_file
            seen += 1
    assert seen == 29
    trees = next(s for s in m.values() if s.original_file == "trees.gpkg")
    t = get_reader(trees).profile(trees).tables[0]
    assert (t.table_name, t.n_rows, t.crs, t.columns[0].name) == (
        "ezytreevcurrenttpotrees",
        1571,
        "EPSG:27700",
        "reference",
    )
    zones = next(s for s in m.values() if s.original_file == "zones-2026-06-23.geojson")
    gdf = vector.load_layer(zones)
    assert len(gdf) == 727 and "1912/A1" in set(gdf["reference"])

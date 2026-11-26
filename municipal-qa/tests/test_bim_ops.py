import pytest

from municipal_qa.config import load_config
from municipal_qa.profiling.profile import ProfileStore
from municipal_qa.readers.ifc import length_scale, load_model, placement_matrix, space_geometry
from municipal_qa.schema import Conventions, SourceInfo, Step
from municipal_qa.tools.registry import ExecContext, run_step

# A 4 m x 2 m room (mm units) on a storey 3 m up, on a site at Z 10 m rotated by 90 degrees.
IFC = """ISO-10303-21;
HEADER;FILE_SCHEMA(('IFC2X3'));ENDSEC;
DATA;
#1= IFCPROJECT('p',$,'P',$,$,$,$,$,#2);
#2= IFCUNITASSIGNMENT((#3,#4));
#3= IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#4= IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#10= IFCCARTESIANPOINT((1000.,2000.,10000.));
#11= IFCDIRECTION((0.,0.,1.));
#12= IFCDIRECTION((0.,1.,0.));
#13= IFCAXIS2PLACEMENT3D(#10,#11,#12);
#14= IFCLOCALPLACEMENT($,#13);
#20= IFCCARTESIANPOINT((0.,0.,3000.));
#21= IFCAXIS2PLACEMENT3D(#20,$,$);
#22= IFCLOCALPLACEMENT(#14,#21);
#30= IFCCARTESIANPOINT((0.,0.));
#31= IFCCARTESIANPOINT((4000.,0.));
#32= IFCCARTESIANPOINT((4000.,2000.));
#33= IFCCARTESIANPOINT((0.,2000.));
#34= IFCPOLYLINE((#30,#31,#32,#33,#30));
#35= IFCGEOMETRICCURVESET((#34));
#36= IFCSHAPEREPRESENTATION(#99,'FootPrint','GeometricCurveSet',(#35));
#40= IFCCARTESIANPOINT((0.,0.,0.));
#41= IFCCARTESIANPOINT((4000.,0.,0.));
#42= IFCCARTESIANPOINT((4000.,2000.,2500.));
#43= IFCPOLYLOOP((#40,#41,#42));
#44= IFCFACEOUTERBOUND(#43,.T.);
#45= IFCFACE((#44));
#46= IFCCLOSEDSHELL((#45));
#47= IFCFACETEDBREP(#46);
#48= IFCSHAPEREPRESENTATION(#99,'Body','Brep',(#47));
#50= IFCPRODUCTDEFINITIONSHAPE($,$,(#48,#36));
#60= IFCSPACE('g',$,'101',$,$,#22,#50,'KORIDOR',.ELEMENT.,.INTERNAL.,$);
ENDSEC;
END-ISO-10303-21;
"""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    path = tmp_path / "model.ifc"
    path.write_text(IFC, encoding="latin-1")
    sid = "SRC_aaaaaaaaaaaa"
    info = SourceInfo(source_id=sid, path=str(path), original_file=path.name, source_set="Train",
                      publisher="", licence="", bytes=1, sha256="x", suffix=".ifc")  # fmt: skip
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    return ExecContext(ProfileStore(cfg, {sid: info}), Conventions(raw_context=""), [sid]), path


def test_placement_chain_units_and_space_geometry(ctx):
    _, path = ctx
    m = load_model(path)
    assert length_scale(m) == 0.001
    mat = placement_matrix(m, 22)
    # local x axis maps to world +y (rotation by 90 degrees); origin at (1000, 2000, 13000) mm
    assert mat[:3, 3].tolist() == [1000.0, 2000.0, 13000.0]
    assert mat[:3, 0].tolist() == pytest.approx([0, 1, 0])
    g = space_geometry(m, 60)
    assert g["footprint_m2"] == pytest.approx(8.0)
    assert (g["bbox_w_m"], g["bbox_d_m"]) == pytest.approx((2.0, 4.0))  # rotated in world axes
    assert (g["z_min_m"], g["z_max_m"]) == pytest.approx((13.0, 15.5))


def test_ifc_footprint_area_op_facts_and_locators(ctx):
    c, _ = ctx
    r = run_step(Step(op="ifc_footprint_area", args={"source_id": c.source_ids[0], "name": "101"},
                      save_as="x"), {}, c)  # fmt: skip
    assert r.ok, r.error
    f = {x.name: x for x in r.facts}
    assert f["footprint_area"].display == "8.00 m²" and f["bbox_area"].display == "8.00 m²"
    assert f["z_min_m"].display == "13.00 m"
    assert f["footprint_area"].locators[0].text == "#60 IfcSpace Name=101, FootPrint representation"
    by_long = run_step(Step(op="ifc_footprint_area", args={"source_id": c.source_ids[0],
                            "name": "koridor"}, save_as="y"), {}, c)  # fmt: skip
    assert by_long.ok
    bad = run_step(Step(op="ifc_footprint_area", args={"source_id": c.source_ids[0], "name": "9"},
                        save_as="z"), {}, c)  # fmt: skip
    assert not bad.ok and "names: ['101']" in bad.error

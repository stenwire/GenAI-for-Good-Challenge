import json
import re

import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.data.manifest import load_manifest
from municipal_qa.readers import structured
from municipal_qa.readers.base import UnsupportedFormat, get_reader
from municipal_qa.readers.structured import StructuredReader, pointer
from municipal_qa.schema import SourceInfo


def _src(path, sid="SRC_dddddddddddd"):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=path.name,
        source_set="Test",
        publisher="",
        licence="",
        bytes=path.stat().st_size,
        sha256="",
        suffix=path.suffix.lower(),
    )


def _by_loc(path):
    return {p.locator.text: p for p in get_reader(_src(path)).passages(_src(path))}


def test_pointer_escapes():
    assert pointer(["result", "a/b", "x~y", 3]) == "/result/a~1b/x~0y/3"


def test_record_list_table_and_passages(tmp_path):
    p = tmp_path / "records.json"
    p.write_text(
        json.dumps(
            [
                {
                    "ID": "007",
                    "Age": 12,
                    "Condition": "[z]",
                    "site": {"name": "Park", "zone": "A1"},
                },
                {
                    "ID": "010",
                    "Age": None,
                    "Condition": "..",
                    "site": {"name": "Road", "zone": "B2"},
                },
            ]
        )
    )
    reader = get_reader(_src(p))
    assert reader.name == "structured"
    df = reader.load_table(_src(p))
    assert list(df.columns) == ["ID", "Age", "Condition", "site.name", "site.zone"]
    assert df.loc[0, "ID"] == "007" and df.loc[1, "Condition"] == ".."
    t = reader.profile(_src(p)).tables[0]
    cols = {c.name: c for c in t.columns}
    assert t.n_rows == 2 and cols["ID"].dtype == "str" and cols["Age"].n_null == 1
    by = _by_loc(p)
    assert (
        by["/"].text == "records.json: 2 records; fields: ID, Age, Condition, site.name, site.zone"
    )
    assert "Condition: [z]" in by["/0"].text and by["/1/site"].text == "name: Road\nzone: B2"


def test_metadata_walk_pointers_and_dictionary_kind(tmp_path):
    p = tmp_path / "DatasetMetadata.json"
    p.write_text(
        json.dumps(
            {
                "result": {"title": "Trees", "notes": "Long description " * 6},
                "fields": [{"name": "pflanzjahr", "label": "Planting year", "type": "int"}],
                "omitted_pages": [1, 7],
            }
        )
    )
    by = _by_loc(p)
    assert by["/result"].text == "title: Trees" and by["/result"].kind == "metadata"
    assert by["/result/notes"].text.startswith("notes: Long description")
    field = by["/fields/0"]
    assert field.kind == "dictionary" and "name: pflanzjahr" in field.text
    assert by["/omitted_pages"].text == "omitted_pages: 1, 7"
    with pytest.raises(UnsupportedFormat):
        StructuredReader().load_table(_src(p))


def test_geojson_properties_only_with_warning(tmp_path):
    p = tmp_path / "zones.json"
    p.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "zones",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"reference": "1912/A1"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                        },
                    },
                ],
            }
        )
    )
    prof = StructuredReader().profile(_src(p))
    assert "1 features" in prof.summary and "Polygon" in prof.summary
    assert any("vector reader" in w for w in prof.warnings)
    by = _by_loc(p)
    assert by["/features"].text == "zones.json: 1 features; properties: reference"
    assert by["/features/0/properties"].text == "reference: 1912/A1"


def test_xml_paths_attributes_and_no_entity_expansion(tmp_path):
    p = tmp_path / "SurveyMetadata.xml"
    p.write_text(
        '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        "<metadata><idinfo><title>Survey 2021</title><leak>&x;</leak>"
        '<extent units="m" epsg="2926"/></idinfo></metadata>'
    )
    by = _by_loc(p)
    assert by["/metadata/idinfo/title"].text == "title: Survey 2021"
    assert by["/metadata/idinfo/extent"].text == "extent: @units: m | @epsg: 2926"
    assert "root:" not in " ".join(x.text for x in by.values())


def test_large_xml_summarised(tmp_path, monkeypatch):
    p = tmp_path / "NeTEx.xml"
    p.write_text("<Delivery>" + "<StopPlace><Name>S</Name></StopPlace>" * 30 + "</Delivery>")
    monkeypatch.setattr(structured, "LARGE_XML_ELEMENTS", 20)
    ps = StructuredReader().passages(_src(p))
    assert len(ps) == 1 and ps[0].locator.text == "/Delivery"
    assert "StopPlace 30" in ps[0].text and "Name 30" in ps[0].text


def test_xsd_field_definitions(tmp_path):
    p = tmp_path / "trees.schema.xsd"
    p.write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="Tree">'
        '<xs:complexType><xs:sequence><xs:element name="pflanzjahr" type="xs:int" minOccurs="0">'
        "<xs:annotation><xs:documentation>Pflanzjahr des Baumes</xs:documentation>"
        "</xs:annotation></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>"
    )
    by = _by_loc(p)
    f = by["//xs:element[@name='pflanzjahr']"]
    assert f.kind == "dictionary"
    assert f.text == "element pflanzjahr, type xs:int, minOccurs 0: Pflanzjahr des Baumes"


LOCATOR = re.compile(r"^(/[^\n]*|//xs:(element|attribute)\[@name='[^']+'\])$")


def test_every_real_structured_source_yields_valid_locators(data_cfg):
    manifest = load_manifest(data_cfg)
    srcs = [s for s in manifest.values() if s.suffix in (".json", ".xml", ".xsd")]
    assert len(srcs) == 31 + 6 + 2
    for src in srcs:
        ps = get_reader(src).passages(src)
        assert ps, src.original_file
        for p in ps:
            assert LOCATOR.match(p.locator.text), (src.original_file, p.locator.text)
            assert p.locator.kind in ("json_pointer", "xpath") and p.text.strip()
    pagemap = next(s for s in srcs if s.original_file == "PageMap.json")
    texts = " ".join(p.text for p in get_reader(pagemap).passages(pagemap))
    assert "excerpt_physical_page: 1" in texts and "original_physical_page: 2" in texts

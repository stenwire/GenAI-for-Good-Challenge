import re

import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.data.manifest import load_manifest
from municipal_qa.readers.base import UnsupportedFormat, get_reader
from municipal_qa.readers.text import TextReader, split_lines
from municipal_qa.schema import SourceInfo


def _src(path, sid="SRC_cccccccccccc"):
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


def test_pdf_physical_pages_and_low_text_warning(docs_dir):
    src = _src(docs_dir / "report.pdf")
    reader = get_reader(src)
    assert reader.name == "text"
    ps = reader.passages(src)
    assert [p.locator.text for p in ps] == ["page 1", "page 2", "page 3"]
    assert ps[0].text == "Results: share was 12.5 percent [z]."
    assert ps[0].locator.kind == "page" and ps[0].kind == "body"
    prof = reader.profile(src)
    assert prof.kind == "document" and "3 pages" in prof.summary
    assert prof.warnings == ["page 3: under 30 characters, possibly scanned"]


def test_markdown_sections_lines_and_doc_kind(docs_dir):
    by = _by_loc(docs_dir / "SourceGuide.md")
    assert set(by) == {
        'section "Source guide", lines 3-4',
        'section "Analysis conventions", lines 8-10',
    }
    conv = by['section "Analysis conventions", lines 8-10']
    assert conv.kind == "doc" and conv.locator.kind == "section"
    assert conv.text == "Analysis conventions\nCounts mean records.\n\n[z] means not applicable."


def test_plain_text_setext_headings_and_banners(docs_dir):
    by = _by_loc(docs_dir / "Readme.txt")
    assert (
        by['section "Bike data", lines 5-6'].text == "Bike data\nIntro line one.\nIntro line two."
    )
    assert 'section "Field notes", line 11' in by
    assert by['section "Field notes", line 11'].kind == "dictionary"


def test_split_lines_without_headings():
    blocks = split_lines("one\ntwo\n\nthree\n")
    assert [(b.text, b.section, b.start, b.end) for b in blocks] == [
        ("one\ntwo", None, 1, 2),
        ("three", None, 4, 4),
    ]


def test_html_drops_scripts_nav_cookies_footer(docs_dir):
    ps = get_reader(_src(docs_dir / "page.html")).passages(_src(docs_dir / "page.html"))
    text = " ".join(p.text for p in ps)
    for gone in ("var x", "cookies", "Menu", "Footer text"):
        assert gone not in text
    by = {p.locator.text: p for p in ps}
    assert by['section "Background", paragraph 1'].text == "Background\nUrban centres were mapped."
    results = by['section "Results", paragraphs 2-4']
    assert "Zone | Minutes" in results.text and "A1 | 12" in results.text


def test_docx_headings_paragraphs_tables(docs_dir):
    by = _by_loc(docs_dir / "guide.docx")
    ((loc, p),) = by.items()
    assert loc == 'section "Historical data", paragraphs 1-4'
    assert "Severity is adjusted." in p.text and "2005 | STATS19" in p.text


def test_no_table_interface_and_files_untouched(docs_dir):
    before = {f: f.read_bytes() for f in docs_dir.iterdir()}
    for f in before:
        TextReader().passages(_src(f))
        TextReader().profile(_src(f))
    assert {f: f.read_bytes() for f in docs_dir.iterdir()} == before
    with pytest.raises(UnsupportedFormat):
        TextReader().load_table(_src(docs_dir / "report.pdf"))


LOCATOR = re.compile(
    r'^(page \d+(, table \d+)?|(section ".+", )?(lines? \d+(-\d+)?|paragraphs? \d+(-\d+)?))$'
)


def test_every_real_document_yields_valid_locators(data_cfg):
    manifest = load_manifest(data_cfg)
    docs = [
        s
        for s in manifest.values()
        if s.suffix in (".pdf", ".md", ".html", ".docx", ".txt") and get_reader(s).name == "text"
    ]
    assert len(docs) == 8 + 16 + 5 + 2 + 22
    for src in docs:
        ps = get_reader(src).passages(src)
        assert ps, src.original_file
        for p in ps:
            assert LOCATOR.match(p.locator.text), (src.original_file, p.locator.text)
            assert p.locator.source_id == src.source_id and p.text.strip()


def test_real_pdf_pages_are_physical(data_cfg):
    src = next(
        s
        for s in load_manifest(data_cfg).values()
        if s.original_file == "PVGIS_Status_Report_2024.pdf"
    )
    pages = {p.locator.text for p in get_reader(src).passages(src) if "table" not in p.locator.text}
    assert pages == {f"page {n}" for n in range(1, 34)}

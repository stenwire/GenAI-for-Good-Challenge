"""Documents → passages with page, section or line locators (plan §7.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    is_documentation_name,
    register,
)
from municipal_qa.schema import Passage, SourceInfo, SourceProfile

MAX_CHARS = 1200
LOW_TEXT_CHARS = 30
DICTIONARY_HINT = re.compile(r"dictionar|codebook|schema|field|variable|definition", re.I)
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT = re.compile(r"^(=+|-+)\s*$")


@dataclass
class Block:
    text: str
    section: str | None
    start: int  # 1-based line, or paragraph index for docx/html
    end: int


def _kind(src: SourceInfo, section: str | None = None) -> str:
    if DICTIONARY_HINT.search(src.original_file) or (section and DICTIONARY_HINT.search(section)):
        return "dictionary"
    return "doc" if is_documentation_name(src.original_file) else "body"


def _read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes any bytes")


def split_lines(text: str) -> list[Block]:
    """Markdown/plain text → blocks per heading, then per paragraph, with 1-based line ranges."""
    lines = text.splitlines()
    rules = {i for i, ln in enumerate(lines, 1) if _SETEXT.match(ln)}
    setext = {
        i
        for i, ln in enumerate(lines, 1)
        if ln.strip()
        and (i + 1) in rules
        and len(ln) <= 120
        and (i == 1 or not lines[i - 2].strip() or (i - 1) in rules)
    }
    blocks: list[Block] = []
    section: str | None = None
    para: list[tuple[int, str]] = []

    def flush() -> None:
        if para:
            blocks.append(Block("\n".join(t for _, t in para), section, para[0][0], para[-1][0]))
            para.clear()

    for i, line in enumerate(lines, 1):
        m = _MD_HEADING.match(line)
        if m or i in setext:
            flush()
            section = (m.group(2) if m else line).strip()
        elif i in rules or not line.strip():
            flush()
        else:
            para.append((i, line))
    flush()
    return blocks


def _pack(blocks: list[Block]) -> list[Block]:
    """Merge neighbouring paragraphs of one section up to MAX_CHARS; split oversize ones."""
    out: list[Block] = []
    for b in blocks:
        last = out[-1] if out else None
        if last and last.section == b.section and len(last.text) + len(b.text) + 2 <= MAX_CHARS:
            out[-1] = Block(last.text + "\n\n" + b.text, b.section, last.start, b.end)
        elif len(b.text) > MAX_CHARS:
            for k in range(0, len(b.text), MAX_CHARS):
                out.append(Block(b.text[k : k + MAX_CHARS], b.section, b.start, b.end))
        else:
            out.append(b)
    return out


def _section_loc(section: str | None, start: int, end: int, unit: str) -> str:
    rng = f"{unit} {start}" if start == end else f"{unit}s {start}-{end}"
    return f'section "{section}", {rng}' if section else rng


# ---------- per-format block extraction ----------


def pdf_pages(path: str | Path) -> tuple[list[str], list[list[list[list[str]]]], list[str]]:
    """(page texts, tables per page, warnings); page i is physical page i + 1."""
    return _pdf_pages(str(Path(path).resolve()))


@lru_cache(maxsize=8)
def _pdf_pages(path: str) -> tuple[list[str], list[list[list[list[str]]]], list[str]]:
    import pdfplumber

    texts, tables, warnings = [], [], []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            texts.append(text)
            if len(text.strip()) < LOW_TEXT_CHARS:
                warnings.append(f"page {n}: under {LOW_TEXT_CHARS} characters, possibly scanned")
            try:
                found = page.extract_tables() or []
            except Exception as e:  # noqa: BLE001
                warnings.append(f"page {n}: table extraction failed ({type(e).__name__})")
                found = []
            tables.append(
                [[[("" if c is None else str(c)) for c in row] for row in t] for t in found]
            )
    return texts, tables, warnings


def html_blocks(path: str | Path) -> list[Block]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_read_text(path), "lxml")
    for tag in soup(["script", "style", "noscript", "template", "nav", "header", "footer"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"id": re.compile("cookie", re.I)}):
        tag.decompose()
    for tag in soup.find_all(class_=re.compile("cookie", re.I)):
        tag.decompose()
    blocks: list[Block] = []
    section: str | None = None
    n = 0
    body = soup.body or soup
    for el in body.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "pre", "dd", "dt"]
    ):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            section = text
            continue
        if el.find_parent(["li", "tr", "pre"]) is not None and el.name != "tr":
            continue
        if el.name == "tr":
            text = " | ".join(
                " ".join(c.get_text(" ", strip=True).split()) for c in el.find_all(["td", "th"])
            )
        n += 1
        blocks.append(Block(text, section, n, n))
    return blocks


def docx_blocks(path: str | Path) -> list[Block]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    blocks: list[Block] = []
    section: str | None = None
    n = 0
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, d)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "") if para.style is not None else ""
            if style.lower().startswith(("heading", "title")):
                section = text
                continue
            n += 1
            blocks.append(Block(text, section, n, n))
        elif tag == "tbl":
            for row in Table(child, d).rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    n += 1
                    blocks.append(Block(" | ".join(cells), section, n, n))
    return blocks


class TextReader:
    name = "text"
    suffixes = (".pdf", ".html", ".htm", ".docx", ".md", ".txt")

    def _blocks(self, src: SourceInfo) -> tuple[list[Block], str]:
        suffix = src.suffix.lower()
        if suffix in (".html", ".htm"):
            return _pack(html_blocks(src.path)), "paragraph"
        if suffix == ".docx":
            return _pack(docx_blocks(src.path)), "paragraph"
        return _pack(split_lines(_read_text(src.path))), "line"

    def profile(self, src: SourceInfo) -> SourceProfile:
        warnings: list[str] = []
        if src.suffix.lower() == ".pdf":
            texts, tables, warnings = pdf_pages(src.path)
            first = next((ln.strip() for t in texts for ln in t.splitlines() if ln.strip()), "")
            n_tables = sum(len(t) for t in tables)
            summary = (
                f"{src.original_file}: PDF, {len(texts)} pages, {n_tables} tables. "
                f"Starts: {first[:200]}"
            )
        else:
            blocks, _ = self._blocks(src)
            if src.suffix.lower() in (".html", ".htm"):
                warnings.append("page navigation, header, footer and cookie banners dropped")
            heads = list(dict.fromkeys(b.section for b in blocks if b.section))
            first = blocks[0].text.splitlines()[0][:200] if blocks else ""
            summary = (
                f"{src.original_file}: document, {len(blocks)} passages. "
                f"Headings: {'; '.join(heads[:25]) or 'none'}. Starts: {first}"
            )
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="document",
            summary=summary[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=warnings,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        if src.suffix.lower() == ".pdf":
            texts, tables, _ = pdf_pages(src.path)
            for n, text in enumerate(texts, 1):
                for k in range(0, max(len(text), 1), MAX_CHARS):
                    pb.add(text[k : k + MAX_CHARS], _kind(src), "page", f"page {n}")
                for t_i, table in enumerate(tables[n - 1], 1):
                    rows = [" | ".join(r) for r in table if any(c.strip() for c in r)]
                    pb.add(
                        "\n".join(rows)[: MAX_CHARS * 2],
                        _kind(src),
                        "page",
                        f"page {n}, table {t_i}",
                    )
            return pb.items
        blocks, unit = self._blocks(src)
        for b in blocks:
            text = f"{b.section}\n{b.text}" if b.section else b.text
            pb.add(
                text,
                _kind(src, b.section),
                "section" if b.section else "lines",
                _section_loc(b.section, b.start, b.end, unit),
            )
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        raise UnsupportedFormat(f"{src.original_file}: a document has no table interface")


register(TextReader())

"""Baseline retrieval: per-source passages cached on disk, BM25 within a question's sources."""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib import metadata
from pathlib import Path

import bm25s

from municipal_qa.readers.base import UnsupportedFormat, get_reader, is_documentation_name
from municipal_qa.schema import Locator, Passage, SourceInfo

DOC_KINDS = {"doc", "dictionary", "notes_sheet", "metadata"}
CACHE_VERSION = 3
_PKG = Path(__file__).resolve().parents[1]
READER_LIBS = [
    "beautifulsoup4",
    "duckdb",
    "geopandas",
    "laspy",
    "lazrs",
    "lxml",
    "openpyxl",
    "pandas",
    "pdfminer.six",
    "pdfplumber",
    "pillow",
    "pyogrio",
    "pyproj",
    "python-docx",
    "rasterio",
    "shapely",
]


def _version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"


@cache
def code_fingerprint() -> str:
    """Hash of reader code, the passage schema and reader library versions."""
    h = hashlib.sha256(f"v{CACHE_VERSION}".encode())
    files = [*(_PKG / "readers").glob("*.py"), _PKG / "retrieval" / "bm25.py", _PKG / "schema.py"]
    for f in sorted(files, key=lambda p: p.relative_to(_PKG).as_posix()):
        h.update(f.relative_to(_PKG).as_posix().encode() + b"\0" + f.read_bytes())
    for name in READER_LIBS:
        h.update(f"{name}=={_version(name)}".encode())
    return h.hexdigest()[:16]


def _fallback_passage(src: SourceInfo, reason: str) -> Passage:
    text = (
        f"{src.original_file}: {src.suffix} file from {src.publisher or 'the publisher'}"
        f" ({src.bytes:,} bytes). Content not extracted: {reason}."
    )
    return Passage(
        passage_id=f"{src.source_id}:0",
        source_id=src.source_id,
        text=text,
        locator=Locator(source_id=src.source_id, kind="file", text="file"),
        kind="metadata",
    )


class PassageStore:
    """Reads each source once per process; a JSON cache under `cache_dir` survives restarts."""

    def __init__(self, manifest: dict[str, SourceInfo], cache_dir: Path | None = None):
        self.manifest = manifest
        self.cache_dir = cache_dir
        self._mem: dict[str, list[Passage]] = {}
        self.errors: dict[str, str] = {}

    def _cache_path(self, sid: str) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(f"{self.manifest[sid].sha256}:{code_fingerprint()}".encode())
        return self.cache_dir / f"{sid}.{key.hexdigest()[:16]}.json"

    def get(self, sid: str) -> list[Passage]:
        if sid in self._mem:
            return self._mem[sid]
        path = self._cache_path(sid)
        if path and path.exists():
            items = [Passage.model_validate(p) for p in json.loads(path.read_text("utf-8"))]
        else:
            items = self._read(sid)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps([p.model_dump() for p in items]), "utf-8")
        self._mem[sid] = items
        return items

    def _read(self, sid: str) -> list[Passage]:
        src = self.manifest[sid]
        try:
            items = get_reader(src).passages(src)
        except UnsupportedFormat:
            return [_fallback_passage(src, f"no reader for {src.suffix}")]
        except Exception as e:  # noqa: BLE001
            self.errors[sid] = f"{type(e).__name__}: {e}"
            return [_fallback_passage(src, "the reader failed")]
        return items or [_fallback_passage(src, "the reader found no text")]


_TOKEN = re.compile(r"\[\w{1,5}\]|\w\w+", re.UNICODE)  # keeps markers such as [z], [r]


def _tokens(texts: list[str]) -> list[list[str]]:
    return [_TOKEN.findall(t.lower()) or ["_"] for t in texts]


def is_doc(p: Passage, manifest: dict[str, SourceInfo]) -> bool:
    src = manifest.get(p.source_id)
    return p.kind in DOC_KINDS or bool(src and is_documentation_name(src.original_file))


def search(
    store: PassageStore,
    query: str,
    source_ids: list[str],
    k: int = 8,
    docs_first: int = 3,
) -> list[Passage]:
    """Top-k BM25 passages within `source_ids`; up to `docs_first` documentation hits lead."""
    corpus = [p for sid in source_ids if sid in store.manifest for p in store.get(sid)]
    if not corpus:
        return []
    retriever = bm25s.BM25()
    retriever.index(_tokens([p.text for p in corpus]), show_progress=False)
    idx, scores = retriever.retrieve(_tokens([query]), k=len(corpus), show_progress=False)
    ranked = [
        corpus[i].model_copy(update={"score": float(s)})
        for i, s in zip(idx[0], scores[0], strict=True)
    ]
    hits = [p for p in ranked if (p.score or 0) > 0] or ranked
    docs = [p for p in hits if is_doc(p, store.manifest)][:docs_first]
    rest = [p for p in hits if p not in docs]
    return (docs + rest)[:k]

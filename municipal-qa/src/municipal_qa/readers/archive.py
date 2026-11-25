"""Nested .zip (members read by their own readers) and .jpg (metadata only), plan §7.7."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd

from municipal_qa.config import PROJECT_ROOT
from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    get_reader,
    is_documentation_name,
    register,
)
from municipal_qa.schema import Passage, SourceInfo, SourceProfile

# Extracted copies only; the source zip is never written.
EXTRACT_ROOT = PROJECT_ROOT / "artifacts" / "extracted"


def _safe(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in name


def extract(src: SourceInfo, root: Path | None = None) -> dict[str, Path]:
    """member name → extracted path, under <root>/<source_id>/ (reused when already there)."""
    dest = (root or EXTRACT_ROOT) / src.source_id
    out: dict[str, Path] = {}
    with zipfile.ZipFile(src.path) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        bad = [i.filename for i in members if not _safe(i.filename)]
        if bad:
            raise ValueError(f"unsafe archive members refused: {bad[:3]}")
        for info in members:
            target = dest.joinpath(*PurePosixPath(info.filename).parts)
            if not target.exists() or target.stat().st_size != info.file_size:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
            out[info.filename] = target
    return out


def member_source(src: SourceInfo, name: str, path: Path) -> SourceInfo:
    return src.model_copy(
        update={
            "path": str(path),
            "original_file": name,
            "suffix": PurePosixPath(name).suffix.lower(),
            "bytes": path.stat().st_size,
        }
    )


class ArchiveReader:
    name = "archive"
    suffixes = (".zip",)

    def __init__(self, root: Path | None = None):
        self.root = root

    def _members(self, src: SourceInfo) -> list[tuple[str, SourceInfo]]:
        return [(n, member_source(src, n, p)) for n, p in extract(src, self.root).items()]

    def profile(self, src: SourceInfo) -> SourceProfile:
        tables, warnings, names = [], [], []
        for name, msrc in self._members(src):
            names.append(name)
            try:
                prof = get_reader(msrc).profile(msrc)
            except UnsupportedFormat:
                warnings.append(f"member {name}: no reader for its format")
                continue
            for t in prof.tables:
                tables.append(t.model_copy(update={"table_name": f"{name}"}))
            warnings += [f"member {name}: {w}" for w in prof.warnings]
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="table" if tables else "binary",
            tables=tables,
            summary=(
                f"{src.original_file}: zip archive, {len(names)} members: " + ", ".join(names)
            )[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=warnings,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        members = self._members(src)
        pb.add(
            f"{src.original_file} contains: " + ", ".join(n for n, _ in members),
            "metadata",
            "file",
            "archive member list",
        )
        for name, msrc in members:
            try:
                inner = get_reader(msrc).passages(msrc)
            except UnsupportedFormat:
                continue
            for p in inner:
                pb.add(p.text, p.kind, p.locator.kind, f"member {name}, {p.locator.text}")
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        members = dict(self._members(src))
        if table not in members:
            raise KeyError(f"no member {table!r}; members: {list(members)}")
        msrc = members[table]
        return get_reader(msrc).load_table(msrc)


class ImageReader:
    name = "image"
    suffixes = (".jpg", ".jpeg", ".png")

    @staticmethod
    def _meta(src: SourceInfo) -> dict[str, str]:
        from PIL import ExifTags, Image

        with Image.open(src.path) as im:
            meta = {"format": str(im.format), "width": str(im.width), "height": str(im.height)}
            exif = im.getexif()
        for tag, value in exif.items():
            name = ExifTags.TAGS.get(tag, str(tag))
            if isinstance(value, str | int | float) and len(str(value)) <= 200:
                meta[name] = str(value).strip("\x00 ")
        return meta

    def profile(self, src: SourceInfo) -> SourceProfile:
        meta = self._meta(src)
        text = ", ".join(f"{k}: {v}" for k, v in meta.items())
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="binary",
            summary=f"{src.original_file}: image ({text})"[:1200],
            is_documentation=False,
            warnings=["image content is not read; answer from accompanying text"],
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        meta = self._meta(src)
        pb.add(
            f"Image {src.original_file}: " + ", ".join(f"{k}: {v}" for k, v in meta.items()),
            "metadata",
            "file",
            "image metadata",
        )
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        raise UnsupportedFormat(f"{src.original_file}: an image has no table")


register(ArchiveReader())
register(ImageReader())

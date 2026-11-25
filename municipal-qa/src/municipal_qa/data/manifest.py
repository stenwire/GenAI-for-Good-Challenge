from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd

from municipal_qa.config import Config
from municipal_qa.schema import SourceInfo

CHUNK = 1 << 20
ARCHIVE = "SourceMaterials.zip"


class DataSetupError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        head = "\n".join(problems[:20])
        more = f"\n... and {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"{len(problems)} data problem(s):\n{head}{more}")


def _text(v) -> str:
    return "" if pd.isna(v) else str(v)


def load_manifest(cfg: Config) -> dict[str, SourceInfo]:
    df = pd.read_csv(cfg.data_dir / "SourceManifest.csv", dtype=str, keep_default_na=False)
    out: dict[str, SourceInfo] = {}
    for r in df.itertuples(index=False):
        path = cfg.data_dir / r.File
        out[r.Source_ID] = SourceInfo(
            source_id=r.Source_ID,
            path=str(path),
            original_file=r.Original_File,
            source_set=r.Source_Set,
            publisher=_text(r.Publisher),
            licence=_text(r.Licence),
            bytes=int(r.Bytes),
            sha256=r.SHA256.lower(),
            suffix=PurePosixPath(r.Original_File).suffix.lower(),
        )
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return (
        name.startswith("sources/")
        and ".." not in p.parts
        and "\\" not in name
        and not p.is_absolute()
    )


def extract_sources(zip_path: Path, data_dir: Path) -> int:
    """Extract into a staging folder, then rename, so a partial extract is never `sources/`."""
    staging = data_dir / "sources.partial"
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(zip_path) as zf:
        bad = [i.filename for i in zf.infolist() if not _safe_member(i.filename)]
        if bad:
            raise DataSetupError([f"unsafe archive member refused: {n!r}" for n in bad])
        n = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = PurePosixPath(info.filename).relative_to("sources")
            target = staging.joinpath(*rel.parts)
            if not target.resolve().is_relative_to(staging.resolve()):
                raise DataSetupError([f"unsafe archive member refused: {info.filename!r}"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            n += 1
    staging.rename(data_dir / "sources")
    return n


def verify_sources(manifest: dict[str, SourceInfo], verify_hashes: bool = True) -> list[str]:
    problems: list[str] = []
    for sid, info in manifest.items():
        p = Path(info.path)
        if not p.is_file():
            problems.append(f"{sid}: missing file {p.name}")
            continue
        size = p.stat().st_size
        if size != info.bytes:
            problems.append(f"{sid}: size {size} != manifest {info.bytes}")
            continue
        if verify_hashes and sha256_file(p) != info.sha256:
            problems.append(f"{sid}: SHA256 mismatch")
    return problems


def setup_data(cfg: Config, verify_hashes: bool = True) -> None:
    if not (cfg.sources_dir).exists():
        zip_path = cfg.data_dir / ARCHIVE
        if not zip_path.exists():
            raise DataSetupError([f"{ARCHIVE} not found in {cfg.data_dir}"])
        extract_sources(zip_path, cfg.data_dir)
    problems = verify_sources(load_manifest(cfg), verify_hashes)
    if problems:
        raise DataSetupError(problems)

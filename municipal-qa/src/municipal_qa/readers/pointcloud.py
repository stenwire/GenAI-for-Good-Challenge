"""LAS/LAZ point clouds through laspy + lazrs, read in chunks (plan §7.7)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import laspy
import numpy as np

from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    is_documentation_name,
    register,
)
from municipal_qa.readers.vector import crs_label
from municipal_qa.schema import Passage, SourceInfo, SourceProfile

CHUNK = 2_000_000


def iter_chunks(path: str | Path, size: int = CHUNK) -> Iterator[laspy.ScaleAwarePointRecord]:
    with laspy.open(path) as f:
        yield from f.chunk_iterator(size)


@lru_cache(maxsize=8)
def _header(path: str) -> dict:
    with laspy.open(path) as f:
        h = f.header
        try:
            crs = crs_label(h.parse_crs())
        except Exception:  # noqa: BLE001
            crs = None
        return {
            "points": int(h.point_count),
            "version": str(h.version),
            "point_format": int(h.point_format.id),
            "mins": [float(x) for x in h.mins],
            "maxs": [float(x) for x in h.maxs],
            "scales": [float(x) for x in h.scales],
            "crs": crs,
            "vlrs": [f"{v.user_id}:{v.record_id}" for v in h.vlrs],
        }


def header(path: str | Path) -> dict:
    return _header(str(Path(path).resolve()))


@lru_cache(maxsize=8)
def _class_counts(path: str) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for chunk in iter_chunks(path):
        vals, n = np.unique(np.asarray(chunk.classification), return_counts=True)
        counts.update(dict(zip(vals.tolist(), n.tolist(), strict=True)))
    return dict(sorted(counts.items()))


def class_counts(path: str | Path) -> dict[int, int]:
    return _class_counts(str(Path(path).resolve()))


def _summary(name: str, h: dict, classes: dict[int, int]) -> str:
    cls = ", ".join(f"{k}: {v:,}" for k, v in classes.items())
    return (
        f"{name}: LAS {h['version']} point format {h['point_format']}, {h['points']:,} points, "
        f"x {h['mins'][0]:g}..{h['maxs'][0]:g}, y {h['mins'][1]:g}..{h['maxs'][1]:g}, "
        f"z {h['mins'][2]:g}..{h['maxs'][2]:g} (header extrema), CRS {h['crs']}; "
        f"classification counts {cls}"
    )


class PointCloudReader:
    name = "pointcloud"
    suffixes = (".laz", ".las")

    def profile(self, src: SourceInfo) -> SourceProfile:
        h = header(src.path)
        warns = [] if h["crs"] else ["no CRS recorded in the LAS header"]
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="pointcloud",
            summary=_summary(src.original_file, h, class_counts(src.path))[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=warns,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        h = header(src.path)
        pb = PassageBuilder(src.source_id)
        pb.add(
            _summary(src.original_file, h, class_counts(src.path)),
            "metadata",
            "pointcloud",
            "header and classification counts",
        )
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None):
        raise UnsupportedFormat(f"{src.original_file}: point cloud has no table interface")


register(PointCloudReader())

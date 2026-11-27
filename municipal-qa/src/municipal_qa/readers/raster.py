"""GeoTIFF rasters through rasterio, nodata and NaN masked (plan §7.7)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio

from municipal_qa.readers.base import (
    PassageBuilder,
    UnsupportedFormat,
    is_documentation_name,
    register,
)
from municipal_qa.readers.vector import crs_label
from municipal_qa.schema import Passage, SourceInfo, SourceProfile


def raster_meta(path: str | Path) -> dict:
    with rasterio.open(path) as r:
        return {
            "width": r.width,
            "height": r.height,
            "bands": r.count,
            "dtype": r.dtypes[0],
            "nodata": r.nodata,
            "crs": crs_label(r.crs),
            "res": [float(x) for x in r.res],
            "bounds": [float(b) for b in r.bounds],
            "transform": list(r.transform)[:6],
            "units": [u for u in r.units if u],
            "tags": {k: str(v) for k, v in r.tags().items()},
        }


@lru_cache(maxsize=4)
def _band(path: str, band: int) -> np.ma.MaskedArray:
    with rasterio.open(path) as r:
        arr = r.read(band, masked=True)
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.ma.masked_invalid(arr)
    return arr


def read_band(path: str | Path, band: int = 1) -> np.ma.MaskedArray:
    """Masked where the file's nodata applies, and at NaN for float rasters."""
    return _band(str(Path(path).resolve()), band)


def _summary(name: str, m: dict) -> str:
    units = f", units {', '.join(m['units'])}" if m["units"] else ""
    return (
        f"{name}: raster {m['width']}x{m['height']} px, {m['bands']} band(s), {m['dtype']}, "
        f"nodata {m['nodata']}, CRS {m['crs']}, pixel size {m['res'][0]:g} x {m['res'][1]:g}"
        f"{units}, bounds {[round(b, 6) for b in m['bounds']]}"
    )


class RasterReader:
    name = "raster"
    suffixes = (".tif", ".tiff")

    def profile(self, src: SourceInfo) -> SourceProfile:
        m = raster_meta(src.path)
        warns = [] if m["crs"] else ["no CRS recorded in the raster"]
        if m["nodata"] is None:
            warns.append("no nodata value recorded; only NaN cells are masked")
        tags = "; ".join(f"{k}: {v}" for k, v in m["tags"].items() if k != "AREA_OR_POINT")
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="raster",
            summary=(_summary(src.original_file, m) + (f". Tags: {tags}" if tags else ""))[:1200],
            is_documentation=is_documentation_name(src.original_file),
            warnings=warns,
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        m = raster_meta(src.path)
        pb = PassageBuilder(src.source_id)
        pb.add(_summary(src.original_file, m), "metadata", "raster", "raster header")
        for k, v in m["tags"].items():
            if k != "AREA_OR_POINT":
                pb.add(f"{src.original_file} tag {k}: {v}", "metadata", "raster", f"tag {k}")
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None):
        raise UnsupportedFormat(f"{src.original_file}: raster has no table interface")


register(RasterReader())

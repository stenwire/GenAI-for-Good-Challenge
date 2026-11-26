"""Format readers; importing this package registers every reader."""

# Order matters where suffixes are shared: delimited claims tabular .txt before text,
# vector claims GeoJSON-with-geometry .json before structured.
# isort: off
from municipal_qa.readers import (  # noqa: F401
    archive,
    delimited,
    ifc,
    pointcloud,
    raster,
    spreadsheet,
    sqlite,
    vector,
    structured,
    text,
)
# isort: on

"""Format readers; importing this package registers every reader."""

# Order matters where suffixes are shared: delimited claims tabular .txt before text.
from municipal_qa.readers import archive, delimited, spreadsheet, sqlite, text  # noqa: F401

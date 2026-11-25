"""Reader protocol and suffix registry (plan §7.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from municipal_qa.schema import Locator, Passage, SourceInfo, SourceProfile

if TYPE_CHECKING:
    import pandas as pd

DOC_NAME_HINTS = (
    "readme",
    "guide",
    "dictionary",
    "codebook",
    "metadata",
    "methods",
    "method",
    "notes",
    "derivation",
    "definitions",
    "legend",
)


class UnsupportedFormat(Exception): ...


@runtime_checkable
class Reader(Protocol):
    name: str
    suffixes: tuple[str, ...]

    def profile(self, src: SourceInfo) -> SourceProfile: ...

    def passages(self, src: SourceInfo) -> list[Passage]: ...

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame: ...


_REGISTRY: dict[str, Reader] = {}


def register(reader: Reader) -> Reader:
    for suffix in reader.suffixes:
        _REGISTRY[suffix.lower()] = reader
    return reader


def get_reader(src: SourceInfo) -> Reader:
    try:
        return _REGISTRY[src.suffix.lower()]
    except KeyError:
        raise UnsupportedFormat(f"no reader for {src.suffix!r} ({src.source_id})") from None


def registered_suffixes() -> list[str]:
    return sorted(_REGISTRY)


def is_documentation_name(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in DOC_NAME_HINTS)


class PassageBuilder:
    """Numbers passages `<source_id>:<n>` in emission order."""

    def __init__(self, source_id: str):
        self.source_id = source_id
        self.items: list[Passage] = []

    def add(self, text: str, kind: str, locator_kind: str, locator_text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.items.append(
            Passage(
                passage_id=f"{self.source_id}:{len(self.items)}",
                source_id=self.source_id,
                text=text,
                locator=Locator(source_id=self.source_id, kind=locator_kind, text=locator_text),
                kind=kind,
            )
        )

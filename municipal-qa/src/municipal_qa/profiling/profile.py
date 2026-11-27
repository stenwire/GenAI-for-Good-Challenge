"""Source profiles (plan §6.4, card M4): built once per source, cached as JSON."""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from pathlib import Path

from municipal_qa.config import Config
from municipal_qa.readers.base import UnsupportedFormat, get_reader, is_documentation_name
from municipal_qa.retrieval.bm25 import code_fingerprint
from municipal_qa.schema import SourceInfo, SourceProfile, TableProfile

# (max tables, max columns, examples per column, example chars, summary chars, warnings)
DETAIL_LEVELS = [
    (6, 25, 3, 30, 300, 2),
    (4, 15, 2, 24, 200, 1),
    (3, 10, 1, 20, 150, 1),
    (2, 6, 0, 0, 100, 0),
]


@cache
def _profile_code() -> str:
    own = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    return f"{code_fingerprint()}:{own}"


def cache_key(src: SourceInfo) -> str:
    return hashlib.sha256(f"{src.sha256}:{_profile_code()}".encode()).hexdigest()[:16]


def fallback_profile(src: SourceInfo, reason: str) -> SourceProfile:
    return SourceProfile(
        source=src,
        reader="none",
        kind="binary",
        summary=f"{src.original_file}: {src.suffix} file, {src.bytes:,} bytes. {reason}.",
        is_documentation=is_documentation_name(src.original_file),
        warnings=[reason],
    )


def build_profile(src: SourceInfo) -> SourceProfile:
    """Never raises: an unreadable source becomes a `binary` profile with a warning."""
    import municipal_qa.readers  # noqa: F401  registers readers in worker processes

    try:
        prof = get_reader(src).profile(src)
    except UnsupportedFormat:
        return fallback_profile(src, f"no reader for {src.suffix} yet")
    except Exception as e:  # noqa: BLE001
        return fallback_profile(src, f"reader failed: {type(e).__name__}: {str(e)[:200]}")
    if not prof.is_documentation and is_documentation_name(src.original_file):
        prof = prof.model_copy(update={"is_documentation": True})
    return prof


def _build_and_save(src: SourceInfo, path: Path) -> str:
    prof = build_profile(src)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(prof.model_dump_json(), "utf-8")
    tmp.replace(path)
    return src.source_id


class ProfileStore:
    def __init__(self, cfg: Config, manifest: dict[str, SourceInfo]):
        self.cfg = cfg
        self.manifest = manifest
        self.cache_dir = cfg.artifacts_dir / "profiles"
        self._mem: dict[str, SourceProfile] = {}

    def path(self, source_id: str) -> Path:
        src = self.manifest[source_id]
        return self.cache_dir / f"{source_id}.{cache_key(src)}.json"

    def get(self, source_id: str) -> SourceProfile:
        if source_id in self._mem:
            return self._mem[source_id]
        path = self.path(source_id)
        if not path.exists():
            _build_and_save(self.manifest[source_id], path)
        prof = SourceProfile.model_validate_json(path.read_text("utf-8"))
        self._mem[source_id] = prof
        return prof

    def get_many(self, source_ids: list[str]) -> list[SourceProfile]:
        return [self.get(s) for s in source_ids if s in self.manifest]

    def build_all(self, workers: int = 4) -> None:
        todo = [s for s in self.manifest if not self.path(s).exists()]
        jobs = [(self.manifest[s], self.path(s)) for s in todo]
        if workers <= 1 or len(jobs) <= 1:
            for src, path in jobs:
                _build_and_save(src, path)
            return
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for fut in [pool.submit(_build_and_save, src, path) for src, path in jobs]:
                fut.result()

    def render_for_prompt(
        self, profiles: list[SourceProfile], max_chars: int = 6000, question: str | None = None
    ) -> str:
        return render_profiles(profiles, max_chars, question)


def _named_in(question: str | None, prof: SourceProfile) -> bool:
    if not question:
        return False
    q = question.lower()
    name = prof.source.original_file.lower()
    stem = Path(name).stem
    return name in q or (len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", q) is not None)


def order_profiles(profiles: list[SourceProfile], question: str | None) -> list[SourceProfile]:
    """Documentation first, then sources the question names, then the rest (stable)."""
    rank = [(0 if p.is_documentation else 1 if _named_in(question, p) else 2) for p in profiles]
    return [p for _, _, p in sorted(zip(rank, range(len(profiles)), profiles, strict=True))]


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: max(n - 1, 0)] + "…"


def _table_lines(t: TableProfile, level: tuple) -> list[str]:
    _, max_cols, n_ex, ex_chars, _, _ = level
    head = f"  table {_short(t.table_name, 60)}: {t.n_rows:,} rows"
    if t.header_row:
        head += f", header row {t.header_row}"
    if t.crs or t.geometry_type:
        head += f", {t.geometry_type or 'geometry'} in {t.crs or 'unknown CRS'}"
    lines = [head]
    for c in t.columns[:max_cols]:
        uniq = f", {c.n_unique:,} unique" if c.n_unique is not None else ""
        ex = "; ".join(_short(e, ex_chars) for e in c.examples[:n_ex])
        lines.append(
            f"    {_short(c.name, 50)} ({c.dtype}, {c.n_null:,} null{uniq}"
            + (f", e.g. {ex})" if ex else ")")
        )
    if len(t.columns) > max_cols:
        lines.append(f"    … {len(t.columns) - max_cols} more columns")
    return lines


def _block(p: SourceProfile, level: tuple | None) -> str:
    n = len(p.tables)
    head = f"[{p.source.source_id}] {p.source.original_file} · {p.kind}"
    if n:
        head += f" · {n} table{'s' if n != 1 else ''}"
    if p.is_documentation:
        head += " · DOCUMENTATION"
    if level is None:
        return head
    max_tables, _, _, _, sum_chars, n_warn = level
    lines = [head]
    for t in p.tables[:max_tables]:
        lines += _table_lines(t, level)
    if n > max_tables:
        lines.append(f"  … {n - max_tables} more tables")
    if not p.tables or p.kind in ("document", "structured", "binary"):
        lines.append(f"  {_short(p.summary, sum_chars)}")
    warns = [w for w in p.warnings if w not in p.summary][:n_warn]
    lines += [f"  warning: {_short(w, 160)}" for w in warns]
    return "\n".join(lines)


def render_profiles(
    profiles: list[SourceProfile], max_chars: int = 6000, question: str | None = None
) -> str:
    """Compact profile text for prompts; detail shrinks until it fits `max_chars`."""
    ordered = order_profiles(profiles, question)
    for level in [*DETAIL_LEVELS, None]:
        text = "\n".join(_block(p, level) for p in ordered)
        if len(text) <= max_chars:
            return text
    out: list[str] = []
    for i, p in enumerate(ordered):
        more = f"… {len(ordered) - i} more sources not shown"
        line = _block(p, None)
        if len("\n".join([*out, line, more])) > max_chars:
            out.append(more)
            break
        out.append(line)
    return "\n".join(out)[:max_chars]


def profile_summary(store: ProfileStore) -> dict:
    kinds: dict[str, int] = {}
    fallbacks = []
    for sid in store.manifest:
        p = store.get(sid)
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
        if p.reader == "none":
            fallbacks.append((sid, p.source.original_file, p.warnings[0]))
    return {"kinds": dict(sorted(kinds.items())), "fallbacks": fallbacks}

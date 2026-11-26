"""Hybrid retrieval (card T2): BM25 + dense encoder vectors fused by reciprocal rank."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from functools import cache
from pathlib import Path

import bm25s
import numpy as np

from municipal_qa.config import Config
from municipal_qa.retrieval.bm25 import PassageStore, _tokens, code_fingerprint, is_doc
from municipal_qa.retrieval.chunker import chunk
from municipal_qa.schema import Locator, Passage, SourceProfile

MAX_COLUMN_PASSAGES = 300
_EXACT = re.compile(r"\[\w{1,5}\]|\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b")


def _exact_terms(query: str) -> set[str]:
    """Publisher markers such as [z] and field-like names such as made_date, matched verbatim."""
    return {m.lower() for m in _EXACT.findall(query)}


EMBED_CHARS = 1500
_DIR = Path(__file__).resolve().parent


@cache
def _index_code() -> str:
    """What decides the indexed texts: passages, chunking, column passages, embed text."""
    from municipal_qa.profiling.profile import _profile_code

    h = hashlib.sha256(f"{code_fingerprint()}:{_profile_code()}".encode())
    h.update((_DIR / "chunker.py").read_bytes())
    for fn in (column_passages, Retriever._embed_text):
        h.update(inspect.getsource(fn).encode())
    return h.hexdigest()[:16]


def column_passages(prof: SourceProfile) -> list[Passage]:
    """One schema passage per table column, so field-name questions retrieve schema facts."""
    sid, name, out = prof.source.source_id, prof.source.original_file, []
    word = {"workbook": "sheet", "vector": "layer", "database": "table"}.get(prof.kind, "table")
    for t in prof.tables:
        where = name if t.table_name == name else f"{name} {word} {t.table_name}"
        for c in t.columns:
            if len(out) >= MAX_COLUMN_PASSAGES:
                return out
            uniq = f", {c.n_unique:,} unique" if c.n_unique is not None else ""
            ex = "; ".join(e[:40] for e in c.examples[:4])
            text = f"Column {c.name} in {where}: {c.dtype}, {c.n_null:,} null of {t.n_rows:,}"
            text += uniq + (f", e.g. {ex}" if ex else "")
            loc = (f"{word} {t.table_name}, column {c.name}" if t.table_name != name
                   else f"column {c.name}")  # fmt: skip
            out.append(Passage(passage_id=f"{sid}:col{len(out)}", source_id=sid, text=text,
                               locator=Locator(source_id=sid, kind="column", text=loc),
                               kind="header"))  # fmt: skip
    return out


class Retriever:
    def __init__(
        self, cfg: Config, profile_store, client=None, passages: PassageStore | None = None
    ):
        self.cfg = cfg
        self.profiles = profile_store
        self.manifest = profile_store.manifest
        self.client = client
        self.store = passages or PassageStore(self.manifest, cfg.artifacts_dir / "passages")
        self.dir = cfg.artifacts_dir / "index"
        self._items: dict[str, list[Passage]] = {}
        self._vecs: dict[str, np.ndarray | None] = {}
        self._bm25: dict[tuple, tuple] = {}
        self.dense_error: str | None = None

    def key(self, sid: str) -> str:
        r = self.cfg.retrieval
        parts = [self.manifest[sid].sha256, _index_code(), r.embedding_model, r.chunk_chars]
        raw = ":".join(map(str, [*parts, r.chunk_overlap]))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def items(self, sid: str) -> list[Passage]:
        if sid not in self._items:
            r = self.cfg.retrieval
            base = chunk(self.store.get(sid), r.chunk_chars, r.chunk_overlap)
            self._items[sid] = base + column_passages(self.profiles.get(sid))
        return self._items[sid]

    def _path(self, sid: str) -> Path:
        return self.dir / f"{sid}.{self.key(sid)}.npy"

    def _embed_text(self, sid: str, p: Passage) -> str:
        return f"{self.manifest[sid].original_file}: {p.text}"[:EMBED_CHARS]

    def vectors(self, sid: str, build: bool = False) -> np.ndarray | None:
        if sid in self._vecs and (self._vecs[sid] is not None or not build):
            return self._vecs[sid]
        path, vecs = self._path(sid), None
        if path.exists():
            vecs = np.load(path)
        elif build and self.client is not None and self.cfg.retrieval.embedding_model:
            items = self.items(sid)
            raw = np.asarray(
                self.client.embed([self._embed_text(sid, p) for p in items]), "float32"
            )
            vecs = raw / np.maximum(np.linalg.norm(raw, axis=1, keepdims=True), 1e-12)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npy")
            np.save(tmp, vecs)
            tmp.replace(path)
        if vecs is not None and len(vecs) != len(self.items(sid)):
            vecs = None
        self._vecs[sid] = vecs
        return vecs

    def build(self, source_ids: list[str] | None = None, progress=None) -> dict:
        sids = source_ids or list(self.manifest)
        n = 0
        for i, sid in enumerate(sids, 1):
            self.vectors(sid, build=True)
            n += len(self.items(sid))
            if progress:
                progress(i, len(sids), sid)
        meta = {"sources": len(sids), "passages": n, "model": self.cfg.retrieval.embedding_model}
        (self.dir / "index_manifest.json").write_text(json.dumps(meta, indent=2))
        return meta

    def _corpus(self, source_ids: tuple[str, ...]):
        if source_ids not in self._bm25:
            corpus = [p for sid in source_ids for p in self.items(sid)]
            texts = [f"{self.manifest[p.source_id].original_file} {p.text}" for p in corpus]
            bm = bm25s.BM25()
            bm.index(_tokens(texts), show_progress=False)
            vecs = [self.vectors(sid) for sid in source_ids]
            mat = np.vstack(vecs) if vecs and all(v is not None for v in vecs) else None
            if len(self._bm25) > 64:
                self._bm25.pop(next(iter(self._bm25)))
            self._bm25[source_ids] = (corpus, bm, mat)
        return self._bm25[source_ids]

    def _query_vector(self, query: str) -> np.ndarray | None:
        if self.client is None or not self.cfg.retrieval.embedding_model:
            return None
        try:
            q = np.asarray(self.client.embed([query])[0], "float32")
        except Exception as e:  # noqa: BLE001
            self.dense_error = f"{type(e).__name__}: {e}"
            return None
        return q / max(float(np.linalg.norm(q)), 1e-12)

    def _lead(self, corpus, order, docs, n):
        """Best passage of up to 2 guide files first, then other documentation, then the rest."""
        guides, seen = [], set()
        for i in order:
            sid = corpus[i].source_id
            if (
                sid not in seen
                and self.profiles.get(sid).is_documentation
                and len(guides) < min(2, n)
            ):
                guides.append(i)
                seen.add(sid)
        lead = guides + [i for i in order if docs[i] and i not in guides][: max(n - len(guides), 0)]
        return lead + [i for i in order if i not in set(lead)]

    def search(self, query: str, source_ids: list[str], k: int = 8, kinds: list[str] | None = None,
               qtype: str | None = None) -> list[Passage]:  # fmt: skip
        """BM25 and dense rankings merged by RRF; documentation leads, more so for semantics."""
        sids = tuple(s for s in dict.fromkeys(source_ids) if s in self.manifest)
        if not sids:
            return []
        corpus, bm, mat = self._corpus(sids)
        if not corpus:
            return []
        r = self.cfg.retrieval
        allowed = np.array([kinds is None or p.kind in kinds for p in corpus])
        fused = np.zeros(len(corpus))
        idx, sc = bm.retrieve(_tokens([query]), k=len(corpus), show_progress=False)
        rank = 0
        for i, s in zip(idx[0], sc[0], strict=True):
            if s > 0 and allowed[i] and rank < r.k_bm25:
                rank += 1
                fused[i] += 1 / (r.rrf_k + rank)
        q = self._query_vector(query) if mat is not None else None
        if q is not None:
            sims = np.where(allowed, mat @ q, -np.inf)
            for rank, i in enumerate(np.argsort(-sims)[: r.k_dense], 1):
                fused[i] += 1 / (r.rrf_k + rank)
        exact = _exact_terms(query)
        if exact:
            has = np.array([any(e in p.text.lower() for e in exact) for p in corpus])
            fused = np.where(has & allowed, fused + 0.5 / (r.rrf_k + 1), fused)
        docs = np.array([is_doc(p, self.manifest) for p in corpus])
        if qtype == "semantics":
            fused = np.where(docs & (fused > 0), fused * 1.25, fused)
        order = [int(i) for i in np.argsort(-fused, kind="stable") if fused[i] > 0]
        if not order:
            order = [i for i in range(len(corpus)) if allowed[i]][:k]
        ranked = self._lead(corpus, order, docs, r.docs_first)
        picked, per = [], {}
        for i in ranked:
            sid = corpus[i].source_id
            if per.get(sid, 0) < r.max_per_source:
                picked.append(i)
                per[sid] = per.get(sid, 0) + 1
            if len(picked) == k:
                break
        if len(picked) < k:
            picked += [i for i in ranked if i not in set(picked)][: k - len(picked)]
        return [corpus[i].model_copy(update={"score": float(fused[i])}) for i in picked]

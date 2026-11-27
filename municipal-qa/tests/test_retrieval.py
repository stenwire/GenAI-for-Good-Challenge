import numpy as np
import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.profiling.profile import ProfileStore
from municipal_qa.retrieval.chunker import chunk
from municipal_qa.retrieval.index import Retriever, column_passages
from municipal_qa.schema import Conventions, Locator, Passage, SourceInfo, Step
from municipal_qa.tools.registry import ExecContext, run_step

GUIDE, DATA, OTHER = "SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb", "SRC_cccccccccccc"
TOPICS = {"tree": 0, "baum": 0, "pflanzjahr": 0, "planting": 0, "energy": 1, "bus": 2, "[z]": 3}


class FakeEncoder:
    """Topic vectors: German and English words for the same idea share a dimension."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("encoder down")
        out = []
        for t in texts:
            v = np.full(5, 0.01)
            for w, d in TOPICS.items():
                v[d] += t.lower().count(w)
            out.append(v.tolist())
        return out


def _info(sid, path):
    return SourceInfo(source_id=sid, path=str(path), original_file=path.name, source_set="Train",
                      publisher="", licence="", bytes=path.stat().st_size, sha256=path.name,
                      suffix=path.suffix.lower())  # fmt: skip


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    (tmp_path / "SourceGuide.md").write_text(
        "# Fields\n\nPflanzjahr ist das Jahr der Pflanzung des Baumes.\n\n"
        "# Markers\n\nShorthand: [z] = not applicable, not zero.\n\n"
        "# Buses\n\nBus journeys are counted per year ending March.\n"
    )
    (tmp_path / "trees.csv").write_text("baum_id,pflanzjahr,height\nB1,1985,12\nB2,1990,[z]\n")
    (tmp_path / "energy.md").write_text("# Energy\n\nEnergy use in GWh by sector.\n")
    names = {GUIDE: "SourceGuide.md", DATA: "trees.csv", OTHER: "energy.md"}
    manifest = {s: _info(s, tmp_path / n) for s, n in names.items()}
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    return cfg, ProfileStore(cfg, manifest)


def test_chunker_keeps_locator_and_overlaps():
    loc = Locator(source_id=GUIDE, kind="page", text="page 3")
    text = " ".join(f"Sentence {i} is here." for i in range(120))
    p = Passage(passage_id=f"{GUIDE}:0", source_id=GUIDE, text=text, locator=loc, kind="body")
    parts = chunk([p], chars=300, overlap=60)
    assert len(parts) > 5 and all(len(x.text) <= 300 for x in parts)
    assert all(x.locator.text == "page 3" for x in parts)
    assert [x.passage_id for x in parts[:2]] == [f"{GUIDE}:0.0", f"{GUIDE}:0.1"]
    assert parts[1].text[:20] in parts[0].text  # overlap carried over
    assert "".join(x.text for x in parts).count("Sentence 119") >= 1


def test_column_passages_describe_schema(env):
    _, store = env
    cols = {p.locator.text: p for p in column_passages(store.get(DATA))}
    assert set(cols) == {"column baum_id", "column pflanzjahr", "column height"}
    assert cols["column pflanzjahr"].text.startswith("Column pflanzjahr in trees.csv:")
    assert (
        "e.g. 1985; 1990" in cols["column pflanzjahr"].text
        and cols["column height"].kind == "header"
    )


def test_hybrid_search_dense_finds_cross_language_match(env):
    cfg, store = env
    enc = FakeEncoder()
    r = Retriever(cfg, store, enc)
    r.build()
    assert len(list((cfg.artifacts_dir / "index").glob("*.npy"))) == 3
    hits = r.search("When was the tree planted, unlike journeys?", [GUIDE, DATA, OTHER], k=3)
    texts = [h.text for h in hits]
    assert any("Pflanzjahr ist das Jahr" in t for t in texts)  # no shared word with the query
    bm25 = Retriever(cfg, store, None).search(
        "When was the tree planted, unlike journeys?", [GUIDE, DATA, OTHER], k=3
    )
    assert not any("Pflanzjahr ist das Jahr" in h.text for h in bm25)
    assert hits[0].source_id == GUIDE  # guide file leads


def test_exact_markers_kinds_cap_and_scope(env):
    cfg, store = env
    r = Retriever(cfg, store, FakeEncoder())
    r.build()
    hits = r.search("What does [z] mean in height?", [GUIDE, DATA], k=4)
    assert "[z] = not applicable" in hits[0].text
    only = r.search("pflanzjahr", [DATA], k=5, kinds=["header"])
    assert only and all(h.kind == "header" and h.source_id == DATA for h in only)
    cap = cfg.model_copy(
        update={"retrieval": cfg.retrieval.model_copy(update={"max_per_source": 1})}
    )
    hits = Retriever(cap, store, FakeEncoder()).search("bus energy tree", [GUIDE, DATA, OTHER], k=3)
    assert sorted(h.source_id for h in hits) == [GUIDE, DATA, OTHER]
    assert r.search("anything", ["SRC_ffffffffffff"]) == []


def test_encoder_failure_falls_back_to_bm25(env):
    cfg, store = env
    Retriever(cfg, store, FakeEncoder()).build()
    r = Retriever(cfg, store, FakeEncoder(fail=True))
    hits = r.search("Bus journeys", [GUIDE, OTHER], k=2)
    assert hits and "Bus journeys" in hits[0].text and r.dense_error.startswith("RuntimeError")


def test_vectors_are_cached_and_keyed_on_the_source(env):
    cfg, store = env
    enc = FakeEncoder()
    Retriever(cfg, store, enc).build()
    calls = enc.calls
    Retriever(cfg, store, enc).build()
    assert enc.calls == calls  # second build reuses the .npy files
    r = Retriever(cfg, store, enc)
    k1 = r.key(DATA)
    store.manifest[DATA] = store.manifest[DATA].model_copy(update={"sha256": "changed"})
    assert r.key(DATA) != k1


def test_text_ops_cite_passage_locators(env):
    cfg, store = env
    r = Retriever(cfg, store, FakeEncoder())
    r.build()
    ctx = ExecContext(store, Conventions(raw_context=""), [GUIDE, DATA], retriever=r)

    def run(op, **args):
        res = run_step(Step(op=op, args=args, save_as="x"), {}, ctx)
        assert res.ok, res.error
        return res.facts

    d = run("get_definition", term="[z]")
    assert "[z] = not applicable" in d[0].value and d[0].locators[0].source_id == GUIDE
    s = run("get_section", heading="Markers", source_id=GUIDE)
    assert s[0].locators[0].text.startswith('section "Markers"')
    f = run("find_passages", query="planting year", k=2)
    assert len(f) == 2 and f[0].claim
    bad = run_step(Step(op="get_section", args={"heading": "Nope"}, save_as="x"), {}, ctx)
    assert not bad.ok and "no section" in bad.error


def test_markers_are_tokens_and_exact_terms_outrank_word_overlap(env):
    from municipal_qa.retrieval.bm25 import _tokens

    assert _tokens(["Shorthand [z] and made_date"]) == [["shorthand", "[z]", "and", "made_date"]]
    cfg, store = env
    r = Retriever(cfg, store, None)
    q = "What does [z] mean for bus journeys per year ending March?"
    hits = r.search(q, [GUIDE, OTHER], k=2)
    assert "[z] = not applicable" in hits[0].text and "Bus journeys" in hits[1].text

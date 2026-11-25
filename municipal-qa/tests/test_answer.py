import json
import re

import httpx
import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.llm.client import LLMClient
from municipal_qa.pipeline import answer as answer_mod
from municipal_qa.pipeline.answer import Deps, answer_question, clean_answer, fallback_text
from municipal_qa.readers.base import UnsupportedFormat
from municipal_qa.schema import Question, SourceInfo

LINE = re.compile(r"^SRC_[0-9a-f]{12} \| .+ \| .+$")


def _info(sid, path):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=path.name,
        source_set="Train",
        publisher="Pub",
        licence="",
        bytes=path.stat().st_size,
        sha256="",
        suffix=path.suffix.lower(),
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    guide = tmp_path / "SourceGuide.md"
    guide.write_text("# Shorthand\n\n[z] means not applicable, not zero.\n")
    table = tmp_path / "revenue.csv"
    table.write_text("year,london,metro\n2025,2478,[z]\n2024,2390,[z]\n")
    raster = tmp_path / "surface.tif"
    raster.write_bytes(b"II*\x00")
    manifest = {
        "SRC_aaaaaaaaaaaa": _info("SRC_aaaaaaaaaaaa", guide),
        "SRC_bbbbbbbbbbbb": _info("SRC_bbbbbbbbbbbb", table),
        "SRC_cccccccccccc": _info("SRC_cccccccccccc", raster),
    }
    cfg = load_config(env_file=tmp_path / "none.env")
    q = Question(
        id="q1",
        case_id="c1",
        domain="transport",
        context="Counts mean records.",
        question="Is the metro revenue for 2025 zero, given the [z] marker?",
        source_ids=list(manifest),
        split="dev",
    )
    return cfg, manifest, q, tmp_path


def _client(cfg, reply="No. [z] means not applicable, not zero.", status=200):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.parameter_count": 1}})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if status != 200:
            return httpx.Response(status, text="boom")
        return httpx.Response(
            200, json={"message": {"content": reply}, "eval_count": 12, "prompt_eval_count": 300}
        )

    return LLMClient(cfg, transport=httpx.MockTransport(handler))


def _valid(rec, q):
    assert rec.answer.strip()
    assert not re.search(r"^## ", rec.answer, re.M)
    assert 0 < len(rec.evidence) <= 8 or rec.fallback_used
    for ln in rec.evidence:
        assert LINE.match(ln) and ln[:16] in q.source_ids and "\n" not in ln
    assert rec.total_calls <= 15 and rec.total_generated_tokens <= 12000


def test_baseline_composes_once_with_code_built_evidence(env):
    cfg, manifest, q, tmp = env
    deps = Deps(cfg, manifest, _client(cfg), cache_dir=tmp / "cache")
    rec = answer_question(q, deps)
    _valid(rec, q)
    assert rec.answer == "No. [z] means not applicable, not zero."
    assert rec.fallback_used is False and rec.error is None
    assert (rec.total_calls, rec.total_generated_tokens) == (1, 12)
    assert rec.evidence[0].startswith('SRC_aaaaaaaaaaaa | section "Shorthand", line 3 | ')
    assert any(ln.startswith("SRC_bbbbbbbbbbbb | header row |") for ln in rec.evidence)
    assert (tmp / "cache" / "SRC_aaaaaaaaaaaa.v1.json").exists()


def test_model_text_is_cleaned(env):
    cfg, manifest, q, _ = env
    reply = "<think>hmm</think>\n## No.\nIt is not zero.\n### Evidence\n- SRC_x | made up | y"
    rec = answer_question(q, Deps(cfg, manifest, _client(cfg, reply)))
    _valid(rec, q)
    assert rec.answer == "No.\nIt is not zero."
    assert all("made up" not in ln for ln in rec.evidence)
    assert clean_answer("   ") == ""


@pytest.mark.parametrize(
    "inject",
    ["http_500", "empty_reply", "no_client", "budget", "reader_crash", "search_crash"],
)
def test_failures_never_raise_and_never_blank(env, monkeypatch, inject):
    cfg, manifest, q, _ = env
    client = _client(cfg)
    if inject == "http_500":
        client = _client(cfg, status=500)
    elif inject == "empty_reply":
        client = _client(cfg, reply="   ")
    elif inject == "no_client":
        client = None
    elif inject == "budget":
        cfg = cfg.model_copy(
            update={"budget": cfg.budget.model_copy(update={"max_generated_tokens": 100})}
        )
    elif inject == "reader_crash":

        def boom(src):
            raise RuntimeError("reader exploded")

        monkeypatch.setattr("municipal_qa.retrieval.bm25.get_reader", boom)
    elif inject == "search_crash":

        def boom(*a, **k):
            raise RuntimeError("index exploded")

        monkeypatch.setattr(answer_mod, "search", boom)
    rec = answer_question(q, Deps(cfg, manifest, client))
    _valid(rec, q)
    if inject == "reader_crash":
        # Plan §6.10 step 1: passages (here file-level stand-ins) exist, so compose still runs.
        assert "reader exploded" in rec.error
        assert all("| file |" in ln for ln in rec.evidence)
        return
    assert rec.fallback_used is True
    assert rec.answer == fallback_text(q)
    if inject in ("http_500", "budget", "search_crash"):
        assert rec.error
    if inject == "http_500":
        assert (rec.total_calls, rec.total_generated_tokens) == (1, 600)
    if inject == "budget":
        assert rec.total_calls == 0


def test_unreadable_source_gets_file_passage(env):
    cfg, manifest, q, _ = env
    deps = Deps(cfg, manifest, None)
    (p,) = deps.store.get("SRC_cccccccccccc")
    assert p.locator.text == "file" and "no reader for .tif" in p.text
    with pytest.raises(UnsupportedFormat):
        from municipal_qa.readers.base import get_reader

        get_reader(manifest["SRC_cccccccccccc"])


def test_prompt_render_does_not_reexpand_source_text(env):
    from municipal_qa.llm.prompts import render_prompt

    text = render_prompt(
        "compose_baseline",
        context="C",
        sources="S",
        excerpts="[1] contains {question} literally",
        question="REAL Q",
    )
    assert "contains {question} literally" in text and text.count("REAL Q") == 1
    assert json.dumps(text)

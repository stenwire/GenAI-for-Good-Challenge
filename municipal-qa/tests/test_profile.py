import pytest
from typer.testing import CliRunner

import municipal_qa.readers  # noqa: F401
from municipal_qa.cli import app
from municipal_qa.config import load_config
from municipal_qa.profiling import profile as prof_mod
from municipal_qa.profiling.profile import ProfileStore, order_profiles, render_profiles
from municipal_qa.schema import ColumnProfile, SourceInfo, SourceProfile, TableProfile


def _info(sid, path, sha="a"):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=path.name,
        source_set="Train",
        publisher="Pub",
        licence="",
        bytes=path.stat().st_size,
        sha256=sha,
        suffix=path.suffix.lower(),
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    (tmp_path / "SourceGuide.md").write_text("# Fields\n\n[z] means not applicable.\n")
    (tmp_path / "revenue.csv").write_text("year,london,metro\n2025,2478,[z]\n2024,2390,[z]\n")
    (tmp_path / "surface.tif").write_bytes(b"II*\x00")
    (tmp_path / "broken.json").write_text("{not json")
    manifest = {
        f"SRC_{c * 12}": _info(f"SRC_{c * 12}", tmp_path / n)
        for c, n in zip(
            "abcd", ["revenue.csv", "SourceGuide.md", "surface.tif", "broken.json"], strict=True
        )
    }
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    return cfg, manifest


def test_profiles_values_kinds_and_fallbacks(env):
    cfg, manifest = env
    store = ProfileStore(cfg, manifest)
    table = store.get("SRC_aaaaaaaaaaaa")
    assert table.kind == "table" and table.tables[0].n_rows == 2
    cols = {c.name: c for c in table.tables[0].columns}
    assert list(cols) == ["year", "london", "metro"]
    assert cols["metro"].examples == ["[z]"] and cols["metro"].n_null == 0
    guide = store.get("SRC_bbbbbbbbbbbb")
    assert guide.kind == "document" and guide.is_documentation
    tif = store.get("SRC_cccccccccccc")
    assert (tif.kind, tif.reader, tif.warnings) == ("binary", "none", ["no reader for .tif yet"])
    bad = store.get("SRC_dddddddddddd")
    assert bad.kind == "binary" and bad.warnings[0].startswith("reader failed:")


def test_cache_reused_and_invalidated_by_source_or_code(env, monkeypatch):
    cfg, manifest = env
    calls = []
    real = prof_mod.build_profile

    def counting(src):
        calls.append(src.source_id)
        return real(src)

    monkeypatch.setattr(prof_mod, "build_profile", counting)
    sid = "SRC_aaaaaaaaaaaa"
    ProfileStore(cfg, manifest).get(sid)
    ProfileStore(cfg, manifest).get(sid)
    assert calls == [sid]
    changed = {**manifest, sid: manifest[sid].model_copy(update={"sha256": "b"})}
    ProfileStore(cfg, changed).get(sid)
    monkeypatch.setattr(prof_mod, "_profile_code", lambda: "reader-code-changed")
    ProfileStore(cfg, manifest).get(sid)
    assert calls == [sid, sid, sid]
    assert len(list((cfg.artifacts_dir / "profiles").glob(f"{sid}.*.json"))) == 3


def test_build_all_in_parallel_writes_every_profile(env):
    cfg, manifest = env
    store = ProfileStore(cfg, manifest)
    store.build_all(workers=2)
    assert all(store.path(s).exists() for s in manifest)
    assert {p.kind for p in store.get_many(list(manifest))} == {"table", "document", "binary"}


def _wide(sid, name, doc=False, n_cols=40):
    cols = [
        ColumnProfile(name=f"col_{i}", dtype="str", n_null=0, n_unique=9, examples=["x" * 40] * 5)
        for i in range(n_cols)
    ]
    src = SourceInfo(
        source_id=sid,
        path="/x",
        original_file=name,
        source_set="Train",
        publisher="",
        licence="",
        bytes=1,
        sha256="",
        suffix=".csv",
    )
    return SourceProfile(
        source=src,
        reader="delimited",
        kind="table",
        tables=[TableProfile(table_name=name, n_rows=4812, columns=cols)] * 3,
        summary="s" * 1000,
        is_documentation=doc,
    )


def test_render_orders_docs_then_named_and_fits_budget():
    profs = [
        _wide("SRC_000000000001", "zones.csv"),
        _wide("SRC_000000000002", "tpo-trees.csv"),
        _wide("SRC_000000000003", "field_dictionary.csv", doc=True),
    ]
    q = "How many rows does tpo-trees.csv have?"
    assert [p.source.original_file for p in order_profiles(profs, q)] == [
        "field_dictionary.csv",
        "tpo-trees.csv",
        "zones.csv",
    ]
    roomy = render_profiles(profs[:1], 6000, q)
    assert "table zones.csv: 4,812 rows" in roomy and "col_0 (str, 0 null, 9 unique, e.g." in roomy
    text = render_profiles(profs * 4, 6000, q)
    assert len(text) <= 6000 and text.startswith("[SRC_000000000003] field_dictionary.csv")
    tiny = render_profiles(profs * 20, 300, q)
    assert len(tiny) <= 300 and tiny.endswith("more sources not shown")


def test_cli_profile_one_source(env, monkeypatch):
    cfg, manifest = env
    monkeypatch.setattr("municipal_qa.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("municipal_qa.data.manifest.load_manifest", lambda c: manifest)
    result = CliRunner().invoke(app, ["profile", "--source", "SRC_cccccccccccc", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert "profiled 1 sources" in result.output and "no reader for .tif yet" in result.output
    bad = CliRunner().invoke(app, ["profile", "--source", "SRC_nope"])
    assert bad.exit_code == 1

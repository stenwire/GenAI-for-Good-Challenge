import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from municipal_qa.config import load_config
from municipal_qa.data.manifest import DataSetupError, load_manifest, setup_data
from municipal_qa.data.questions import load_questions
from municipal_qa.schema import Domain

FILES = {
    "SRC_aaaaaaaaaaaa__trees.csv": b"reference,height\nT1,12\n",
    "SRC_bbbbbbbbbbbb__guide.md": b"# Guide\n[z] means not applicable\n",
}
QCOLS = ["ID", "Case_ID", "Domain", "Question", "Context", "Source_IDs"]


def _manifest_rows(files: dict[str, bytes], sets: dict[str, str]) -> list[dict]:
    rows = []
    for name, data in files.items():
        sid, orig = name.split("__", 1)
        rows.append(
            {
                "Source_ID": sid,
                "File": f"sources/{name}",
                "Source_Set": sets[sid],
                "Original_File": orig,
                "Publisher": "Pub",
                "Licence": "CC BY 4.0",
                "SHA256": hashlib.sha256(data).hexdigest(),
                "Bytes": len(data),
            }
        )
    return rows


@pytest.fixture
def mini_data(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    with zipfile.ZipFile(d / "SourceMaterials.zip", "w") as zf:
        for name, data in FILES.items():
            zf.writestr(f"sources/{name}", data)
    sets = {"SRC_aaaaaaaaaaaa": "Train", "SRC_bbbbbbbbbbbb": "Test"}
    pd.DataFrame(_manifest_rows(FILES, sets)).to_csv(d / "SourceManifest.csv", index=False)
    pd.DataFrame(
        [
            [
                "t1",
                "c1",
                "urban_greenery",
                "Q?",
                "Ctx",
                '["SRC_aaaaaaaaaaaa"]',
                "A.",
                '["trees.csv, row 1"]',
            ]
        ],
        columns=[*QCOLS, "Answer", "Evidence"],
    ).to_csv(d / "Train.csv", index=False)
    pd.DataFrame(
        [["x1", "c9", "transport", "Q2?", "Ctx2", '["SRC_bbbbbbbbbbbb"]']], columns=QCOLS
    ).to_csv(d / "Test.csv", index=False)
    monkeypatch.setenv("MQA_DATA_DIR", str(d))
    return d


def _cfg(tmp_path):
    return load_config(env_file=tmp_path / "none.env")


def test_setup_extracts_and_verifies(mini_data, tmp_path):
    cfg = _cfg(tmp_path)
    setup_data(cfg)
    assert (mini_data / "sources" / "SRC_aaaaaaaaaaaa__trees.csv").read_bytes() == FILES[
        "SRC_aaaaaaaaaaaa__trees.csv"
    ]
    assert not (mini_data / "sources.partial").exists()
    m = load_manifest(cfg)
    assert set(m) == {"SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb"}
    info = m["SRC_aaaaaaaaaaaa"]
    assert (info.suffix, info.original_file, info.source_set) == (".csv", "trees.csv", "Train")
    assert Path(info.path).is_absolute() and Path(info.path).is_file()


def test_existing_sources_are_not_re_extracted(mini_data, tmp_path):
    cfg = _cfg(tmp_path)
    setup_data(cfg)
    (mini_data / "sources" / "SRC_aaaaaaaaaaaa__trees.csv").write_bytes(
        b"reference,height\nT1,99\n"
    )
    with pytest.raises(DataSetupError, match="SHA256 mismatch"):
        setup_data(cfg)


def test_size_mismatch_and_missing_file_reported(mini_data, tmp_path):
    cfg = _cfg(tmp_path)
    setup_data(cfg)
    (mini_data / "sources" / "SRC_aaaaaaaaaaaa__trees.csv").write_bytes(b"x")
    (mini_data / "sources" / "SRC_bbbbbbbbbbbb__guide.md").unlink()
    with pytest.raises(DataSetupError) as e:
        setup_data(cfg, verify_hashes=False)
    text = "\n".join(e.value.problems)
    assert "SRC_aaaaaaaaaaaa: size 1" in text
    assert "SRC_bbbbbbbbbbbb: missing file" in text


@pytest.mark.parametrize(
    "bad", ["sources/../../evil.txt", "evil.txt", "/sources/abs.txt", "sources\\..\\evil.txt"]
)
def test_unsafe_members_refused(mini_data, tmp_path, bad):
    with zipfile.ZipFile(mini_data / "SourceMaterials.zip", "a") as zf:
        zf.writestr(bad, b"x")
    with pytest.raises(DataSetupError, match="unsafe archive member"):
        setup_data(_cfg(tmp_path))
    assert not (mini_data / "sources").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_load_questions_parses_lists(mini_data, tmp_path):
    cfg = _cfg(tmp_path)
    tr = load_questions(cfg, "train")
    assert tr[0].source_ids == ["SRC_aaaaaaaaaaaa"]
    assert tr[0].reference_evidence == ["trees.csv, row 1"]
    assert tr[0].domain is Domain.URBAN_GREENERY and tr[0].split == "train"
    te = load_questions(cfg, "test")
    assert te[0].reference_answer is None and te[0].reference_evidence is None
    with pytest.raises(ValueError):
        load_questions(cfg, "holdout")


def test_real_data_acceptance(data_cfg):
    manifest = load_manifest(data_cfg)
    assert len(manifest) == 188
    assert all(Path(i.path).is_file() for i in manifest.values())
    test = load_questions(data_cfg, "test")
    train = load_questions(data_cfg, "train")
    assert (len(test), len(train)) == (600, 9)
    test_src = {s for q in test for s in q.source_ids}
    train_src = {s for q in train for s in q.source_ids}
    assert test_src <= set(manifest) and train_src <= set(manifest)
    assert not test_src & train_src
    assert {manifest[s].source_set for s in test_src} == {"Test"}
    assert {manifest[s].source_set for s in train_src} == {"Train"}
    assert [q.id for q in test] == sorted(q.id for q in test)
    json.dumps([q.model_dump() for q in train])

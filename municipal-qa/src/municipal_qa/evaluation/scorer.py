"""Deterministic dev scorer (card T4): element 0.8c + 0.2ce, then question, case, domain."""

from __future__ import annotations

import calendar
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, utils

from municipal_qa.pipeline.evidence import line as evidence_line
from municipal_qa.schema import AnswerRecord, Question, SourceInfo

TEXT_FULL = 85
TEXT_HALF = 60
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_NUM = re.compile(r"(?<![\w/.,])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w/])")
_DATE_WORDS = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\b", re.I)
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CELL = re.compile(r"\b([A-Z]{1,3})(\d{1,7})(?::([A-Z]{1,3})(\d{1,7}))?\b")
# Checked in order: percentage points before percent, square units before plain ones.
_UNITS: dict[str, str] = {
    "pp": r"percentage[- ]points?|\bpp\b",
    "%": r"%|\bper ?cent(?!age)",
    "ha": r"\bha\b|hectares?",
    "km²": r"km²|\bkm2\b|square kilomet",
    "m²": r"m²|\bm2\b|square met",
    "m³": r"m³|\bm3\b|cubic met",
    "GWh": r"\bgwh\b",
    "MWh": r"\bmwh\b",
    "kWh": r"\bkwh\b",
    "km": r"\bkm\b|kilomet",
    "m": r"\bm\b|\bmet(?:re|er)s?\b",
    "days": r"\bdays?\b",
    "minutes": r"\bmin(?:ute)?s?\b",
}


# ---------- parsing ----------


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[Decimal]:
    cleaned = _DATE_WORDS.sub(" ", _DATE_ISO.sub(" ", text))
    return [d for d in (_dec(m.group(0)) for m in _NUM.finditer(cleaned)) if d is not None]


def dates_in(text: str) -> set[str]:
    out = {f"{y}-{m}-{d}" for y, m, d in _DATE_ISO.findall(text)}
    for d, mon, y in _DATE_WORDS.findall(text):
        out.add(f"{y}-{MONTHS[mon.lower()]:02d}-{int(d):02d}")
    return out


def _unit_after(text: str, end: int) -> str | None:
    tail = text[end : end + 32]
    for unit, pattern in _UNITS.items():
        if re.match(r"\s*(?:" + pattern + ")", tail, re.I):
            return unit
    return None


def default_elements(reference: str) -> list[dict]:
    """Elements derived from a reference answer when a row gives none (e.g. Train)."""
    elements: list[dict] = []
    first = re.match(r"\s*(yes|no)\b", reference, re.I)
    if first:
        elements.append({"type": "yes_no", "value": first.group(1).lower(), "text": first.group(0)})
    for iso in sorted(dates_in(reference)):
        elements.append({"type": "date", "value": iso, "text": iso})
    cleaned = _DATE_WORDS.sub(
        lambda m: " " * len(m.group(0)), _DATE_ISO.sub(lambda m: " " * len(m.group(0)), reference)
    )
    for m in _NUM.finditer(cleaned):
        raw = m.group(0)
        value = _dec(raw)
        if value is None or ("." not in raw and "," not in raw and 1900 <= value <= 2100):
            continue
        places = len(raw.split(".")[1]) if "." in raw else 0
        el = {
            "type": "number",
            "text": raw,
            "value": float(value),
            "tol": float(Decimal(5).scaleb(-(places + 1))),
        }
        unit = _unit_after(cleaned, m.end())
        if unit:
            el["unit"] = unit
        elements.append(el)
    if not any(e["type"] in ("number", "date") for e in elements):
        elements.append({"type": "text", "text": reference})
    return elements


# ---------- evidence ----------


def locator_tokens(locator: str) -> set[str]:
    toks: set[str] = set()
    for c1, r1, c2, r2 in _CELL.findall(locator):
        if c2:
            for col in {c1, c2}:
                for row in range(int(r1), int(r2) + 1):
                    toks.add(f"{col}{row}")
        else:
            toks.add(f"{c1}{r1}")
    low = locator.lower()
    toks |= {
        f"{w} {n}" for w, n in re.findall(r"\b(row|note|page|line|table|rowid)s?\s+(\d+)", low)
    }
    toks |= {v.strip().lower() for v in re.findall(r"=\s*([^,;|]+)", locator)}
    toks |= {w for w in re.findall(r"[a-z][a-z0-9]*(?:[._][a-z0-9]+)+", low)}
    toks |= {f"/{p}" for p in re.findall(r"(?:^|\s)(/[\w/~.-]+)", locator)}
    return toks


def parse_reference_evidence(
    items: list[str], q: Question, manifest: dict[str, SourceInfo]
) -> list[tuple[str | None, str]]:
    """Train-style `file, locator; more` or submission-style `SRC_x | locator | claim` → pairs."""
    by_name = {manifest[s].original_file: s for s in q.source_ids if s in manifest}
    out: list[tuple[str | None, str]] = []
    for item in items:
        if item.startswith("SRC_"):
            parts = [p.strip() for p in item.split("|")]
            out.append((parts[0], parts[1] if len(parts) > 1 else ""))
            continue
        current: str | None = None
        for seg in (s.strip() for s in item.split(";")):
            head, _, rest = seg.partition(",")
            if head.strip() in by_name:
                current, loc = by_name[head.strip()], rest.strip()
            else:
                loc = seg
            out.append((current, loc))
    return out


def reference_evidence_lines(q: Question, manifest: dict[str, SourceInfo]) -> list[str]:
    pairs = parse_reference_evidence(q.reference_evidence or [], q, manifest)
    return [evidence_line(sid, loc, "reference evidence") for sid, loc in pairs if sid]


def _cited(record: AnswerRecord) -> list[tuple[str, str]]:
    out = []
    for line in record.evidence:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def score_evidence(record: AnswerRecord, refs: list[tuple[str | None, str]]) -> float:
    """1 = every reference source cited with a shared locator token; 0.5 = a source; else 0."""
    refs = [(s, loc) for s, loc in refs if s]
    if not refs:
        return 1.0 if record.evidence else 0.0
    cited = _cited(record)
    full, any_source = 0, False
    for sid, loc in refs:
        same = [c for c in cited if c[0] == sid]
        any_source = any_source or bool(same)
        want = locator_tokens(loc)
        if same and (not want or any(want & locator_tokens(c[1]) for c in same)):
            full += 1
    if full == len(refs):
        return 1.0
    return 0.5 if any_source else 0.0


# ---------- correctness ----------


def _unit_present(answer: str, unit: str) -> bool:
    return bool(re.search(_UNITS.get(unit, re.escape(unit)), answer, re.I))


def score_element(element: dict, answer: str) -> float:
    kind = element.get("type", "text")
    if not answer.strip():
        return 0.0
    if kind == "number":
        value = Decimal(str(element["value"]))
        tol = Decimal(str(element.get("tol", 0)))
        if not any(abs(n - value) <= tol for n in numbers_in(answer)):
            return 0.0
        unit = element.get("unit")
        return 1.0 if not unit or _unit_present(answer, unit) else 0.5
    if kind == "date":
        return 1.0 if element["value"] in dates_in(answer) else 0.0
    if kind == "yes_no":
        first = re.match(r"\s*(yes|no)\b", answer, re.I)
        return 1.0 if first and first.group(1).lower() == element["value"] else 0.0
    keywords = element.get("keywords")
    if keywords:
        hit = sum(k.lower() in answer.lower() for k in keywords)
        return 1.0 if hit == len(keywords) else 0.5 if hit else 0.0
    ratio = fuzz.token_set_ratio(element["text"], answer, processor=utils.default_process)
    return 1.0 if ratio >= TEXT_FULL else 0.5 if ratio >= TEXT_HALF else 0.0


def score_record(
    record: AnswerRecord,
    q: Question,
    elements: list[dict],
    manifest: dict[str, SourceInfo] | None = None,
) -> dict:
    refs = parse_reference_evidence(q.reference_evidence or [], q, manifest or {})
    e = score_evidence(record, refs)
    rows = []
    for el in elements or default_elements(q.reference_answer or ""):
        c = score_element(el, record.answer)
        rows.append(
            {
                "element": el.get("text", ""),
                "type": el.get("type"),
                "c": c,
                "score": 0.8 * c + 0.2 * c * e,
            }
        )
    score = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
    return {"question_id": q.id, "e": e, "elements": rows, "score": score}


# ---------- aggregation ----------


def aggregate(per_question: pd.DataFrame) -> dict[str, object]:
    """Element → question → case → domain → equal-weight mean over domains."""
    cases = per_question.groupby(["domain", "case_id"], as_index=False)["score"].mean()
    domains = cases.groupby("domain")["score"].mean()
    return {
        "overall": float(domains.mean()) if len(domains) else 0.0,
        "by_domain": domains.to_dict(),
        "by_case": cases,
    }


def load_records(run_dir: Path) -> dict[str, AnswerRecord]:
    out: dict[str, AnswerRecord] = {}
    with open(run_dir / "answers.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = AnswerRecord.model_validate_json(line)
                out[rec.question_id] = rec
    return out


def evaluate(
    records: dict[str, AnswerRecord],
    questions: list[Question],
    elements: dict[str, list[dict]],
    manifest: dict[str, SourceInfo] | None = None,
) -> pd.DataFrame:
    rows = []
    for q in questions:
        rec = records.get(q.id) or AnswerRecord(question_id=q.id, answer="", evidence=[])
        s = score_record(rec, q, elements.get(q.id) or [], manifest)
        rows.append(
            {
                "question_id": q.id,
                "case_id": q.case_id,
                "domain": q.domain.value,
                "qtype": rec.route.qtype.value if rec.route else "unrouted",
                "n_elements": len(s["elements"]),
                "c_mean": sum(x["c"] for x in s["elements"]) / max(len(s["elements"]), 1),
                "e": s["e"],
                "score": s["score"],
                "answered": q.id in records,
            }
        )
    return pd.DataFrame(rows)


def evaluate_run(run_dir: Path, split: str, cfg=None) -> pd.DataFrame:
    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions

    cfg = cfg or load_config()
    questions = load_questions(cfg, split)
    return evaluate(
        load_records(Path(run_dir)), questions, load_elements(cfg, split), load_manifest(cfg)
    )


def load_elements(cfg, split: str) -> dict[str, list[dict]]:
    """Dev rows carry an `elements` JSON column; other splits derive them from the answer."""
    from municipal_qa.data.questions import questions_path

    df = pd.read_csv(questions_path(cfg, split), dtype=str, keep_default_na=False)
    if "elements" not in df.columns:
        return {}
    return {r["ID"]: json.loads(r["elements"]) for r in df.to_dict("records") if r["elements"]}

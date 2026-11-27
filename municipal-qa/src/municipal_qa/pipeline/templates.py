"""Code-built plans for common question shapes (D-056). The model plans only what these miss.

Names come from the question's own sources: sheet labels, column names and identifier values.
Nothing here is keyed to a question, a case or a file name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from municipal_qa.readers.spreadsheet import read_cells
from municipal_qa.schema import Plan, SourceProfile, Step
from municipal_qa.tools.table_ops import TOTAL, _is_heading

YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
NOTE = re.compile(r"\[[^\]]*\]")
DOC_SHEET = re.compile(
    r"note|cover|content|readme|about|info|definition|metadata|guid|glossar", re.I
)
IDENT = re.compile(r"(?<![\w/])[A-Za-z]{0,6}\d(?:[\w./-]*\w)?(?![\w/])")
STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "what",
    "which",
    "how",
    "many",
    "much",
    "were",
    "have",
    "does",
    "into",
    "than",
}
MAX_CELLS = 4


def clean_label(s: object) -> str:
    return " ".join(NOTE.sub("", str(s)).split()).strip(" :")


def mentions(question: str, label: str) -> bool:
    lab = clean_label(label).lower()
    if len(lab) < 3 or lab.isdigit():
        return False
    return re.search(rf"(?<!\w){re.escape(lab)}(?!\w)", question.lower()) is not None


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüåé]{4,}", s.lower()) if w not in STOP}


# ---------- workbooks ----------


@dataclass
class Sheet:
    name: str
    title: str = ""
    rows: dict[str, list[int]] = field(default_factory=dict)  # label -> row numbers
    headers: dict[str, list[int]] = field(default_factory=dict)  # label -> header rows
    blocks: set[str] = field(default_factory=set)


@cache
def sheet_vocab(path: str) -> tuple[Sheet, ...]:
    out = []
    for name, cells in read_cells(path).items():
        if DOC_SHEET.search(name) or not cells:
            continue
        sh = Sheet(name)
        by_row: dict[int, list] = {}
        for c in cells:
            by_row.setdefault(c.row, []).append(c)
        title = []
        for r in sorted(by_row):
            row = sorted(by_row[r], key=lambda c: c.col)
            filled = [c for c in row if c.displayed not in (None, "")]
            if not filled:
                continue
            first = filled[0]
            if first.col <= 2:
                lab = clean_label(first.displayed)
                sh.rows.setdefault(lab, []).append(r)
                if len(filled) == 1 and r <= 6 and len(lab) > 20:
                    title.append(lab)
            if _is_heading(row):
                if first.col <= 2:
                    sh.blocks.add(clean_label(first.displayed))
                for c in filled[1:]:
                    sh.headers.setdefault(clean_label(c.displayed), []).append(r)
        sh.title = title[0][:400] if title else ""  # row 1 title; later rows are boilerplate
        out.append(sh)
    return tuple(out)


def _years(question: str) -> list[str]:
    return list(dict.fromkeys(YEAR.findall(question)))


def _longest(question: str, labels) -> list[str]:
    """Labels the question mentions, longest first, without nested repeats."""
    hits = sorted({lab for lab in labels if mentions(question, lab)}, key=len, reverse=True)
    out: list[str] = []
    for h in hits:
        if not any(h.lower() in o.lower() for o in out):
            out.append(h)
    return out


GENERIC = {"percentage", "percent", "number", "total", "year", "years", "value", "rate", "share",
           "year ending march", "count", "units", "notes"}  # fmt: skip


CALC_WORDS = {"percentage", "percent", "total", "share", "change", "changed", "compare",
              "compared", "difference", "increase", "decrease", "rise", "fall", "average",
              "highest", "lowest", "proportion"}  # describe the calculation, not the table


def _specific(labels):
    return [lab for lab in labels if clean_label(lab).lower() not in GENERIC]


def _sheet_score(question: str, sh: Sheet) -> float:
    years = _years(question)
    ents = _specific(_longest(question, [*sh.headers, *sh.rows, *sh.blocks]))
    year_hits = sum(1 for y in years if y in sh.headers or y in sh.rows)
    qw, tw = _words(question) - CALC_WORDS, _words(sh.title)
    return 3 * len(ents) + 2 * year_hits + len(qw & tw) - 0.25 * len(tw - qw)


def _total_label(sh: Sheet) -> str | None:
    return next((lab for lab in sh.rows if TOTAL.search(lab)), None)


def _order(question: str, labels: list[str]) -> list[str]:
    q = question.lower()
    return sorted(labels, key=lambda lab: q.find(clean_label(lab).lower()))


def workbook_plan(question: str, prof: SourceProfile) -> Plan | None:
    sheets = sheet_vocab(str(Path(prof.source.path).resolve()))
    if not sheets:
        return None
    sh = max(sheets, key=lambda s: _sheet_score(question, s))
    if _sheet_score(question, sh) < 3:
        return None
    sid = prof.source.source_id
    years = _years(question)
    year_cols = [y for y in years if y in sh.headers]
    year_rows = [y for y in years if y in sh.rows and y not in year_cols]
    blocks = _order(question, _longest(question, sh.blocks))
    heads = _order(question, [h for h in _specific(_longest(question, sh.headers))
                              if not YEAR.fullmatch(h)])  # fmt: skip
    rows = _order(question, [r for r in _longest(question, sh.rows)
                             if r not in sh.blocks and not YEAR.fullmatch(r)])  # fmt: skip
    wants_total = bool(re.search(r"\btotal\b", question, re.I))
    cells: list[dict] = []
    if year_cols:
        row_labels = rows or ([_total_label(sh)] if (wants_total or blocks) and _total_label(sh)
                              else [])  # fmt: skip
        if not row_labels:
            return None
        for b in blocks or [None]:
            for r in row_labels:
                for y in year_cols:
                    cells.append({"row": r, "column": y, "block": b})
    elif year_rows and heads:
        for h in heads:
            for y in year_rows:
                cells.append({"row": y, "column": h, "block": None})
    elif rows and heads:
        for r in rows:
            for h in heads:
                cells.append({"row": r, "column": h, "block": None})
    else:
        return None
    share_q = re.search(r"\bshare\b|proportion|percentage of|what percent", question, re.I)
    total = _total_label(sh)
    if (share_q and len(cells) == 1 and total and not TOTAL.search(str(cells[0]["row"]))
            and year_cols):  # fmt: skip
        cells.append({**cells[0], "row": total})
    if not cells or len(cells) > MAX_CELLS:
        return None
    steps = []
    for i, c in enumerate(cells):
        args = {"source_id": sid, "sheet": sh.name, "row": c["row"], "column": c["column"]}
        if c["block"]:
            args["block"] = c["block"]
        steps.append(Step(op="cell_lookup", args=args, save_as=f"v{i + 1}"))
    steps += _derive(question, cells)
    return Plan(steps=steps, rationale=f"template: workbook sheet {sh.name}")


def _year_of(c: dict) -> str | None:
    return next((str(c[k]) for k in ("row", "column") if YEAR.fullmatch(str(c[k]))), None)


def _entity(c: dict) -> str:
    """The cell's labels other than its year: block, then row or column."""
    parts = [c["block"], *(c[k] for k in ("row", "column") if not YEAR.fullmatch(str(c[k])))]
    return " / ".join(str(x) for x in parts if x)


def _two_years(yrs: list, ix: list[int]) -> bool:
    a, b = yrs[ix[0]], yrs[ix[1]]
    return bool(a and b and a != b)


def _derive(question: str, cells: list[dict]) -> list[Step]:
    """Calc steps over the looked-up cells: change between years, comparison, or share."""
    q = question.lower()
    as_share = re.search(r"as an? (?:percentage|share|proportion) of|% of", q)
    pp = "percentage point" in q
    if len(cells) == 1:
        return []
    yrs = [_year_of(c) for c in cells]
    by_ent: dict[str, list[int]] = {}
    for i, c in enumerate(cells):
        by_ent.setdefault(_entity(c), []).append(i)
    pairs = [ix for ix in by_ent.values() if len(ix) == 2 and _two_years(yrs, ix)]
    steps: list[Step] = []
    if pairs:
        for k, (a, b) in enumerate(pairs, 1):
            old, new = sorted((a, b), key=lambda i: yrs[i])
            ent = _entity(cells[a])
            if as_share:
                args = {"part": f"$v{new + 1}", "total": f"$v{old + 1}",
                        "label": f"{ent} {yrs[new]} as % of {yrs[old]}"}  # fmt: skip
                steps.append(Step(op="share", save_as=f"r{k}", args=args))
            else:
                args = {"old": f"$v{old + 1}", "new": f"$v{new + 1}", "label": ent}
                steps.append(Step(op="percent_change", save_as=f"r{k}", args=args))
        if len(pairs) == 2:
            fact = "" if as_share else ".pct"
            steps.append(Step(op="compare_values", save_as="cmp", args={
                "a": f"$r1{fact}", "b": f"$r2{fact}",
                "label_a": _entity(cells[pairs[0][0]]),
                "label_b": _entity(cells[pairs[1][0]])}))  # fmt: skip
        return steps
    if len(cells) == 2:
        la, lb = _entity(cells[0]), _entity(cells[1])
        totals = [TOTAL.search(str(c["row"])) for c in cells]
        if any(totals) and not all(totals) and re.search(r"share|proportion|percent", q):
            part, total = ("$v1", "$v2") if totals[1] else ("$v2", "$v1")
            return [Step(op="share", save_as="share", args={"part": part, "total": total})]
        if pp:
            args = {"a": "$v1", "b": "$v2", "label": f"{la} minus {lb}"}
            return [Step(op="pp_difference", save_as="pp", args=args)]
        args = {"a": "$v1", "b": "$v2", "label_a": la, "label_b": lb}
        return [Step(op="compare_values", save_as="cmp", args=args)]
    return steps


# ---------- tables and layers ----------


DATE_SUFFIX = re.compile(r"[\s_-]*(date|dt)$", re.I)


def _mentioned_columns(question: str, prof: SourceProfile) -> list[tuple[str, str]]:
    """Columns named in the question; a date column also by its stem (`made` for made_date)."""
    out = []
    for t in prof.tables:
        for c in t.columns:
            names = {c.name, c.name.replace("_", " ").replace("-", " ")}
            stem = DATE_SUFFIX.sub("", c.name)
            if stem != c.name and len(stem) >= 4:
                names.add(stem.replace("_", " ").replace("-", " "))
            words = re.split(r"[\s_-]+", c.name)
            if len(words) >= 3:
                names.add(" ".join(words[-2:]))
            if any(mentions(question, n) for n in names):
                out.append((t.table_name, c.name))
    return out


def _ident_score(prof, hit, ctx) -> int:
    table, col, value = hit
    vals = ctx.frame(prof.source.source_id, table)[col].astype(str).str.strip()
    return (2 if int((vals == value).sum()) == 1 else 0) + (1 if KEYISH.search(col) else 0)


KEYISH = re.compile(r"ref|id$|^id|code|key|name|number|nummer", re.I)


def _ident_match(question: str, prof: SourceProfile, ctx) -> tuple[str, str, str] | None:
    """(table, key column, value): an identifier in the question found in a column, preferring
    columns where it is unique and whose name looks like a key."""
    idents = [m for m in IDENT.findall(question) if not YEAR.fullmatch(m)]
    if not idents:
        return None
    best, score = None, -1
    for t in prof.tables:
        if t.n_rows > 300_000:
            continue
        try:
            df = ctx.frame(prof.source.source_id, t.table_name)
        except Exception:  # noqa: BLE001
            continue
        for col in df.columns:
            if col == "geometry" or df[col].dtype.kind in "fc":
                continue
            vals = df[col].astype(str).str.strip()
            for ident in idents:
                n = int((vals == ident).sum())
                if not n:
                    continue
                s = (2 if n == 1 else 0) + (1 if KEYISH.search(str(col)) else 0)
                if s > score:
                    best, score = (t.table_name, str(col), ident), s
    return best


def _date_columns(cols: list[str]) -> list[str]:
    return [c for c in cols if re.search(r"date|_at$|day", c, re.I)]


def _two_idents(question: str, profiles, ctx):
    idents = [m for m in IDENT.findall(question) if not YEAR.fullmatch(m)]
    if len(idents) < 2:
        return None
    for p in profiles:
        if p.kind != "vector":
            continue
        hit = _ident_match(idents[0], p, ctx)
        hit2 = _ident_match(idents[1], p, ctx)
        if hit and hit2:
            return p, hit, hit2
    return None


def table_plan(question: str, profiles: list[SourceProfile], ctx) -> Plan | None:
    q = question.lower()
    if re.search(r"how far apart|distance between", q):
        two = _two_idents(question, profiles, ctx)
        if two:
            p, (ta, ka, va), (tb, kb, vb) = two
            sid = p.source.source_id
            return Plan(steps=[Step(op="distance", save_as="dist", args={
                "source_id": sid, "table": ta, "key_column": ka, "value": va,
                "source_b": sid, "table_b": tb, "key_column_b": kb, "value_b": vb})],
                rationale="template: distance")  # fmt: skip
    tabled = [p for p in profiles if p.tables and p.kind != "workbook"]
    if not tabled:
        return None
    named = [p for p in tabled if p.source.original_file.lower() in q]
    order = named + [p for p in tabled if p not in named]
    ranked = []
    for prof in order:
        h = _ident_match(question, prof, ctx)
        if h:
            used = sum(1 for tb, _ in _mentioned_columns(question, prof) if tb == h[0])
            ranked.append((4 * (prof in named) + 2 * used + _ident_score(prof, h, ctx), prof, h))
    ranked.sort(key=lambda x: -x[0])
    for _, prof, hit in ranked[:1]:
        sid = prof.source.source_id
        if hit and prof.kind == "vector" and re.search(r"\b(inside|within) (?:zone|area|polygon)",
                                                        q):  # fmt: skip
            pts = [p for p in profiles if p.kind == "vector" and p is not prof]
            pts = [p for p in pts if p.source.original_file.lower() in q] or pts
            if pts:
                table, key, value = hit
                return Plan(steps=[Step(op="point_in_polygon", save_as="inside", args={
                    "source_id": pts[0].source.source_id, "source_b": prof.source.source_id,
                    "table_b": table, "key_column_b": key, "value_b": value})],
                    rationale="template: points in polygon")  # fmt: skip
        if hit:
            table, key, value = hit
            cols = [c for t, c in _mentioned_columns(question, prof) if t == table and c != key]
            if prof.kind == "vector" and re.search(r"\barea\b|hectare", q):
                unit = "ha" if "hectare" in q else "m²"
                return Plan(steps=[Step(op="area", save_as="area", args={
                    "source_id": sid, "table": table, "key_column": key, "value": value,
                    "unit": unit})], rationale="template: feature area")  # fmt: skip
            m = re.search(r"within (\d+(?:\.\d+)?)\s*(?:m\b|metres|meters)", q)
            if prof.kind == "vector" and m:
                vec = [p for p in profiles if p.kind == "vector"]
                vec = [p for p in vec if p.source.original_file.lower() in q] or vec
                targets = [{"source_id": p.source.source_id, "table": tb.table_name}
                           for p in vec for tb in p.tables]  # fmt: skip
                return Plan(steps=[Step(op="within_radius", save_as="near", args={
                    "source_id": sid, "table": table, "key_column": key, "value": value,
                    "radius": float(m.group(1)), "targets": targets})],
                    rationale="template: radius")  # fmt: skip
            steps = [Step(op="lookup_row", save_as="row", args={
                "source_id": sid, "table": table, "key_column": key, "value": value,
                **({"columns": cols} if cols else {})})]  # fmt: skip
            dates = _date_columns(cols)
            if len(dates) >= 2 and re.search(r"\bdays?\b", q):
                steps.append(Step(op="date_diff", save_as="days", args={
                    "start": f"$row.{dates[0]}", "end": f"$row.{dates[1]}"}))  # fmt: skip
            return Plan(steps=steps, rationale="template: record lookup")
    if re.search(
        r"only one|present in one|in either file|missing from (?:the )?(?:other|csv|file)", q
    ):
        keyed = [(p, t, c) for p in order for t, c in _mentioned_columns(question, p)]
        pick = {}
        for p, t, c in keyed:
            pick.setdefault(p.source.source_id, (t, c))
        if len(pick) < 2:
            ref = [(p, t.table_name, c.name) for p in order for t in p.tables for c in t.columns
                   if c.name.lower() in ("reference", "id", "ref")]  # fmt: skip
            for p, t, c in ref:
                pick.setdefault(p.source.source_id, (t, c))
        if len(pick) >= 2:
            (sa, (ta, ca)), (sb, (tb, cb)) = list(pick.items())[:2]
            return Plan(steps=[Step(op="set_compare", save_as="cmp", args={
                "source_a": sa, "table_a": ta, "column_a": ca,
                "source_b": sb, "table_b": tb, "column_b": cb})],
                rationale="template: set compare")  # fmt: skip
    for prof in order:
        sid = prof.source.source_id
        cols = _mentioned_columns(question, prof)
        if re.search(r"\b(no|missing|blank|empty|without)\b", q) and cols:
            table, col = cols[0]
            steps = [Step(op="count_nulls", save_as="nulls", args={
                "source_id": sid, "table": table, "columns": [col]})]  # fmt: skip
            if re.search(r"share|percent|proportion|what fraction", q):
                steps.append(Step(op="share", save_as="share", args={
                    "part": f"$nulls.{col}.blank", "total": "$nulls.total"}))  # fmt: skip
            return Plan(steps=steps, rationale="template: nulls")
        if re.search(r"duplicat", q) and (cols or named):
            table, col = (
                cols[0] if cols else (prof.tables[0].table_name, prof.tables[0].columns[0].name)
            )
            return Plan(steps=[Step(op="duplicates", save_as="dups", args={
                "source_id": sid, "table": table, "columns": [col]})],
                rationale="template: duplicates")  # fmt: skip
        if re.search(r"\binvalid\b", q) and prof.kind == "vector" and prof in named:
            return Plan(steps=[Step(op="geometry_validity", save_as="valid", args={
                "source_id": sid, "table": prof.tables[0].table_name})],
                rationale="template: validity")  # fmt: skip
        if re.search(r"\beach\b|\bper\b|by type|breakdown", q) and cols and prof in named:
            table, col = cols[0]
            return Plan(steps=[Step(op="value_counts", save_as="counts", args={
                "source_id": sid, "table": table, "column": col, "top": 20})],
                rationale="template: value counts")  # fmt: skip
        if re.search(r"\bhow many\b", q) and prof in named and not re.search(
                r"distinct|unique|\band how many\b", q):  # fmt: skip
            table = prof.tables[0].table_name
            where = []
            yrs = _years(question)
            dates = _date_columns([c for _, c in cols])
            if yrs and dates:
                where = [{"column": dates[0], "op": "contains", "value": yrs[0]}]
            elif cols and re.search(r"\bha(?:ve|s) (?:a|an)\b", q):
                where = [{"column": cols[0][1], "op": "not_null"}]
            args = {"source_id": sid, "table": table, **({"where": where} if where else {})}
            return Plan(
                steps=[Step(op="count_rows", save_as="n", args=args)], rationale="template: count"
            )
    return None


RANKING = re.compile(
    r"\bwhich\b.*\b(most|highest|largest|lowest|least|fewest|biggest|smallest|"
    r"fastest)\b|\brank",
    re.I,
)


def template_plan(question: str, profiles: list[SourceProfile], ctx,
                  qtype: str | None = None) -> Plan | None:  # fmt: skip
    """A code-built plan when the question has a recognised shape; None otherwise."""
    if qtype in ("semantics", "insufficient") or RANKING.search(question):
        return None
    books = [p for p in profiles if p.kind == "workbook"]
    for prof in sorted(books, key=lambda p: -max((_sheet_score(question, s) for s in
                       sheet_vocab(str(Path(p.source.path).resolve()))), default=0)):  # fmt: skip
        plan = workbook_plan(question, prof)
        if plan:
            return plan
    return table_plan(question, profiles, ctx)

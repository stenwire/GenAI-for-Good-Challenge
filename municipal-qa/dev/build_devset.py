"""Regenerate dev/dev_questions.csv from the 22 Train sources only.

Run: cd municipal-qa && uv run python dev/build_devset.py
Every answer is computed here from named cells, rows or features; nothing is typed by hand.
"""

from __future__ import annotations

import csv
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import geopandas as gpd
import pandas as pd

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.data.manifest import load_manifest
from municipal_qa.readers.spreadsheet import get_cell

OUT = Path(__file__).with_name("dev_questions.csv")
COLUMNS = [
    "ID",
    "Case_ID",
    "Domain",
    "Question",
    "Context",
    "Source_IDs",
    "Answer",
    "Evidence",
    "elements",
    "qtype",
]

TRANSPORT = [
    "SRC_f9068c4ee468",
    "SRC_a40a7b1f7c87",
    "SRC_04ef0398ed5b",
    "SRC_275feba90619",
    "SRC_86c284e57780",
    "SRC_8512b6d57ce4",
    "SRC_91e9cd9bb89c",
    "SRC_fa15375ecabf",
    "SRC_d789f5d75489",
    "SRC_097c5e7c4039",
    "SRC_9c13649b37cc",
    "SRC_442ef635e9b5",
    "SRC_d8e99441762f",
]
ENERGY = ["SRC_d0ba40e73d2c", "SRC_d416160a72ba"]
GREENERY = [
    "SRC_7bc924533043",
    "SRC_013eda19f72a",
    "SRC_0e8ca062c66a",
    "SRC_5b6ce878f23c",
    "SRC_5ddbc42bcd4a",
    "SRC_4b81bb5b1c48",
    "SRC_0e67edbba552",
]
BUS01, BUS02KM, BUS03, BUS04I, BUS04II, BUS06, BUS08 = (
    "SRC_f9068c4ee468",
    "SRC_a40a7b1f7c87",
    "SRC_275feba90619",
    "SRC_86c284e57780",
    "SRC_8512b6d57ce4",
    "SRC_d789f5d75489",
    "SRC_097c5e7c4039",
)
GUIDE_T, GUIDE_E, GUIDE_G = "SRC_d8e99441762f", "SRC_d416160a72ba", "SRC_0e67edbba552"
HSY = "SRC_d0ba40e73d2c"
TREES_CSV, GROUPS_CSV, DATASET_CSV = "SRC_013eda19f72a", "SRC_7bc924533043", "SRC_5b6ce878f23c"
TREES_GEOJSON, ZONES, GPKG = "SRC_0e8ca062c66a", "SRC_4b81bb5b1c48", "SRC_5ddbc42bcd4a"

CTX_T = (
    "Use the figures exactly as they are displayed in the supplied DfT workbooks, not the "
    "extra decimals stored in the cells. Keep intermediate results unrounded and report "
    "percentages and percentage-point differences to two decimal places."
)
CTX_E = (
    "Use the stored numeric values in the supplied HSY workbook, keeping their full "
    "precision; the display format rounds to whole GWh. Keep intermediate results "
    "unrounded and report GWh and percentages to two decimal places."
)
CTX_G = (
    "Use the supplied frozen Liverpool tree-preservation files as published. Different "
    "formats are not row-for-row copies. Counts mean records; keep nulls and geometry "
    "unchanged. Report derived figures to two decimal places, hectares to two decimals."
)

D2 = Decimal("0.01")
cfg = load_config()
M = load_manifest(cfg)
ROWS: list[dict] = []


def q2(x: Decimal) -> Decimal:
    return x.quantize(D2, rounding=ROUND_HALF_UP)


def f2(x: Decimal) -> str:
    return f"{q2(x):,.2f}"


def disp(sid: str, sheet: str, ref: str) -> Decimal:
    return Decimal(str(get_cell(M[sid].path, sheet, ref).displayed).replace(",", ""))


def stored(sid: str, sheet: str, ref: str) -> Decimal:
    return Decimal(repr(get_cell(M[sid].path, sheet, ref).stored))


def text(sid: str, sheet: str, ref: str) -> str:
    return str(get_cell(M[sid].path, sheet, ref).displayed)


def num(value: Decimal, unit: str | None = None, places: int = 2) -> dict:
    el = {
        "type": "number",
        "text": f"{value}",
        "value": float(value),
        "tol": float(Decimal(5).scaleb(-(places + 1))),
    }
    if unit:
        el["unit"] = unit
    return el


def add(domain, qtype, question, answer, evidence, elements):
    case, ctx, srcs = {
        "transport": ("dev-transport", CTX_T, TRANSPORT),
        "buildings_energy": ("dev-energy", CTX_E, ENERGY),
        "urban_greenery": ("dev-greenery", CTX_G, GREENERY),
    }[domain]
    ROWS.append(
        {
            "ID": f"dev-{len(ROWS) + 1:03d}",
            "Case_ID": case,
            "Domain": domain,
            "Question": question,
            "Context": ctx,
            "Source_IDs": json.dumps(srcs),
            "Answer": answer,
            "Evidence": json.dumps([f"{s} | {loc} | {claim}" for s, loc, claim in evidence]),
            "elements": json.dumps(elements, ensure_ascii=False),
            "qtype": qtype,
        }
    )


def guide_loc(sid: str, phrase: str) -> str:
    from municipal_qa.readers.base import get_reader

    src = M[sid]
    for p in get_reader(src).passages(src):
        if phrase.lower() in " ".join(p.text.split()).lower():
            return p.locator.text
    raise KeyError(f"{phrase!r} not in {src.original_file}")


def cell_ev(sid, sheet, ref, claim):
    return (sid, f"sheet {sheet}, cell {ref}", claim)


# ---------------------------------------------------------------- transport (20)

REGION_COL = {
    "North East": "B",
    "North West": "C",
    "West Midlands": "F",
    "South West": "J",
    "Great Britain": "S",
    "Scotland": "Q",
    "Wales": "R",
}


def journeys_vs_2019(region):
    c = REGION_COL[region]
    a, b = disp(BUS01, "BUS01a", f"{c}29"), disp(BUS01, "BUS01a", f"{c}23")
    pct = a / b * 100
    add(
        "transport",
        "compare",
        f"How do {region}'s bus passenger journeys in the year ending March 2025 compare with "
        f"the year ending March 2019, as a percentage of the 2019 total?",
        f"{region}'s journeys in the year ending March 2025 were {f2(pct)}% of its "
        f"year-ending-March 2019 total ({a:,} million against {b:,} million).",
        [
            cell_ev(BUS01, "BUS01a", f"{c}29", f"{a:,} million journeys, YE March 2025"),
            cell_ev(BUS01, "BUS01a", f"{c}23", f"{b:,} million journeys, YE March 2019"),
        ],
        [num(q2(pct), "%")],
    )


for region in ["North West", "West Midlands", "South West", "Great Britain"]:
    journeys_vs_2019(region)

for region in ["Scotland", "Wales"]:
    c = REGION_COL[region]
    a, b = disp(BUS01, "BUS01a", f"{c}29"), disp(BUS01, "BUS01a", f"{c}28")
    pct = (a - b) / b * 100
    direction = "rose" if pct > 0 else "fell"
    add(
        "transport",
        "compare",
        f"By what percentage did bus passenger journeys in {region} change between the years "
        f"ending March 2024 and March 2025?",
        f"Journeys in {region} {direction} by {f2(abs(pct))}%, "
        f"from {b:,} million to {a:,} million.",
        [
            cell_ev(BUS01, "BUS01a", f"{c}28", f"{b:,} million, YE March 2024"),
            cell_ev(BUS01, "BUS01a", f"{c}29", f"{a:,} million, YE March 2025"),
        ],
        [num(q2(abs(pct)), "%")],
    )

per_head = {
    get_cell(M[BUS01].path, "BUS01b", f"{c}8").displayed: disp(BUS01, "BUS01b", f"{c}29")
    for c in "BCDEFGIJ"
}
best = max(per_head, key=per_head.get)
best_col = next(c for c in "BCDEFGIJ" if text(BUS01, "BUS01b", f"{c}8") == best)
add(
    "transport",
    "aggregate",
    "Outside London, which English region had the most bus passenger journeys per head of "
    "population in the year ending March 2025, and how many?",
    f"{best} had the most, at {per_head[best]} journeys per head in the year ending March 2025.",
    [cell_ev(BUS01, "BUS01b", f"{best_col}29", f"{best}: {per_head[best]} journeys per head")],
    [{"type": "text", "text": best, "keywords": [best]}, num(per_head[best], None, 0)],
)

lon, eol = disp(BUS01, "BUS01d", "I44"), disp(BUS01, "BUS01d", "R44")
add(
    "transport",
    "compare",
    "In the year ending March 2025, how many percentage points higher was the concessionary "
    "share of bus journeys in England outside London than in London?",
    f"England outside London's concessionary share was {eol}% against {lon}% in London, "
    f"{f2(eol - lon)} percentage points higher.",
    [
        cell_ev(BUS01, "BUS01d", "R44", f"England outside London {eol}%"),
        cell_ev(BUS01, "BUS01d", "I44", f"London {lon}%"),
    ],
    [num(q2(eol - lon), "pp")],
)

gb25, gb24 = disp(BUS02KM, "BUS02b_km", "N29"), disp(BUS02KM, "BUS02b_km", "N28")
add(
    "transport",
    "compare",
    "By what percentage did local bus vehicle kilometres in Great Britain change from the year "
    "ending March 2024 to the year ending March 2025?",
    f"Vehicle kilometres rose by {f2((gb25 - gb24) / gb24 * 100)}%, from {gb24:,} million to "
    f"{gb25:,} million.",
    [
        cell_ev(BUS02KM, "BUS02b_km", "N28", f"{gb24:,} million km"),
        cell_ev(BUS02KM, "BUS02b_km", "N29", f"{gb25:,} million km"),
    ],
    [num(q2((gb25 - gb24) / gb24 * 100), "%")],
)

add(
    "transport",
    "semantics",
    "Can I add the vehicle miles workbook and the vehicle kilometres workbook together to get "
    "total bus activity?",
    "No. The miles and kilometre workbooks are alternative representations of the same "
    "distance, not additional activity, so adding them double counts it.",
    [(GUIDE_T, guide_loc(GUIDE_T, "alternative distance"), "miles and km are alternatives")],
    [
        {"type": "yes_no", "value": "no", "text": "No"},
        {
            "type": "text",
            "text": "alternative representations of the same distance",
            "keywords": ["alternative", "double"],
        },
    ],
)

add(
    "transport",
    "semantics",
    "Someone reported English metropolitan areas' constant-price operating revenue for the year "
    "ending March 2025 as zero because the cell shows [z]. Is that right?",
    "No. In this table [z] means not applicable, not zero: operating revenue for England cannot "
    "be split into metropolitan and non-metropolitan areas.",
    [
        cell_ev(BUS04II, "BUS04aii", "C29", "[z]"),
        (BUS04II, "sheet BUS04aii, row 3", "[z] = not applicable"),
        (BUS04II, "sheet Notes, note 4", "revenue cannot be split by metropolitan status"),
    ],
    [
        {"type": "yes_no", "value": "no", "text": "No"},
        {"type": "text", "text": "[z] means not applicable", "keywords": ["not applicable"]},
    ],
)

add(
    "transport",
    "semantics",
    "Can I compare London's operating revenue from the current-price table with the "
    "constant-price table to measure growth?",
    "No. Current-price and constant-price series have different meanings: constant prices are "
    "adjusted for inflation, so the two tables are not comparable for growth.",
    [
        (
            GUIDE_T,
            guide_loc(GUIDE_T, "constant-price series"),
            "current and constant prices differ",
        ),
        (BUS04II, "sheet Notes, note 1", "constant prices adjusted for inflation"),
    ],
    [
        {"type": "yes_no", "value": "no", "text": "No"},
        {"type": "text", "text": "different meanings inflation", "keywords": ["inflation"]},
    ],
)

occ = disp(BUS03, "BUS03b", "B29")
add(
    "transport",
    "lookup",
    "What was the average bus occupancy in London in the year ending March 2025?",
    f"Average occupancy on London's local buses was {occ} passengers "
    "in the year ending March 2025.",
    [cell_ev(BUS03, "BUS03b", "B29", f"London occupancy {occ}")],
    [num(occ, None, 1)],
)

f25, f22 = disp(BUS06, "BUS06b", "H30"), disp(BUS06, "BUS06b", "H27")
add(
    "transport",
    "compare",
    "How many buses (in thousands) were used as public service vehicles in Great Britain in "
    "2025, and how did that compare with 2022?",
    f"There were {f25} thousand buses in 2025, {f2(abs(f25 - f22))} thousand "
    f"{'fewer' if f25 < f22 else 'more'} than the {f22} thousand in 2022.",
    [
        cell_ev(BUS06, "BUS06b", "H30", f"{f25} thousand, 2025"),
        cell_ev(BUS06, "BUS06b", "H27", f"{f22} thousand, 2022"),
    ],
    [num(f25, None, 1), num(q2(abs(f25 - f22)), None, 1)],
)

add(
    "transport",
    "semantics",
    "What does [r] mean next to a figure in the constant-price operating revenue table?",
    "[r] marks a revised figure: the table's shorthand defines [r] as revision.",
    [(BUS04II, "sheet BUS04aii, row 3", "[r] = revision")],
    [{"type": "text", "text": "revised", "keywords": ["revis"]}],
)

add(
    "transport",
    "insufficient",
    "How many bus passenger journeys were made in Northern Ireland in the year ending March 2025?",
    "The supplied workbook cannot answer this: BUS01a covers England, Scotland, Wales and Great "
    "Britain, and has no Northern Ireland series.",
    [(BUS01, "sheet BUS01a, row 8", "columns cover England, Scotland, Wales and Great Britain")],
    [{"type": "text", "text": "no Northern Ireland series", "keywords": ["Northern Ireland"]}],
)

add(
    "transport",
    "insufficient",
    "What was London's operating cost per passenger journey at constant prices in the year "
    "ending March 2025?",
    "It is not available: the London column shows [z], meaning not applicable, so the table "
    "gives no constant-price cost per journey for London.",
    [cell_ev(BUS04II, "BUS04fii", "B30", "[z]")],
    [{"type": "text", "text": "not applicable [z]", "keywords": ["[z]"]}],
)

passes = disp(BUS08, "BUS08a", "C98")
add(
    "transport",
    "lookup",
    "How many concessionary bus journeys per pass were made on average in London in the year "
    "ending March 2025?",
    f"London averaged {passes} concessionary bus journeys per pass in the year ending March 2025.",
    [cell_ev(BUS08, "BUS08a", "C98", f"{passes} journeys per pass")],
    [num(passes, None, 0)],
)

add(
    "transport",
    "semantics",
    "Is the bus quality report web page another data table I should add to the workbook totals?",
    "No. The quality-report page is unchanged publisher documentation, not another data table.",
    [(GUIDE_T, guide_loc(GUIDE_T, "quality-report HTML"), "the quality report is documentation")],
    [
        {"type": "yes_no", "value": "no", "text": "No"},
        {"type": "text", "text": "documentation not a data table", "keywords": ["documentation"]},
    ],
)

ne25, ne19 = disp(BUS01, "BUS01a", "B29"), disp(BUS01, "BUS01a", "B23")
nw25, nw19 = disp(BUS01, "BUS01a", "C29"), disp(BUS01, "BUS01a", "C23")
ne_p, nw_p = ne25 / ne19 * 100, nw25 / nw19 * 100
closer = "North East" if ne_p > nw_p else "North West"
add(
    "transport",
    "compare",
    "Which recovered more of its year-ending-March-2019 bus journeys by the year ending March "
    "2025: the North East or the North West?",
    f"The {closer} recovered more: the North East reached {f2(ne_p)}% and the North West "
    f"{f2(nw_p)}% of their 2019 totals.",
    [
        cell_ev(BUS01, "BUS01a", "B29", f"North East {ne25}"),
        cell_ev(BUS01, "BUS01a", "B23", f"North East {ne19}"),
        cell_ev(BUS01, "BUS01a", "C29", f"North West {nw25}"),
        cell_ev(BUS01, "BUS01a", "C23", f"North West {nw19}"),
    ],
    [
        {"type": "text", "text": closer, "keywords": [closer]},
        num(q2(ne_p), "%"),
        num(q2(nw_p), "%"),
    ],
)

stored_h23 = stored(BUS01, "BUS01a", "H23")
add(
    "transport",
    "stored_displayed",
    "The London cell for the year ending March 2019 in BUS01a shows 2,198. What value is stored "
    "in the cell, to two decimal places, and which should this briefing use?",
    f"The cell stores {f2(stored_h23)} million journeys; this briefing uses the displayed "
    f"2,198 million, as the stated convention requires.",
    [cell_ev(BUS01, "BUS01a", "H23", "displayed 2,198; stored value has more decimals")],
    [
        num(q2(stored_h23), None),
        {"type": "text", "text": "use the displayed value", "keywords": ["displayed"]},
    ],
)

# ---------------------------------------------------------------- energy (20)


def col(year: int) -> str:
    if year == 1990:
        return "B"
    if year == 2024:
        return "AA"
    assert 2000 <= year <= 2023, year
    return chr(ord("C") + year - 2000)


def hsy(row: int, year: int) -> Decimal:
    return stored(HSY, "Tulokset", f"{col(year)}{row}")


def hev(row: int, year: int, claim: str):
    return cell_ev(HSY, "Tulokset", f"{col(year)}{row}", claim)


CITY_TOTAL = {"Helsinki": 22, "Espoo": 35, "Vantaa": 48, "Kauniainen": 61}
CITY_RES = {"Helsinki": 24, "Espoo": 37, "Vantaa": 50, "Kauniainen": 63}
CITY_SECTOR_TOTAL = {"Helsinki": 27, "Espoo": 40, "Vantaa": 53, "Kauniainen": 66}

v24, v23 = hsy(48, 2024), hsy(48, 2023)
add(
    "buildings_energy",
    "compare",
    "How much did Vantaa's total energy consumption change from 2023 to 2024, in GWh and percent?",
    f"Vantaa's total rose by {f2(v24 - v23)} GWh, or {f2((v24 - v23) / v23 * 100)}%.",
    [hev(48, 2024, "Vantaa total 2024"), hev(48, 2023, "Vantaa total 2023")],
    [num(q2(v24 - v23), "GWh"), num(q2((v24 - v23) / v23 * 100), "%")],
)

e24, e23 = hsy(32, 2024), hsy(32, 2023)
add(
    "buildings_energy",
    "compare",
    "By what percentage did Espoo's consumer electricity use (Kulutussähkö) change "
    "from 2023 to 2024?",
    f"Espoo's consumer electricity use rose by {f2((e24 - e23) / e23 * 100)}%, from "
    f"{f2(e23)} GWh to {f2(e24)} GWh.",
    [hev(32, 2024, "Espoo Kulutussähkö 2024"), hev(32, 2023, "Espoo Kulutussähkö 2023")],
    [num(q2((e24 - e23) / e23 * 100), "%")],
)

dh, ht = hsy(16, 2024), hsy(22, 2024)
add(
    "buildings_energy",
    "aggregate",
    "What share of Helsinki's total energy consumption in 2024 was district heating (Kaukolämpö)?",
    f"District heating was {f2(dh / ht * 100)}% of Helsinki's 2024 total.",
    [hev(16, 2024, "Helsinki Kaukolämpö 2024"), hev(22, 2024, "Helsinki total 2024")],
    [num(q2(dh / ht * 100), "%")],
)

k24 = hsy(61, 2024)
add(
    "buildings_energy",
    "lookup",
    "What was Kauniainen's total energy consumption in 2024, in GWh to two decimals?",
    f"Kauniainen's total was {f2(k24)} GWh in 2024.",
    [hev(61, 2024, "Kauniainen total 2024")],
    [num(q2(k24), "GWh")],
)

ind = {
    c: hsy(r, 2024)
    for c, r in {"Helsinki": 26, "Espoo": 39, "Vantaa": 52, "Kauniainen": 65}.items()
}
top = max(ind, key=ind.get)
top_row = {"Helsinki": 26, "Espoo": 39, "Vantaa": 52, "Kauniainen": 65}[top]
add(
    "buildings_energy",
    "compare",
    "Which city had the largest industry-sector (Teollisuus) energy consumption in 2024, "
    "and how much?",
    f"{top} had the largest, at {f2(ind[top])} GWh.",
    [hev(top_row, 2024, f"{top} Teollisuus 2024")],
    [{"type": "text", "text": top, "keywords": [top]}, num(q2(ind[top]), "GWh")],
)

t24, t90 = hsy(7, 2024), hsy(7, 1990)
add(
    "buildings_energy",
    "compare",
    "By what percentage did transport (Liikenne) energy use in the capital region change from "
    "1990 to 2024?",
    f"Capital-region transport energy rose by {f2((t24 - t90) / t90 * 100)}%, from {f2(t90)} GWh "
    f"to {f2(t24)} GWh.",
    [hev(7, 2024, "PKS Liikenne 2024"), hev(7, 1990, "PKS Liikenne 1990")],
    [num(q2((t24 - t90) / t90 * 100), "%")],
)

vr, vt = hsy(50, 2024), hsy(53, 2024)
add(
    "buildings_energy",
    "aggregate",
    "What share of Vantaa's 2024 energy use was residential (Asuminen)?",
    f"Residential use was {f2(vr / vt * 100)}% of Vantaa's 2024 total.",
    [hev(50, 2024, "Vantaa Asuminen 2024"), hev(53, 2024, "Vantaa total 2024")],
    [num(q2(vr / vt * 100), "%")],
)

hp = hsy(25, 2024)
add(
    "buildings_energy",
    "lookup",
    "How much energy did Helsinki's services sector (Palvelut) use in 2024?",
    f"Helsinki's services sector used {f2(hp)} GWh in 2024.",
    [hev(25, 2024, "Helsinki Palvelut 2024")],
    [num(q2(hp), "GWh")],
)

pks = hsy(9, 2024)
add(
    "buildings_energy",
    "stored_displayed",
    "The capital-region total for 2024 is displayed as 26,308 GWh. What is the stored value to "
    "two decimal places?",
    f"The stored value is {f2(pks)} GWh; the display format rounds it to 26,308.",
    [hev(9, 2024, "stored capital-region total 2024")],
    [num(q2(pks), "GWh")],
)

parts = sum((hsy(r, 2024) for r in range(3, 9)), Decimal(0))
gap = parts - pks
sums = abs(gap) < D2
add(
    "buildings_energy",
    "data_quality",
    "Do the six energy-type rows for the capital region in 2024 add up to the stated total?",
    f"{'Yes' if sums else 'No'}. The six rows sum to {f2(parts)} GWh against a total of "
    f"{f2(pks)} GWh, a difference of {f2(abs(gap))} GWh.",
    [hev(9, 2024, "capital-region total 2024")] + [hev(r, 2024, "energy-type row") for r in (3, 8)],
    [
        {"type": "yes_no", "value": "yes" if sums else "no", "text": "Yes" if sums else "No"},
        num(q2(parts), "GWh"),
    ],
)

o24, o00 = hsy(30, 2024), hsy(30, 2000)
add(
    "buildings_energy",
    "compare",
    "By what percentage did oil heating (Öljylämmitys) in Espoo change from 2000 to 2024?",
    f"Oil heating in Espoo fell by {f2(abs((o24 - o00) / o00 * 100))}%, "
    f"from {f2(o00)} GWh to {f2(o24)} GWh.",
    [hev(30, 2024, "Espoo Öljylämmitys 2024"), hev(30, 2000, "Espoo Öljylämmitys 2000")],
    [num(q2(abs((o24 - o00) / o00 * 100)), "%")],
)

note = text(HSY, "Tulokset", "D1")
add(
    "buildings_energy",
    "semantics",
    "What does the note at the top of the HSY results sheet say about heating figures?",
    "It says that for heating, year-to-year variation in consumption is smoothed with a "
    f'five-year moving average ("{note}").',
    [cell_ev(HSY, "Tulokset", "D1", "heating smoothed with a five-year moving average")],
    [{"type": "text", "text": "five-year moving average", "keywords": ["moving average"]}],
)

add(
    "buildings_energy",
    "insufficient",
    "What was Helsinki's energy consumption per resident in 2024?",
    "The workbook cannot answer this: it gives consumption in GWh by city and sector but no "
    "population figures, so per-resident use cannot be calculated from it.",
    [hev(22, 2024, "Helsinki total in GWh only")],
    [{"type": "text", "text": "no population figures", "keywords": ["population"]}],
)

add(
    "buildings_energy",
    "insufficient",
    "What was Espoo's total energy consumption in 2025?",
    "The workbook does not cover 2025: it runs from 1990 and 2000 to 2024, so 2025 consumption "
    "cannot be given.",
    [cell_ev(HSY, "Tulokset", "AA2", "last year column is 2024")],
    [{"type": "text", "text": "does not cover 2025", "keywords": ["2024"]}],
)

add(
    "buildings_energy",
    "aggregate",
    "What share of the capital region's 2024 total energy consumption was Helsinki's?",
    f"Helsinki accounted for {f2(ht / pks * 100)}% of the capital region's 2024 total.",
    [hev(22, 2024, "Helsinki total 2024"), hev(9, 2024, "capital-region total 2024")],
    [num(q2(ht / pks * 100), "%")],
)

ki = hsy(65, 2024)
add(
    "buildings_energy",
    "stored_displayed",
    "Kauniainen's industry-sector energy use for 2024 is displayed as a whole number. What is the "
    "stored value to two decimals?",
    f"The stored value is {f2(ki)} GWh.",
    [hev(65, 2024, "Kauniainen Teollisuus 2024 stored")],
    [num(q2(ki), "GWh")],
)

et = hsy(35, 2024)
add(
    "buildings_energy",
    "compare",
    "How much more energy did Vantaa use than Espoo in 2024, or the other way round?",
    f"{'Espoo' if et > v24 else 'Vantaa'} used {f2(abs(et - v24))} GWh more "
    f"({f2(et)} GWh in Espoo against {f2(v24)} GWh in Vantaa).",
    [hev(35, 2024, "Espoo total 2024"), hev(48, 2024, "Vantaa total 2024")],
    [
        {
            "type": "text",
            "text": "Espoo" if et > v24 else "Vantaa",
            "keywords": ["Espoo" if et > v24 else "Vantaa"],
        },
        num(q2(abs(et - v24)), "GWh"),
    ],
)

el24, el23 = hsy(5, 2024), hsy(5, 2023)
add(
    "buildings_energy",
    "compare",
    "How did electric heating (Sähkölämmitys) in the capital region change from 2023 to "
    "2024 in GWh?",
    f"Electric heating rose by {f2(el24 - el23)} GWh, from {f2(el23)} to {f2(el24)} GWh.",
    [hev(5, 2024, "PKS Sähkölämmitys 2024"), hev(5, 2023, "PKS Sähkölämmitys 2023")],
    [num(q2(el24 - el23), "GWh")],
)

for city in CITY_RES:
    r_row, t_row = CITY_RES[city], CITY_SECTOR_TOTAL[city]
    s_share = hsy(r_row, 2024) / hsy(t_row, 2024) * 100
    d_share = disp(HSY, "Tulokset", f"AA{r_row}") / disp(HSY, "Tulokset", f"AA{t_row}") * 100
    if q2(s_share) != q2(d_share):
        break
add(
    "buildings_energy",
    "stored_displayed",
    f"Using stored values as the convention requires, what was {city}'s residential share of "
    "its 2024 total, and would the displayed whole-GWh figures give the same answer?",
    f"With stored values the share is {f2(s_share)}%; the displayed figures would give "
    f"{f2(d_share)}%, so they differ and the stored result is the one to report.",
    [hev(r_row, 2024, f"{city} Asuminen 2024"), hev(t_row, 2024, f"{city} total 2024")],
    [num(q2(s_share), "%")],
)

p24, p00 = pks, hsy(9, 2000)
add(
    "buildings_energy",
    "compare",
    "By what percentage did the capital region's total energy consumption grow from 2000 to 2024?",
    f"It grew by {f2((p24 - p00) / p00 * 100)}%, from {f2(p00)} GWh to {f2(p24)} GWh.",
    [hev(9, 2024, "PKS total 2024"), hev(9, 2000, "PKS total 2000")],
    [num(q2((p24 - p00) / p00 * 100), "%")],
)

# ---------------------------------------------------------------- greenery (20)

trees = pd.read_csv(M[TREES_CSV].path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
groups = pd.read_csv(M[GROUPS_CSV].path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
orders = pd.read_csv(M[DATASET_CSV].path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
gtrees = gpd.read_file(M[TREES_GEOJSON].path)
zones = gpd.read_file(M[ZONES].path)
gpkg = gpd.read_file(M[GPKG].path)

add(
    "urban_greenery",
    "count",
    "How many tree records are in tpo-trees.csv?",
    f"tpo-trees.csv contains {len(trees):,} tree records.",
    [(TREES_CSV, "header row", f"{len(trees):,} data rows")],
    [num(Decimal(len(trees)), None, 0)],
)

blank = int((trees["confirmed-date"] == "").sum())
add(
    "urban_greenery",
    "data_quality",
    "How many tree records in tpo-trees.csv have no confirmed-date, and what share is that?",
    f"{blank} of {len(trees):,} records have no confirmed-date, "
    f"{f2(Decimal(blank) / len(trees) * 100)}%.",
    [(TREES_CSV, "column confirmed-date", f"{blank} empty values")],
    [num(Decimal(blank), None, 0), num(q2(Decimal(blank) / len(trees) * 100), "%")],
)

blank_made = int((orders["made_date"] == "").sum())
add(
    "urban_greenery",
    "data_quality",
    "How many orders in trees-dataset-2026-06-23.csv have no made_date?",
    f"{blank_made} of the {len(orders)} order records have no made_date.",
    [(DATASET_CSV, "column made_date", f"{blank_made} empty values")],
    [num(Decimal(blank_made), None, 0)],
)

add(
    "urban_greenery",
    "semantics",
    "trees-2026-06-23.geojson has more tree features than tpo-trees.csv has rows. Can I treat "
    "the extra features as trees missing from the CSV?",
    f"No. The GeoJSON has {len(gtrees):,} features and the CSV {len(trees):,} rows, a difference "
    f"of {len(gtrees) - len(trees)}, but the formats are separate publications that must not be "
    f"assumed to be row-for-row copies.",
    [
        (TREES_GEOJSON, "/features", f"{len(gtrees):,} features"),
        (TREES_CSV, "header row", f"{len(trees):,} rows"),
        (GUIDE_G, guide_loc(GUIDE_G, "Similar filenames"), "formats are not row-for-row copies"),
    ],
    [
        {"type": "yes_no", "value": "no", "text": "No"},
        num(Decimal(len(gtrees) - len(trees)), None, 0),
    ],
)

add(
    "urban_greenery",
    "spatial",
    "What coordinate reference systems do trees.gpkg and trees-2026-06-23.geojson use?",
    f"trees.gpkg uses {gpkg.crs.to_string()} (British National Grid) and the GeoJSON uses "
    f"{gtrees.crs.to_string()} (WGS 84).",
    [
        (GPKG, "layer ezytreevcurrenttpotrees", "CRS EPSG:27700"),
        (TREES_GEOJSON, "/features", "CRS EPSG:4326"),
    ],
    [
        {"type": "text", "text": "EPSG:27700", "keywords": ["27700"]},
        {"type": "text", "text": "EPSG:4326", "keywords": ["4326"]},
    ],
)

invalid = zones[~zones.is_valid]
add(
    "urban_greenery",
    "spatial",
    "How many zone polygons in zones-2026-06-23.geojson are invalid geometries?",
    f"{len(invalid)} of the {len(zones)} zone polygons are invalid; they should be measured as "
    f"published, not repaired.",
    [(ZONES, f"properties.reference={r}", "invalid geometry") for r in invalid["reference"]],
    [num(Decimal(len(invalid)), None, 0)],
)

bng = zones.to_crs(27700)
valid = bng[bng.is_valid].assign(area=lambda g: g.area).sort_values("area", ascending=False)
for ref in [valid["reference"].iloc[0], valid["reference"].iloc[9]]:
    ha = Decimal(repr(float(bng.loc[bng["reference"] == ref].area.iloc[0]))) / 10000
    add(
        "urban_greenery",
        "spatial",
        f"What is the area of zone {ref} in hectares, measured in British National Grid metres?",
        f"Zone {ref} covers {f2(ha)} hectares in British National Grid (EPSG:27700).",
        [(ZONES, f"properties.reference={ref}", f"area {f2(ha)} ha")],
        [num(q2(ha), "ha")],
    )

add(
    "urban_greenery",
    "count",
    "How many zones are in zones-2026-06-23.geojson, and how many group records are in "
    "tpo-groups.csv?",
    f"The GeoJSON has {len(zones)} zones and tpo-groups.csv has {len(groups)} records.",
    [
        (ZONES, "/features", f"{len(zones)} features"),
        (GROUPS_CSV, "header row", f"{len(groups)} rows"),
    ],
    [num(Decimal(len(zones)), None, 0), num(Decimal(len(groups)), None, 0)],
)


def order_days(ref: str):
    row = orders.loc[orders["reference"] == ref].iloc[0]
    made, conf = row["made_date"], row["confirmed_date"]
    return made, conf, (pd.Timestamp(conf) - pd.Timestamp(made)).days if made and conf else None


full = orders[(orders["made_date"] != "") & (orders["confirmed_date"] != "")]["reference"]
for ref in [full.iloc[0], full.iloc[len(full) // 2]]:
    made, conf, days = order_days(ref)
    add(
        "urban_greenery",
        "lookup",
        f"When was {ref} made and confirmed, and how many days passed between them?",
        f"{ref} was made on {made} and confirmed on {conf}, {days} days later.",
        [
            (
                DATASET_CSV,
                f"reference={ref}, made_date and confirmed_date",
                f"made {made}, confirmed {conf}",
            )
        ],
        [
            {"type": "date", "value": made, "text": made},
            {"type": "date", "value": conf, "text": conf},
            num(Decimal(days), "days", 0),
        ],
    )

no_conf = orders[(orders["reference"] != "") & (orders["confirmed_date"] == "")]["reference"].iloc[
    0
]
add(
    "urban_greenery",
    "insufficient",
    f"How many days did it take to confirm {no_conf} after it was made?",
    f"This cannot be calculated: {no_conf} has no confirmed_date in the dataset.",
    [(DATASET_CSV, f"reference={no_conf}, confirmed_date", "empty")],
    [{"type": "text", "text": "no confirmed_date", "keywords": ["confirmed"]}],
)

zt = groups["tree-preservation-zone-type"].value_counts()
add(
    "urban_greenery",
    "count",
    "How many tpo-groups.csv records are of each tree-preservation zone type?",
    "; ".join(f"{k}: {v}" for k, v in zt.items()) + ".",
    [(GROUPS_CSV, "column tree-preservation-zone-type", "value counts")],
    [num(Decimal(int(v)), None, 0) for v in zt.to_numpy()],
)

distinct = trees["reference"].str.split(".").str[0].nunique()
add(
    "urban_greenery",
    "count",
    "How many distinct tree preservation orders do the tree records in tpo-trees.csv belong to?",
    f"The {len(trees):,} tree records belong to {distinct} distinct orders (the part of the "
    f"reference before the dot).",
    [(TREES_CSV, "column reference", f"{distinct} distinct order prefixes")],
    [num(Decimal(distinct), None, 0)],
)

ended = int((orders["end_date"] != "").sum())
add(
    "urban_greenery",
    "count",
    "How many orders in trees-dataset-2026-06-23.csv have an end_date?",
    f"{ended} of the {len(orders)} orders have an end_date.",
    [(DATASET_CSV, "column end_date", f"{ended} non-empty values")],
    [num(Decimal(ended), None, 0)],
)

add(
    "urban_greenery",
    "insufficient",
    "Does tpo-trees.csv show that I currently have permission to fell tree TPO527.T3?",
    "No. These are frozen snapshots; they do not establish current legal permission for work on "
    "a particular tree.",
    [(GUIDE_G, guide_loc(GUIDE_G, "legal permission"), "no current legal permission")],
    [
        {"type": "yes_no", "value": "no", "text": "No"},
        {
            "type": "text",
            "text": "do not establish current legal permission",
            "keywords": ["permission"],
        },
    ],
)

a, b = gpkg.iloc[0], gpkg.iloc[1]
dist = Decimal(repr(float(a.geometry.distance(b.geometry))))
add(
    "urban_greenery",
    "spatial",
    f"How far apart are trees {a['reference']} and {b['reference']} in trees.gpkg, in metres?",
    f"They are {f2(dist)} metres apart in British National Grid (EPSG:27700).",
    [
        (GPKG, f"layer ezytreevcurrenttpotrees, feature reference={a['reference']}", "point"),
        (GPKG, f"layer ezytreevcurrenttpotrees, feature reference={b['reference']}", "point"),
    ],
    [num(q2(dist), "m")],
)

dups = int(trees["reference"].duplicated().sum())
add(
    "urban_greenery",
    "data_quality",
    "Are there duplicate reference values in tpo-trees.csv?",
    "No, every reference value in tpo-trees.csv is unique."
    if dups == 0
    else f"Yes, {dups} reference values are duplicated.",
    [(TREES_CSV, "column reference", f"{dups} duplicates")],
    [
        {
            "type": "yes_no",
            "value": "no" if dups == 0 else "yes",
            "text": "No" if dups == 0 else "Yes",
        }
    ],
)

made = orders.loc[orders["made_date"] != "", "made_date"]
first = made.min()
first_ref = orders.loc[orders["made_date"] == first, "reference"].iloc[0]
add(
    "urban_greenery",
    "aggregate",
    "What is the earliest made_date in trees-dataset-2026-06-23.csv, and which order is it?",
    f"The earliest is {first}, for {first_ref}.",
    [(DATASET_CSV, f"reference={first_ref}, made_date", first)],
    [
        {"type": "date", "value": first, "text": first},
        {"type": "text", "text": first_ref, "keywords": [first_ref]},
    ],
)

n19 = int(made.str.startswith("2019").sum())
add(
    "urban_greenery",
    "count",
    "How many orders in trees-dataset-2026-06-23.csv were made in 2019?",
    f"{n19} orders have a made_date in 2019.",
    [(DATASET_CSV, "column made_date", f"{n19} values in 2019")],
    [num(Decimal(n19), None, 0)],
)

felled = int(gtrees["felled-date"].fillna("").astype(str).str.strip().ne("").sum())
add(
    "urban_greenery",
    "data_quality",
    "How many tree features in trees-2026-06-23.geojson have a felled-date?",
    f"{felled} of the {len(gtrees):,} tree features have a felled-date.",
    [(TREES_GEOJSON, "/features", f"{felled} with felled-date")],
    [num(Decimal(felled), None, 0)],
)

# ----------------------------------------------------------------

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(ROWS)
print(f"wrote {len(ROWS)} rows to {OUT}")

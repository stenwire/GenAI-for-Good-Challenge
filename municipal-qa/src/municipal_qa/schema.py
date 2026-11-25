from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- Questions ----------


class Domain(str, Enum):
    BUILDINGS_ENERGY = "buildings_energy"
    TRANSPORT = "transport"
    URBAN_GREENERY = "urban_greenery"


class Question(BaseModel):
    id: str
    case_id: str
    domain: Domain
    question: str
    context: str
    source_ids: list[str]
    split: Literal["train", "dev", "test"]
    reference_answer: str | None = None  # train/dev only
    reference_evidence: list[str] | None = None  # train/dev only


# ---------- Sources ----------


class SourceInfo(BaseModel):
    source_id: str  # "SRC_7bc924533043"
    path: str  # absolute path to extracted file
    original_file: str  # "tpo-trees.csv"
    source_set: Literal["Train", "Test"]
    publisher: str
    licence: str
    bytes: int
    sha256: str
    suffix: str  # ".csv", lower-case


class Locator(BaseModel):
    """Where a fact lives. `text` is what appears in the evidence line."""

    source_id: str
    kind: Literal[
        "file",
        "cell",
        "cell_range",
        "sheet",
        "row",
        "rows",
        "column",
        "record",
        "feature",
        "layer",
        "page",
        "lines",
        "section",
        "json_pointer",
        "xpath",
        "table_row",
        "sql",
        "raster",
        "pointcloud",
        "ifc_entity",
    ]
    text: str  # e.g. "sheet BUS01a, cell H23" | "reference=TPO453, made_date" | "page 4"


class Fact(BaseModel):
    """One value produced by a tool, with provenance."""

    name: str  # "london_2025_vs_2019_pct"
    value: float | int | str | bool | None
    unit: str | None = None  # "%", "ha", "GWh", "m", "records", "days"
    display: str  # value formatted per Conventions, e.g. "82.85%"
    claim: str  # one-sentence claim for the evidence line
    locators: list[Locator] = Field(default_factory=list)
    note: str | None = None  # caveats, e.g. "[z] means not applicable"


class ToolResult(BaseModel):
    ok: bool
    op: str
    args: dict[str, Any]
    facts: list[Fact] = Field(default_factory=list)
    preview: str | None = None  # small text preview for the LLM (max ~1,500 chars)
    error: str | None = None  # message fed back to the planner on failure
    elapsed_ms: int = 0


class Passage(BaseModel):
    """A retrievable text unit (documentation, notes, dictionary entries, cell labels)."""

    passage_id: str  # f"{source_id}:{n}"
    source_id: str
    text: str
    locator: Locator
    kind: Literal["doc", "notes_sheet", "dictionary", "header", "metadata", "body"]
    score: float | None = None


# ---------- Profiles ----------


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    n_null: int
    n_unique: int | None
    examples: list[str]  # up to 5 distinct values, as strings


class TableProfile(BaseModel):
    table_name: str  # sheet name, layer name, csv file name, sql table
    n_rows: int
    columns: list[ColumnProfile]
    header_row: int | None = None  # 1-based row of header for spreadsheets
    crs: str | None = None  # vector layers, e.g. "EPSG:27700"
    geometry_type: str | None = None
    bounds: list[float] | None = None


class SourceProfile(BaseModel):
    source: SourceInfo
    reader: str  # reader name used
    kind: Literal[
        "table",
        "workbook",
        "vector",
        "raster",
        "pointcloud",
        "document",
        "structured",
        "database",
        "bim",
        "binary",
    ]
    tables: list[TableProfile] = Field(default_factory=list)
    summary: str  # <= 1,200 chars, written deterministically
    is_documentation: bool  # guides, dictionaries, readmes, methods, metadata
    warnings: list[str] = Field(default_factory=list)


# ---------- Planning ----------


class QuestionType(str, Enum):
    LOOKUP = "lookup"  # read one or a few values
    COUNT = "count"
    AGGREGATE = "aggregate"  # sum/mean/max/min/share
    COMPARE = "compare"  # A vs B, change, difference
    SPATIAL = "spatial"
    RASTER = "raster"
    DATA_QUALITY = "data_quality"  # nulls, duplicates, set differences, joins
    SEMANTICS = "semantics"  # what does a field/code mean, premise checks
    CRITERIA = "criteria"  # check given values against stated thresholds
    INSUFFICIENT = "insufficient"  # sources cannot answer


class SubQuestion(BaseModel):
    text: str  # one answer element, e.g. "2025 vs 2019 % for London"
    expects: Literal["number", "text", "yes_no", "list", "date", "identifier"]
    unit: str | None = None


class Route(BaseModel):
    qtype: QuestionType
    sub_questions: list[SubQuestion]
    primary_source_ids: list[str]
    premise_to_check: str | None = None  # the user's assumption, if any


class Step(BaseModel):
    op: str  # must exist in tools.registry
    args: dict[str, Any]
    save_as: str  # name other steps can reference as "$name"


class Plan(BaseModel):
    steps: list[Step]
    rationale: str | None = None


# ---------- Conventions (parsed from Context) ----------


class Conventions(BaseModel):
    value_mode: Literal["stored", "displayed", "unspecified"] = "unspecified"
    decimals: int = 2
    decimals_overrides: dict[str, int] = Field(default_factory=dict)  # {"ha": 5}
    rounding: Literal["half_up", "half_even", "either"] = "half_up"
    round_at_end: bool = True
    crs: str | None = None  # "EPSG:2056"
    radius_inclusive: bool | None = None
    repair_geometry: bool = False
    counts_mean: Literal["records", "unique_keys", "unspecified"] = "unspecified"
    preserve_ids_as_strings: bool = True
    time_shift: bool = False
    hypothetical_rules: bool = False
    raw_context: str


# ---------- Output ----------


class CallRecord(BaseModel):
    purpose: str  # "route" | "plan" | "repair" | "compose" | "check"
    prompt_tokens: int
    generated_tokens: int
    elapsed_ms: int


class AnswerRecord(BaseModel):
    question_id: str
    answer: str  # final answer text, no "## " lines
    evidence: list[str]  # "SRC_x | locator | claim", one line each
    route: Route | None = None
    plan: Plan | None = None
    results: list[ToolResult] = Field(default_factory=list)
    calls: list[CallRecord] = Field(default_factory=list)
    total_calls: int = 0
    total_generated_tokens: int = 0
    fallback_used: bool = False
    error: str | None = None

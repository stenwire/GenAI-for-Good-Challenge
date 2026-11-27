"""Score tables for a scored run (card T4). Prints text only; writes no files."""

from __future__ import annotations

import pandas as pd

from municipal_qa.evaluation.scorer import aggregate


def _table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append(
            "| " + " | ".join(f"{v:.4f}" if isinstance(v, float) else str(v) for v in row) + " |"
        )
    return "\n".join(lines)


def summary_tables(per_question: pd.DataFrame) -> dict[str, pd.DataFrame]:
    agg = aggregate(per_question)
    domain = (
        per_question.groupby("domain")
        .agg(questions=("score", "size"), cases=("case_id", "nunique"))
        .join(pd.Series(agg["by_domain"], name="score"))
        .reset_index()
    )
    qtype = (
        per_question.groupby("qtype")
        .agg(
            questions=("score", "size"),
            score=("score", "mean"),
            c=("c_mean", "mean"),
            e=("e", "mean"),
        )
        .reset_index()
        .sort_values("score")
    )
    worst = per_question.sort_values("score").head(20)[
        ["question_id", "domain", "qtype", "c_mean", "e", "score"]
    ]
    overall = pd.DataFrame(
        [
            {
                "overall": agg["overall"],
                "questions": len(per_question),
                "answered": int(per_question["answered"].sum()),
                "mean_e": float(per_question["e"].mean()) if len(per_question) else 0.0,
            }
        ]
    )
    return {"overall": overall, "domain": domain, "qtype": qtype, "worst": worst}


def render(per_question: pd.DataFrame) -> str:
    t = summary_tables(per_question)
    parts = [
        "## Overall (equal-weight mean of domains)",
        _table(t["overall"]),
        "## By domain (mean of case means)",
        _table(t["domain"]),
        "## By question type (mean of question scores)",
        _table(t["qtype"]),
        "## Worst 20 questions",
        _table(t["worst"]),
    ]
    return "\n\n".join(parts)


def diff(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Per-domain score of run b minus run a."""
    da = pd.Series(aggregate(a)["by_domain"], name="a")
    db = pd.Series(aggregate(b)["by_domain"], name="b")
    out = pd.concat([da, db], axis=1)
    out["delta"] = out["b"] - out["a"]
    return out.reset_index(names="domain")


def plan_stats(records) -> pd.DataFrame:
    """Plans made, plans whose every step ran (after at most the configured repairs), repairs."""
    rows = []
    for r in records.values():
        repairs = sum(1 for c in r.calls if c.purpose == "repair")
        rows.append(
            {
                "route": r.route.qtype.value if r.route else "none",
                "planned": r.plan is not None and bool(r.plan.steps),
                "executed": bool(r.results) and all(x.ok for x in r.results),
                "repairs": repairs,
                "calls": r.total_calls,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    by = df.groupby("route").agg(
        questions=("planned", "size"),
        planned=("planned", "sum"),
        executed=("executed", "sum"),
        repairs=("repairs", "sum"),
    )
    total = by.sum().to_frame().T.assign(route="all").set_index("route")
    out = pd.concat([by, total]).reset_index()
    out["executed_share"] = (out["executed"] / out["planned"].where(out["planned"] > 0)).fillna(0.0)
    return out.astype({"questions": int, "planned": int, "executed": int, "repairs": int})


def render_plans(records) -> str:
    df = plan_stats(records)
    if df.empty or not df["planned"].sum():
        return ""
    return "## Plans (executed = every step ok after at most the allowed repairs)\n\n" + _table(df)

"""Deterministic verification primitives.

The model never computes a number. It chooses an operation and its arguments;
this module computes, and the exact call is recorded in the claim record so a
reviewer can re-run it by hand.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

AGGS = ("mean", "median", "sum", "min", "max", "count", "nunique", "std")


class VerifyError(RuntimeError):
    pass


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as a tool error
        raise VerifyError(f"could not read {path.name} as CSV: {exc}") from exc


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df.columns:
        raise VerifyError(
            f"column '{name}' not in {list(df.columns)}. Run inspect-csv first."
        )
    return df[name]


def _apply_filters(df: pd.DataFrame, filters: list[dict] | None) -> pd.DataFrame:
    """filters: [{"column": c, "op": "eq|ne|lt|lte|gt|gte|in", "value": v}]"""
    if not filters:
        return df
    for f in filters:
        col = _column(df, f["column"])
        op, val = f.get("op", "eq"), f["value"]
        if op == "eq":
            df = df[col == val]
        elif op == "ne":
            df = df[col != val]
        elif op == "lt":
            df = df[col < val]
        elif op == "lte":
            df = df[col <= val]
        elif op == "gt":
            df = df[col > val]
        elif op == "gte":
            df = df[col >= val]
        elif op == "in":
            df = df[col.isin(val)]
        else:
            raise VerifyError(f"unknown filter op '{op}'")
    if df.empty:
        raise VerifyError("filters matched no rows")
    return df


def _agg(series: pd.Series, agg: str) -> float:
    if agg not in AGGS:
        raise VerifyError(f"unknown aggregate '{agg}', expected one of {list(AGGS)}")
    if agg in ("count", "nunique"):
        return float(getattr(series, agg)())
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().all():
        raise VerifyError(f"column is not numeric, cannot compute {agg}")
    return float(getattr(numeric, agg)())


# -- operations ------------------------------------------------------------


def inspect_csv(path: Path, max_rows: int = 5) -> dict:
    df = _read_csv(path)
    return {
        "rows": int(len(df)),
        "columns": [
            {
                "name": str(c),
                "dtype": str(df[c].dtype),
                "nunique": int(df[c].nunique()),
                "sample_values": [_j(v) for v in df[c].dropna().unique()[:5]],
            }
            for c in df.columns
        ],
        "head": [{str(k): _j(v) for k, v in row.items()} for row in df.head(max_rows).to_dict("records")],
    }


def csv_stat(path: Path, column: str, agg: str = "mean", filters=None) -> dict:
    df = _apply_filters(_read_csv(path), filters)
    value = _agg(_column(df, column), agg)
    return {
        "value": value,
        "rows_used": int(len(df)),
        "description": f"{agg}({column}) over {len(df)} rows of {path.name}"
        + (f" filtered by {filters}" if filters else ""),
    }


def csv_unique_count(path: Path, column: str, filters=None) -> dict:
    df = _apply_filters(_read_csv(path), filters)
    values = sorted(str(v) for v in _column(df, column).dropna().unique())
    return {
        "value": float(len(values)),
        "values": values[:50],
        "rows_used": int(len(df)),
        "description": f"{len(values)} distinct values of {column} in {path.name}",
    }


def csv_delta(
    path: Path,
    value_column: str | None = None,
    group_column: str | None = None,
    baseline: str | None = None,
    treatment: str | None = None,
    baseline_column: str | None = None,
    treatment_column: str | None = None,
    agg: str = "mean",
    filters=None,
) -> dict:
    """Compare a baseline against a treatment two ways.

    Shape A (two columns):  baseline_column vs treatment_column.
    Shape B (one column, two groups): value_column split by group_column.

    Returns BOTH interpretations of "improved by X":
      relative_pct_change  -- (t - b) / |b| * 100
      absolute_point_delta -- t - b   (percentage points, when the column is a percentage)
    A manuscript sentence rarely says which it means, so the comparison step
    checks the reported value against both and reports which one matches.
    """
    df = _apply_filters(_read_csv(path), filters)

    if baseline_column and treatment_column:
        b = _agg(_column(df, baseline_column), agg)
        t = _agg(_column(df, treatment_column), agg)
        desc = f"{agg}({treatment_column}) vs {agg}({baseline_column}) in {path.name}"
    elif value_column and group_column and baseline is not None and treatment is not None:
        col = _column(df, group_column)
        b_rows, t_rows = df[col.astype(str) == str(baseline)], df[col.astype(str) == str(treatment)]
        if b_rows.empty or t_rows.empty:
            present = sorted(str(v) for v in col.dropna().unique())[:20]
            raise VerifyError(
                f"group '{baseline}' or '{treatment}' not found in {group_column}. Present: {present}"
            )
        b = _agg(b_rows[value_column], agg)
        t = _agg(t_rows[value_column], agg)
        desc = (
            f"{agg}({value_column}) for {group_column}={treatment} vs "
            f"{group_column}={baseline} in {path.name}"
        )
    else:
        raise VerifyError(
            "csv_delta needs either (baseline_column, treatment_column) or "
            "(value_column, group_column, baseline, treatment)"
        )

    if b == 0:
        rel = None
    else:
        rel = (t - b) / abs(b) * 100.0
    return {
        "baseline_value": b,
        "treatment_value": t,
        "absolute_point_delta": t - b,
        "relative_pct_change": rel,
        "value": rel if rel is not None else t - b,
        "rows_used": int(len(df)),
        "description": desc,
    }


def text_search(paths: list[Path], pattern: str, regex: bool = False, max_results: int = 20) -> dict:
    """Literal or regex search across extracted text and plain-text originals."""
    rx = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
    hits: list[dict] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except Exception:  # noqa: BLE001 - binary or unreadable file, skip
            continue
        page = None
        for lineno, line in enumerate(text.splitlines(), start=1):
            page_marker = re.fullmatch(r"<<<PAGE (\d+)>>>", line.strip())
            if page_marker:
                page = int(page_marker.group(1))
                continue
            if rx.search(line):
                hits.append(
                    {
                        "path": str(p),
                        "line": lineno,
                        "page": page,
                        "text": line.strip()[:300],
                    }
                )
                if len(hits) >= max_results:
                    return {"hits": hits, "truncated": True}
    return {"hits": hits, "truncated": False}


# -- comparison ------------------------------------------------------------


def compare(
    reported: float,
    candidates: dict[str, float | None],
    tolerance: float = 0.5,
    tolerance_mode: str = "absolute",
) -> dict:
    """Compare a reported value against one or more computed interpretations.

    Returns 'supported' if any candidate matches within tolerance, naming which.
    Otherwise 'conflicting' against the primary candidate.
    """
    results = []
    for label, value in candidates.items():
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        diff = abs(reported - value)
        limit = tolerance if tolerance_mode == "absolute" else abs(value) * tolerance / 100.0
        results.append(
            {"interpretation": label, "computed": value, "difference": diff, "within_tolerance": diff <= limit}
        )
    if not results:
        return {
            "status": "unverifiable",
            "reported_value": reported,
            "computed_value": None,
            "comparisons": [],
            "explanation": "No computable value was produced for this claim.",
        }
    match = next((r for r in results if r["within_tolerance"]), None)
    primary = match or results[0]
    return {
        "status": "supported" if match else "conflicting",
        "reported_value": reported,
        "computed_value": primary["computed"],
        "matched_interpretation": primary["interpretation"],
        "comparisons": results,
        "explanation": (
            f"Reported {reported:g} matches the computed {primary['computed']:g} "
            f"({primary['interpretation']}) within a tolerance of {tolerance:g}."
            if match
            else f"Reported {reported:g} does not match the computed "
            f"{primary['computed']:g} ({primary['interpretation']}); "
            f"difference {primary['difference']:g} exceeds the tolerance of {tolerance:g}."
        ),
    }


def _j(v):
    """Make a numpy/pandas scalar JSON-safe."""
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:  # noqa: BLE001 - not a numpy scalar
            pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v

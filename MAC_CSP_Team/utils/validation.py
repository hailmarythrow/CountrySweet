"""Data-quality and filtering helpers for workbook-backed views."""
from __future__ import annotations

import pandas as pd


def verified_measure(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return source values without coercing missing cells to zero."""
    if column not in frame:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def quality_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in frame.columns:
        missing = int(frame[column].isna().sum())
        rows.append({"Field": str(column), "Rows": len(frame), "Missing": missing, "Present": len(frame) - missing})
    return pd.DataFrame(rows)


def options(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame:
        return []
    values = frame[column].dropna().astype(str).str.strip()
    return sorted(value for value in values.unique().tolist() if value)


def apply_filters(frame: pd.DataFrame, filters: dict[str, list[str]]) -> pd.DataFrame:
    result = frame.copy()
    for column, selected in filters.items():
        # The importer keeps a canonical schema across worksheets. A source
        # sheet can therefore have an all-missing dimension (for example,
        # purchasing loads may not include variety or product). Such a field
        # cannot meaningfully constrain that worksheet, so leave it alone.
        if column in result and result[column].notna().any():
            result = result[result[column].astype("string").isin(selected)]
    return result


def source_period(frame: pd.DataFrame) -> str:
    if "reporting_date" in frame and frame["reporting_date"].notna().any():
        dates = frame["reporting_date"].dropna()
        return f"{dates.min():%b %d, %Y} – {dates.max():%b %d, %Y}"
    if "year" in frame and frame["year"].notna().any():
        years = frame["year"].dropna().astype(int).unique().tolist()
        weeks = frame["week"].dropna().astype(int).unique().tolist() if "week" in frame else []
        year_label = ", ".join(str(year) for year in sorted(years))
        return f"Crop year {year_label}" + (f", weeks {min(weeks)}–{max(weeks)}" if weeks else "")
    return "Reporting period not supplied"


def filter_caption(filters: dict[str, list[str]]) -> str:
    active = [f"{column}: {', '.join(values)}" for column, values in filters.items() if values]
    return "All available values" if not active else " · ".join(active)

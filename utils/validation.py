"""Data-quality and filtering helpers for workbook-backed views."""
from __future__ import annotations

import math

import pandas as pd


_MONTH_ORDER = {
    month.casefold(): index
    for index, month in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


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


def filter_label(value: object) -> str | None:
    """Return a stable, user-facing filter label without changing source data."""
    if value is None or pd.isna(value):
        return None
    label = str(value).strip()
    if not label:
        return None
    numeric = pd.to_numeric(label.replace(",", ""), errors="coerce")
    if pd.notna(numeric) and math.isfinite(float(numeric)) and float(numeric).is_integer():
        return str(int(float(numeric)))
    return label


def _classification_rank(value: object) -> int:
    label = filter_label(value)
    if not label:
        return 2
    normalized = label.casefold()
    if normalized in {"conventional", "conv"} or normalized.startswith("conv."):
        return 0
    if normalized in {"organic", "org"} or normalized.startswith("org."):
        return 1
    return 2


def _sort_key(label: str) -> tuple[int, float | str, str]:
    numeric = pd.to_numeric(label.replace(",", ""), errors="coerce")
    if pd.notna(numeric) and math.isfinite(float(numeric)):
        return 0, float(numeric), label.casefold()
    return 1, label.casefold(), label


def sort_filter_options(frame: pd.DataFrame, column: str) -> list[str]:
    """Return consistently sorted filter labels while preserving source text.

    Numeric values are ordered by numeric value. Product dimensions are grouped
    by explicit classification when available, with prefix inference only as a
    fallback. Unclassified values remain after Conventional and Organic.
    """
    if column not in frame:
        return []

    labels = []
    for value in frame[column].tolist():
        label = filter_label(value)
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return []

    numeric_values = [pd.to_numeric(label.replace(",", ""), errors="coerce") for label in labels]
    if all(pd.notna(value) and math.isfinite(float(value)) for value in numeric_values):
        return sorted(labels, key=_sort_key)

    month_labels = {label.casefold(): label for label in labels}
    if month_labels and all(label in _MONTH_ORDER for label in month_labels):
        return sorted(labels, key=lambda label: (_MONTH_ORDER[label.casefold()], label.casefold(), label))

    grouped_columns = {"classification", "product", "variety", "size"}
    if column not in grouped_columns:
        return sorted(labels, key=_sort_key)

    classification_by_label: dict[str, int] = {}
    if "classification" in frame and column != "classification":
        for value, classification in zip(frame[column].tolist(), frame["classification"].tolist()):
            label = filter_label(value)
            if not label:
                continue
            explicit_rank = _classification_rank(classification)
            prefix_rank = _classification_rank(label)
            rank = explicit_rank if explicit_rank < 2 else prefix_rank
            classification_by_label[label] = min(classification_by_label.get(label, 2), rank)

    if column == "classification":
        classification_by_label = {label: _classification_rank(label) for label in labels}

    return sorted(labels, key=lambda label: (classification_by_label.get(label, 2), label.casefold(), label))


def options(frame: pd.DataFrame, column: str) -> list[str]:
    """Backward-compatible public wrapper for all dashboard filter options."""
    return sort_filter_options(frame, column)


def apply_filters(frame: pd.DataFrame, filters: dict[str, list[str]]) -> pd.DataFrame:
    result = frame.copy()
    for column, selected in filters.items():
        # The importer keeps a canonical schema across worksheets. A source
        # sheet can therefore have an all-missing dimension (for example,
        # purchasing loads may not include variety or product). Such a field
        # cannot meaningfully constrain that worksheet, so leave it alone.
        # A blank selection means "All". This also prevents a widget that has
        # not yet received its options from filtering every valid row away.
        if column in result and selected and result[column].notna().any():
            normalized_values = result[column].map(filter_label)
            result = result[normalized_values.isin(selected)]
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

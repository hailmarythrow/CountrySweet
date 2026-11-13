"""Data-quality and filtering helpers for workbook-backed views."""
from __future__ import annotations

import math
import re

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
    # Variety and size controls share the normalized display vocabulary used
    # by X-Blocking.  The source values remain untouched; filtering below
    # compares their dimension keys so aliases such as ``10-12oz`` and
    # ``10-12 oz`` continue to select the same records.
    if column in {"variety", "size"}:
        return standardized_dimension_options((frame,), column)[0]
    return sort_filter_options(frame, column)


def _dimension_key(label: str, column: str) -> str:
    """Return a comparison key without changing the workbook value."""
    normalized = label.replace("–", "-").replace("—", "-")
    if column == "size":
        # Size labels occasionally differ only by whitespace (for example,
        # ``10-12 oz`` versus ``10-12oz``).  Keep the source values intact,
        # but expose one shared dashboard label for those aliases.
        normalized = re.sub(r"\s+", "", normalized)
    return normalized.replace("’", "'").casefold()


def _dimension_display_label(label: str, column: str) -> str:
    """Choose a stable display label for a normalized dimension value."""
    if column == "size":
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)\s*oz", label, flags=re.IGNORECASE)
        if range_match:
            return f"{range_match.group(1)}-{range_match.group(2)} oz"
    return label


def standardized_dimension_options(
    frames: tuple[pd.DataFrame | None, ...],
    column: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Build shared display options while retaining raw values for filtering.

    The returned mapping lets a view present one normalized option while still
    passing every corresponding source value to its existing filter logic.
    This is intentionally view-agnostic so the X-Blocking controls do not
    invent a second set of workbook classifications.
    """
    labels_by_key: dict[str, str] = {}
    raw_by_key: dict[str, list[str]] = {}
    for frame in frames:
        if frame is None or column not in frame:
            continue
        for value in frame[column].tolist():
            raw_label = filter_label(value)
            if not raw_label:
                continue
            key = _dimension_key(raw_label, column)
            display = _dimension_display_label(raw_label, column)
            existing = labels_by_key.get(key)
            # Prefer the readable range form while otherwise retaining the
            # first established dashboard label.
            if existing is None or (column == "size" and " " in display and " " not in existing):
                labels_by_key[key] = display
            raw_values = raw_by_key.setdefault(key, [])
            if raw_label not in raw_values:
                raw_values.append(raw_label)

    display_labels = list(labels_by_key.values())
    ordered = sort_filter_options(pd.DataFrame({column: display_labels}), column)
    display_to_raw: dict[str, list[str]] = {}
    for key, display in labels_by_key.items():
        raw_values = display_to_raw.setdefault(display, [])
        for raw_value in raw_by_key[key]:
            if raw_value not in raw_values:
                raw_values.append(raw_value)
    return ordered, display_to_raw


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
            if column in {"variety", "size"}:
                selected_keys = {
                    _dimension_key(filter_label(label) or "", column)
                    for label in selected
                    if filter_label(label)
                }
                normalized_values = normalized_values.map(
                    lambda value: _dimension_key(value, column) if value else None
                )
                result = result[normalized_values.isin(selected_keys)]
            else:
                result = result[normalized_values.isin(selected)]
    return result


def source_period(frame: pd.DataFrame) -> str:
    if "source_period" in frame and frame["source_period"].notna().any():
        labels = [str(value) for value in frame["source_period"].dropna().unique() if str(value).strip()]
        if labels:
            return " · ".join(labels[:3])
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

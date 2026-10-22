"""Fast, bounded Excel readers for the Country Sweet inventory workbook.

The source workbook contains many calculation and lookup sheets. The dashboard
reads the small set of verified source views it needs in read-only mode with
explicit ranges and leaves every other sheet deferred.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import logging
import re
from time import perf_counter
from typing import Any, Callable, Iterable
import warnings

import openpyxl
import pandas as pd
from openpyxl.utils import get_column_letter


LOGGER = logging.getLogger("country_sweet.excel")
REPORT_SCHEMA_VERSION = "sheet-report-source-views-v2"

CANONICAL_COLUMNS = [
    "product", "variety", "classification", "size", "packaging_type", "units",
    "stored_lbs", "forecast_demand_lbs", "planned_sales_lbs", "starting_inventory_lbs",
    "contribution_margin", "upcoming_demand_lbs", "harvest_lbs", "sales_lbs", "packout_pct",
    "purchased_loads", "remaining_loads", "year", "week", "reporting_date",
]

# These are deliberately bounded. They cover the real model layouts without
# trusting a worksheet's potentially inflated max_row/max_column metadata.
CURRENT_WEEK_BOUNDS = (1, 60, 1, 34)       # A1:AH60
FORECAST_BOUNDS = (1, 70, 1, 74)           # A1:BV70
HARVEST_BOUNDS = (1, 44, 1, 20)            # A1:T44
SALES_BOUNDS = (1, 61, 1, 67)              # A1:BO61
PACKOUT_BOUNDS = (1, 51, 1, 35)            # A1:AI51
PO_BOUNDS = (1, 44, 1, 7)                  # A1:G44
LOADS_BOUNDS = (1, 46, 1, 71)              # A1:BS46
MONTH_NAMES = {
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}
INITIAL_SHEETS = (
    "Current Week Model", "Forecasted Inventory Levels",
    "Forecast Harvest by Week HC", "Sales by Week", "Packout %",
    "POs", "Loads per Week",
)

ALIASES: dict[str, list[str]] = {
    "product": ["product", "product name", "item", "item name", "description", "sku", "product description"],
    "variety": ["variety", "type", "sweet potato variety", "commodity"],
    "classification": ["classification", "class", "organic conventional", "organic/conventional", "production type", "organic type"],
    "size": ["size", "pack size", "grade", "pack", "package size", "size grade"],
    "packaging_type": ["packaging type", "packaging", "package type", "package", "pack style", "pack description"],
    "units": ["units", "unit", "uom", "unit of measure", "measure"],
    "stored_lbs": ["stored lbs", "stored_lbs", "inventory lbs", "inventory pounds", "inventory", "on hand lbs", "on hand", "ending inventory", "forecasted inventory", "pounds in storage", "storage lbs"],
    "forecast_demand_lbs": ["forecast demand lbs", "forecast_demand_lbs", "forecast demand", "demand lbs", "sales forecast", "demand", "planned sales lbs"],
    "harvest_lbs": ["harvest lbs", "harvest_lbs", "harvest pounds", "harvest", "forecast harvest", "harvest plan"],
    "sales_lbs": ["sales lbs", "sales_lbs", "sales pounds", "sales", "shipments", "shipped lbs", "planned sales"],
    "packout_pct": ["packout pct", "packout_pct", "packout %", "packout percentage", "packout"],
    "purchased_loads": ["purchased loads", "purchased_loads", "loads purchased", "purchases", "purchased"],
    "remaining_loads": ["remaining loads", "remaining_loads", "loads remaining", "open loads", "remaining"],
    "year": ["year", "crop year", "crop_year", "reporting year"],
    "week": ["week", "week number", "week #", "wk", "reporting week", "week ending"],
    "reporting_date": ["reporting date", "report date", "date", "week ending date", "period", "as of date"],
}


@dataclass
class SheetReport:
    name: str
    status: str
    semantic: str | None = None
    header_row: int | None = None
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    missing_values: dict[str, int] = field(default_factory=dict)
    formula_cells: int = 0
    formula_errors: int = 0
    missing_formula_results: int = 0
    formula_error_cells: list[str] = field(default_factory=list)
    missing_formula_cells: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    frame: pd.DataFrame | None = None


@dataclass
class WorkbookReport:
    available_sheets: list[str]
    preferred_sheet: str
    frames: dict[str, pd.DataFrame]
    sheet_status: dict[str, str]
    warnings: list[str]
    sheet_reports: dict[str, SheetReport] = field(default_factory=dict)
    semantic_sheets: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    content_id: str = ""
    deferred_sheets: list[str] = field(default_factory=list)


def workbook_content_id(content: bytes) -> str:
    """Return a stable cache key for the uploaded workbook bytes."""
    return sha256(content).hexdigest()


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _canonical_empty() -> pd.DataFrame:
    return pd.DataFrame(columns=CANONICAL_COLUMNS)


def _quality_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {str(column): int(frame[column].isna().sum()) for column in frame.columns if frame[column].isna().any()}


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce imported fields while preserving missing values and errors."""
    result = frame.copy()
    for field_name in ["product", "variety", "classification", "size", "packaging_type", "units", "inventory_state"]:
        if field_name in result:
            result[field_name] = result[field_name].where(result[field_name].notna(), pd.NA).astype("string").str.strip()
    if "classification" in result:
        result["classification"] = result["classification"].str.title()
    for field_name in [
        "stored_lbs", "forecast_demand_lbs", "planned_sales_lbs", "starting_inventory_lbs",
        "contribution_margin", "upcoming_demand_lbs", "harvest_lbs", "sales_lbs", "packout_pct",
        "purchased_loads", "remaining_loads", "year", "week",
    ]:
        if field_name in result:
            result[field_name] = pd.to_numeric(result[field_name], errors="coerce")
    if "reporting_date" in result:
        result["reporting_date"] = pd.to_datetime(result["reporting_date"], errors="coerce")
    return result


def _header_map(columns: Iterable[object]) -> dict[str, object]:
    normalized = {_norm(c): c for c in columns if c is not None and str(c).strip()}
    result: dict[str, object] = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if _norm(alias) in normalized:
                result[target] = normalized[_norm(alias)]
                break
    return result


def _rows(ws: Any, bounds: tuple[int, int, int, int]) -> list[tuple[Any, ...]]:
    min_row, max_row, min_col, max_col = bounds
    return list(ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col, values_only=True))


def _formula_quality_bounded(formula_ws: Any, cached_ws: Any, bounds: tuple[int, int, int, int]) -> tuple[int, int, int, list[str], list[str]]:
    """Inspect formulas only inside a target sheet's bounded model range."""
    formula_cells = formula_errors = missing_results = 0
    error_cells: list[str] = []
    missing_cells: list[str] = []
    min_row, max_row, min_col, max_col = bounds
    formula_rows = formula_ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col, values_only=False)
    cached_rows = cached_ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col, values_only=True)
    for formula_row, cached_row in zip(formula_rows, cached_rows):
        for formula_cell, cached in zip(formula_row, cached_row):
            formula = formula_cell.value
            if not (isinstance(formula, str) and formula.startswith("=")):
                continue
            formula_cells += 1
            if isinstance(cached, str) and cached.startswith("#"):
                formula_errors += 1
                error_cells.append(formula_cell.coordinate)
            elif cached is None:
                missing_results += 1
                missing_cells.append(formula_cell.coordinate)
    return formula_cells, formula_errors, missing_results, error_cells, missing_cells


_SIZE_PATTERNS = ("10-12 oz", "10-12oz", "#1 Jewel", "Medium", "Jumbo", "14oz", "Baby", "Small", "#1")


def _split_product_label(label: str, classification: str | None) -> tuple[str, str | None, str | None]:
    clean = str(label).strip()
    prefix = "Conv. " if clean.startswith("Conv. ") else "Org. " if clean.startswith("Org. ") else ""
    inferred_class = classification or ("Conventional" if prefix == "Conv. " else "Organic" if prefix == "Org. " else None)
    base = clean[len(prefix):].strip() if prefix else clean
    for suffix in _SIZE_PATTERNS:
        if base.lower().endswith(suffix.lower()):
            variety = base[: -len(suffix)].strip()
            return clean, variety or None, suffix
    return clean, base or None, None


def _parse_forecasted_inventory(rows: list[tuple[Any, ...]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse the forecast matrix from F:BV without touching other sheets."""
    if len(rows) < 9:
        return _canonical_empty(), {"actual_date": None, "columns": 0}
    state_row, week_row, label_row, date_row = rows[2], rows[3], rows[4], rows[5]
    timeline_columns = [index for index, value in enumerate(date_row) if value is not None and hasattr(value, "year") and index >= 7]
    actual_dates = [date_row[index] for index in timeline_columns if str(state_row[index]).upper() == "ACTUAL"]
    actual_date = max(actual_dates) if actual_dates else None
    output: list[dict[str, Any]] = []
    classification: str | None = None
    for row_index, row in enumerate(rows[6:51], start=6):
        first = row[5] if len(row) > 5 else None
        description = row[6] if len(row) > 6 else None
        if isinstance(description, str) and description.strip() in {"Conventional", "Organic"}:
            classification = description.strip()
            continue
        if row_index < 8:
            continue
        if first in (None, "Lookups", "Total") and not (isinstance(description, str) and description.startswith(("CSP", "Sweet Potatoes"))):
            continue
        product_label = str(first or description or "").strip()
        if not product_label or product_label in {"Bulk Variety/Size", "Total"}:
            continue
        product, variety, size = _split_product_label(product_label, classification)
        for index in timeline_columns:
            value = row[index] if index < len(row) else None
            if value is None:
                continue
            report_date = date_row[index]
            output.append({
                "product": product, "variety": variety, "classification": classification, "size": size,
                "packaging_type": "Bulk Bin", "units": "lbs", "stored_lbs": value,
                "forecast_demand_lbs": pd.NA, "planned_sales_lbs": pd.NA, "starting_inventory_lbs": pd.NA,
                "contribution_margin": pd.NA, "upcoming_demand_lbs": pd.NA,
                "harvest_lbs": pd.NA, "sales_lbs": pd.NA, "packout_pct": pd.NA,
                "purchased_loads": pd.NA, "remaining_loads": pd.NA,
                "year": report_date.year if hasattr(report_date, "year") else pd.NA,
                "week": week_row[index], "reporting_date": report_date,
                "inventory_state": str(state_row[index]).title() if state_row[index] is not None else pd.NA,
            })
    return normalize_frame(pd.DataFrame(output)), {"actual_date": actual_date, "columns": len(timeline_columns)}


def _parse_current_week(rows: list[tuple[Any, ...]], year_hint: int | None, reporting_date: Any) -> pd.DataFrame:
    """Parse the current-week snapshot from the fixed model table D:AH."""
    if len(rows) < 13:
        return _canonical_empty()
    week_value = rows[5][6] if len(rows[5]) > 6 else pd.NA
    output: list[dict[str, Any]] = []
    for row in rows[12:55]:
        product_source = row[4] if len(row) > 4 else None
        fallback_product = row[3] if len(row) > 3 else None
        product_label = str(product_source or fallback_product or "").strip()
        if not product_label or product_label in {"Total", "Lookups"}:
            continue
        variety_source = row[5] if len(row) > 5 else None
        size = row[6] if len(row) > 6 else None
        classification = "Conventional" if product_label.startswith("Conv. ") else "Organic" if product_label.startswith("Org. ") else None
        _, inferred_variety, inferred_size = _split_product_label(product_label, classification)
        variety = str(variety_source).strip() if variety_source is not None else inferred_variety
        if variety.startswith("Conv. ") or variety.startswith("Org. "):
            variety = variety.split(". ", 1)[1]
        upcoming_values = [value for value in row[30:34] if value is not None]
        upcoming_demand = sum(upcoming_values) if upcoming_values else pd.NA
        output.append({
            "product": product_label, "variety": variety, "classification": classification,
            "size": str(size).strip() if size is not None else inferred_size,
            "packaging_type": "Bulk Bin", "units": "lbs",
            "stored_lbs": row[28] if len(row) > 28 else pd.NA,
            "forecast_demand_lbs": row[7] if len(row) > 7 else pd.NA,
            "planned_sales_lbs": row[7] if len(row) > 7 else pd.NA,
            "starting_inventory_lbs": row[23] if len(row) > 23 else pd.NA,
            "contribution_margin": row[19] if len(row) > 19 else pd.NA,
            "upcoming_demand_lbs": upcoming_demand,
            "harvest_lbs": pd.NA, "sales_lbs": pd.NA, "packout_pct": pd.NA,
            "purchased_loads": pd.NA, "remaining_loads": pd.NA,
            "year": year_hint if year_hint is not None else pd.NA, "week": week_value,
            "reporting_date": reporting_date, "inventory_state": "Current",
        })
    return normalize_frame(pd.DataFrame(output))


def _label_metadata(label: object, variety: object = None, size: object = None) -> tuple[str, str | None, str | None, str | None]:
    """Normalize the workbook's compact product labels without changing source values elsewhere."""
    clean = str(label or "").strip()
    if not clean:
        return "", None, None, None
    # Source matrices sometimes carry the compact ``Conv.``/``Org.`` marker
    # after a SKU and description prefix rather than at the start of the cell.
    classification = "Conventional" if re.search(r"(?:^|\s)Conv\.", clean) else "Organic" if re.search(r"(?:^|\s)Org\.", clean) else None
    product = clean
    inferred_variety = str(variety).strip() if variety is not None and str(variety).strip() else None
    inferred_size = str(size).strip() if size is not None and str(size).strip() else None
    if inferred_variety:
        inferred_variety = re.sub(r"^(?:Conv\.|Org\.)\s*", "", inferred_variety).strip()
        if inferred_variety.casefold().startswith("conv,"):
            inferred_variety = inferred_variety.split(",", 1)[1].strip()
    if inferred_size is None or inferred_variety is None:
        descriptive = re.sub(r"^.*?Sweet Potatoes\s+", "", clean).strip()
        descriptive = re.sub(r"\bBulk Bin\b", "", descriptive, flags=re.IGNORECASE).strip()
        if descriptive.startswith("Conv."):
            descriptive = descriptive[6:].strip()
        elif descriptive.startswith("Org."):
            descriptive = descriptive[5:].strip()
        _, inferred_variety_from_label, inferred_size_from_label = _split_product_label(
            ("Conv. " if classification == "Conventional" else "Org. " if classification == "Organic" else "") + descriptive,
            classification,
        )
        inferred_variety = inferred_variety or inferred_variety_from_label
        inferred_size = inferred_size or inferred_size_from_label
    if inferred_variety:
        inferred_variety = re.sub(r"\s+Bulk Bin$", "", inferred_variety, flags=re.IGNORECASE).strip()
    return product, inferred_variety, inferred_size, classification


def _numeric(value: object) -> float | int | pd.NA:
    if isinstance(value, str) and value.strip().startswith("#"):
        return pd.NA
    converted = pd.to_numeric(value, errors="coerce")
    return converted if pd.notna(converted) else pd.NA


def _parse_forecast_harvest(rows: list[tuple[Any, ...]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse Forecast Harvest by Week HC using its fixed B:T matrix."""
    if len(rows) < 3:
        return _canonical_empty(), {"packout_factor": None, "weeks": []}
    week_row = rows[0]
    week_labels = {index: _numeric(value) for index, value in enumerate(week_row) if index >= 2 and pd.notna(_numeric(value))}
    packout_factor = _numeric(rows[1][1] if len(rows[1]) > 1 else None)
    output: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[2:], start=3):
        raw_label = row[1] if len(row) > 1 else None
        if not raw_label or str(raw_label).strip() in {"Total", "PACKOUT"}:
            continue
        product, variety, size, classification = _label_metadata(raw_label)
        if not product:
            continue
        for column_index, week in week_labels.items():
            value = _numeric(row[column_index] if column_index < len(row) else None)
            if pd.isna(value):
                continue
            output.append({
                "product": product, "variety": variety, "classification": classification, "size": size,
                "packaging_type": "Bulk Bin", "units": "lbs", "harvest_lbs": value,
                "year": pd.NA, "week": week, "period_label": f"Week {int(week)}",
                "source_period": "Weeks 34–51 (crop year not supplied)",
                "reporting_date": pd.NaT,
            })
    return normalize_frame(pd.DataFrame(output)), {"packout_factor": packout_factor, "weeks": sorted({int(v) for v in week_labels.values()})}


def _parse_sales_by_week(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    """Parse planned sales pounds from the J:BO weekly matrix."""
    if len(rows) < 5:
        return _canonical_empty()
    years, weeks = rows[0], rows[3]
    output: list[dict[str, Any]] = []
    for row in rows[4:]:
        raw_product = row[5] if len(row) > 5 else None
        if not raw_product or str(raw_product).strip() in {"Total", "Lookups"}:
            continue
        product, variety, size, classification = _label_metadata(raw_product, row[7] if len(row) > 7 else None, row[8] if len(row) > 8 else None)
        for column_index in range(9, min(len(row), len(years))):
            year = _numeric(years[column_index])
            week = _numeric(weeks[column_index] if column_index < len(weeks) else None)
            value = _numeric(row[column_index])
            if pd.isna(value) or pd.isna(week):
                continue
            output.append({
                "product": product, "variety": variety, "classification": classification, "size": size,
                "packaging_type": "Bulk Bin", "units": "lbs", "planned_sales_lbs": value,
                "year": year, "week": week, "period_label": f"{int(year)} W{int(week):02d}",
                "source_period": "Crop years 2026–2027; weeks 1–53",
                "reporting_date": pd.NaT,
            })
    return normalize_frame(pd.DataFrame(output))


def _parse_packout(rows: list[tuple[Any, ...]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse monthly packout percentages from the Conventional and Organic blocks."""
    if len(rows) < 5:
        return _canonical_empty(), {"invalid_cells": []}
    months = rows[3]
    year_headers = rows[2]
    output: list[dict[str, Any]] = []
    invalid_cells: list[str] = []
    classification = None
    for row_number, row in enumerate(rows[4:], start=5):
        section = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
        if section in {"Conventional", "Organic"}:
            classification = section
            continue
        raw_product = row[1] if len(row) > 1 else None
        if not raw_product:
            continue
        product, variety, size, inferred_classification = _label_metadata(raw_product)
        row_classification = inferred_classification or classification
        current_year = None
        for column_index in range(3, min(len(row), len(months))):
            if column_index < len(year_headers) and year_headers[column_index] is not None:
                current_year = _numeric(year_headers[column_index])
            month = months[column_index]
            # The sheet continues into a separate weekly projection block
            # whose headers are strings such as ``Week``. This view is
            # intentionally monthly, so do not misclassify those cells as
            # percentages.
            month_name = str(month).strip() if month is not None else ""
            if month_name not in MONTH_NAMES:
                continue
            value = _numeric(row[column_index])
            if not month or pd.isna(value):
                continue
            if float(value) < 0 or float(value) > 1:
                invalid_cells.append(f"{get_column_letter(column_index + 1)}{row_number}")
                continue
            year = current_year if current_year is not None else pd.NA
            period_label = f"{month_name} {int(year)}" if pd.notna(year) else f"{month_name} (year not supplied)"
            output.append({
                "product": product, "variety": variety, "classification": row_classification, "size": size,
                "packaging_type": "Bulk Bin", "units": "%", "packout_pct": value,
                "year": year, "month": month_name, "period_label": period_label,
                "source_cell": f"{get_column_letter(column_index + 1)}{row_number}",
                "source_period": "Monthly packout inputs; August year not supplied, September 2026 onward labeled",
                "reporting_date": pd.to_datetime(f"{int(year)}-{month}-01", errors="coerce") if pd.notna(year) else pd.NaT,
            })
    return normalize_frame(pd.DataFrame(output)), {"invalid_cells": invalid_cells}


def _parse_pos(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    """Parse POs while coercing spreadsheet errors to missing values."""
    output: list[dict[str, Any]] = []
    update_text = str(rows[0][3]).strip() if rows and len(rows[0]) > 3 and rows[0][3] is not None else "Unknown"
    source_period = f"POs updated {update_text} (date format is ambiguous; verify recency)"
    for excel_row, row in enumerate(rows[2:43], start=3):
        product = row[1] if len(row) > 1 else None
        if not product:
            continue
        product, variety, size, classification = _label_metadata(product, row[2] if len(row) > 2 else None, row[3] if len(row) > 3 else None)
        output.append({
            "product": product, "variety": variety, "classification": classification, "size": size,
            "packaging_type": "Bulk Bin", "units": "loads",
            "total_loads_needed": _numeric(row[4] if len(row) > 4 else None),
            "purchased_loads": _numeric(row[5] if len(row) > 5 else None),
            "remaining_loads": _numeric(row[6] if len(row) > 6 else None),
            "source_cells": f"E{excel_row}, F{excel_row}, G{excel_row}",
            "source_period": source_period, "year": pd.NA, "week": pd.NA, "reporting_date": pd.NaT,
        })
    return normalize_frame(pd.DataFrame(output))


def _parse_loads_per_week(rows: list[tuple[Any, ...]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse weekly load quantities from the H:BS matrix."""
    if len(rows) < 3:
        return _canonical_empty()
    weeks = rows[0]
    output: list[dict[str, Any]] = []
    observed_weeks: set[int] = set()
    duplicate_weeks: set[int] = set()
    week_columns: list[tuple[int, int]] = []
    seen_weeks: set[int] = set()
    for column_index in range(7, len(weeks)):
        week = _numeric(weeks[column_index])
        if pd.isna(week):
            continue
        week_number = int(week)
        if week_number in seen_weeks:
            duplicate_weeks.add(week_number)
            continue
        seen_weeks.add(week_number)
        week_columns.append((column_index, week_number))
    for row in rows[2:]:
        raw_product = (row[4] if len(row) > 4 and row[4] else row[3] if len(row) > 3 else None)
        if not raw_product:
            continue
        product, variety, size, classification = _label_metadata(raw_product, row[5] if len(row) > 5 else None, row[6] if len(row) > 6 else None)
        for column_index, week_number in week_columns:
            value = _numeric(row[column_index])
            if pd.isna(value):
                continue
            observed_weeks.add(week_number)
            output.append({
                "product": product, "variety": variety, "classification": classification, "size": size,
                "packaging_type": "Bulk Bin", "units": "loads", "loads_per_week": value,
                "year": pd.NA, "week": week_number, "period_label": f"Week {week_number}",
                "source_period": "", "reporting_date": pd.NaT,
            })
    if observed_weeks:
        source_period = f"Weeks {min(observed_weeks)}–{max(observed_weeks)} (crop year not supplied)"
        for record in output:
            record["source_period"] = source_period
    return normalize_frame(pd.DataFrame(output)), {"duplicate_weeks": sorted(duplicate_weeks), "weeks": sorted(observed_weeks)}


def _report_for_frame(name: str, status: str, semantic: str, frame: pd.DataFrame, elapsed_ms: float, formula_quality: tuple[int, int, int, list[str], list[str]]) -> SheetReport:
    formula_cells, formula_errors, missing_results, error_cells, missing_cells = formula_quality
    return SheetReport(
        name=name, status=status, semantic=semantic, row_count=len(frame),
        columns=[str(column) for column in frame.columns], missing_values=_quality_counts(frame),
        formula_cells=formula_cells, formula_errors=formula_errors,
        missing_formula_results=missing_results, formula_error_cells=error_cells,
        missing_formula_cells=missing_cells, elapsed_ms=elapsed_ms, frame=frame,
    )


def load_inventory_workbook(content: bytes, progress_callback: Callable[[str], None] | None = None) -> WorkbookReport:
    """Recognize the workbook and parse only the bounded dashboard source views."""
    total_started = perf_counter()
    content_id = workbook_content_id(content)
    timings: dict[str, float] = {}

    def stage(message: str) -> None:
        LOGGER.info("excel.stage %s", message)
        if progress_callback:
            progress_callback(message)

    stage("Opening workbook in read-only mode")
    open_started = perf_counter()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Slicer List extension is not supported and will be removed")
        values_wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    timings["workbook_open_ms"] = (perf_counter() - open_started) * 1000
    available = list(values_wb.sheetnames)
    timings["sheet_recognition_ms"] = (perf_counter() - open_started) * 1000
    LOGGER.info("excel.sheet_recognition sheets=%d elapsed_ms=%.1f", len(available), timings["sheet_recognition_ms"])

    # Formula inspection is also bounded to the two target ranges.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Slicer List extension is not supported and will be removed")
        formula_wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=False)
    frames: dict[str, pd.DataFrame] = {}
    reports: dict[str, SheetReport] = {}
    statuses: dict[str, str] = {}
    semantic_sheets: dict[str, str] = {}
    quality_warnings: list[str] = []
    deferred = [sheet for sheet in available if sheet not in INITIAL_SHEETS]
    for sheet in deferred:
        reports[sheet] = SheetReport(sheet, "Deferred until this worksheet is explicitly requested.", semantic=None)
        statuses[sheet] = reports[sheet].status

    forecast_meta: dict[str, Any] = {"actual_date": None, "columns": 0}
    parse_started = perf_counter()
    # Forecast first so Current Week Model can inherit its verified year/date.
    for sheet in ("Forecasted Inventory Levels", "Current Week Model"):
        if sheet not in available:
            continue
        stage(f"Parsing {sheet} using a bounded model range")
        sheet_started = perf_counter()
        bounds = FORECAST_BOUNDS if sheet == "Forecasted Inventory Levels" else CURRENT_WEEK_BOUNDS
        values_rows = _rows(values_wb[sheet], bounds)
        formula_quality = _formula_quality_bounded(formula_wb[sheet], values_wb[sheet], bounds)
        if sheet == "Forecasted Inventory Levels":
            frame, forecast_meta = _parse_forecasted_inventory(values_rows)
            semantic = "inventory_timeline"
        else:
            year_hint = int(forecast_meta["actual_date"].year) if forecast_meta.get("actual_date") else None
            frame = _parse_current_week(values_rows, year_hint, forecast_meta.get("actual_date"))
            semantic = "inventory"
        elapsed_ms = (perf_counter() - sheet_started) * 1000
        frames[sheet] = frame
        status = f"Imported bounded range ({len(frame):,} rows) in {elapsed_ms:.1f} ms."
        reports[sheet] = _report_for_frame(sheet, status, semantic, frame, elapsed_ms, formula_quality)
        statuses[sheet] = status
        semantic_sheets[semantic] = sheet
        if formula_quality[1]:
            cells = ", ".join(formula_quality[3])
            quality_warnings.append(f"{sheet}: {formula_quality[1]} spreadsheet formula error(s) were found at {cells}; values remain flagged.")
        if formula_quality[2]:
            cells = ", ".join(formula_quality[4])
            quality_warnings.append(f"{sheet}: {formula_quality[2]} formula result(s) have no cached value at {cells}; they were not treated as zero.")
        LOGGER.info("excel.sheet_parse sheet=%s rows=%d elapsed_ms=%.1f", sheet, len(frame), elapsed_ms)

    # These worksheet-specific readers preserve the workbook's matrix layouts
    # and are intentionally bounded to the small used ranges of each model.
    extra_specs: list[tuple[str, tuple[int, int, int, int], str, Callable[[list[tuple[Any, ...]]], Any]]] = [
        ("Forecast Harvest by Week HC", HARVEST_BOUNDS, "harvest_source", _parse_forecast_harvest),
        ("Sales by Week", SALES_BOUNDS, "sales_source", _parse_sales_by_week),
        ("Packout %", PACKOUT_BOUNDS, "packout_source", _parse_packout),
        ("POs", PO_BOUNDS, "po_source", _parse_pos),
        ("Loads per Week", LOADS_BOUNDS, "loads_source", _parse_loads_per_week),
    ]
    extra_meta: dict[str, Any] = {}
    for sheet, bounds, semantic, parser in extra_specs:
        if sheet not in available:
            continue
        stage(f"Parsing {sheet} using a bounded worksheet parser")
        sheet_started = perf_counter()
        values_rows = _rows(values_wb[sheet], bounds)
        formula_quality = _formula_quality_bounded(formula_wb[sheet], values_wb[sheet], bounds)
        parsed = parser(values_rows)
        if isinstance(parsed, tuple):
            frame, metadata = parsed
            extra_meta[sheet] = metadata
        else:
            frame = parsed
        elapsed_ms = (perf_counter() - sheet_started) * 1000
        frames[sheet] = frame
        status = f"Imported bounded range ({len(frame):,} rows) in {elapsed_ms:.1f} ms."
        reports[sheet] = _report_for_frame(sheet, status, semantic, frame, elapsed_ms, formula_quality)
        statuses[sheet] = status
        semantic_sheets[semantic] = sheet
        if formula_quality[1]:
            cells = ", ".join(formula_quality[3])
            quality_warnings.append(f"{sheet}: {formula_quality[1]} spreadsheet formula error(s) were found at {cells}; invalid values were excluded from aggregations.")
        if formula_quality[2]:
            cells = ", ".join(formula_quality[4])
            quality_warnings.append(f"{sheet}: {formula_quality[2]} formula result(s) have no cached value at {cells}; they were not treated as zero.")
        LOGGER.info("excel.sheet_parse sheet=%s rows=%d elapsed_ms=%.1f", sheet, len(frame), elapsed_ms)

    harvest_parts = [frames[sheet] for sheet in ("Forecast Harvest by Week HC", "Sales by Week", "Packout %") if sheet in frames and not frames[sheet].empty]
    if harvest_parts:
        harvest_frame = pd.concat(harvest_parts, ignore_index=True, sort=False)
        harvest_periods: list[str] = []
        harvest_source = frames.get("Forecast Harvest by Week HC")
        if harvest_source is not None and "week" in harvest_source and harvest_source["week"].notna().any():
            harvest_periods.append(
                f"Forecast Harvest by Week HC weeks {int(harvest_source['week'].min())}–{int(harvest_source['week'].max())} (crop year not supplied)"
            )
        sales_source = frames.get("Sales by Week")
        if sales_source is not None and "year" in sales_source and sales_source["year"].notna().any():
            years = sorted(int(value) for value in sales_source["year"].dropna().unique())
            harvest_periods.append(f"Sales by Week crop years {years[0]}–{years[-1]}")
        harvest_periods.append("Packout % monthly inputs")
        harvest_frame["source_period"] = "; ".join(harvest_periods)
        frames["Forecast Harvest by Week HC + Sales by Week + Packout %"] = harvest_frame
        semantic_sheets["harvest_sales"] = "Forecast Harvest by Week HC + Sales by Week + Packout %"

    purchasing_parts = [frames[sheet] for sheet in ("POs", "Loads per Week") if sheet in frames and not frames[sheet].empty]
    if purchasing_parts:
        purchasing_frame = pd.concat(purchasing_parts, ignore_index=True, sort=False)
        po_source = frames.get("POs")
        loads_source = frames.get("Loads per Week")
        po_period = str(po_source["source_period"].dropna().iloc[0]) if po_source is not None and "source_period" in po_source and po_source["source_period"].notna().any() else "POs source date not supplied"
        loads_period = str(loads_source["source_period"].dropna().iloc[0]) if loads_source is not None and "source_period" in loads_source and loads_source["source_period"].notna().any() else "Loads per Week source period not supplied"
        purchasing_frame["source_period"] = f"{po_period}; {loads_period}"
        frames["POs + Loads per Week"] = purchasing_frame
        semantic_sheets["purchasing_loads"] = "POs + Loads per Week"

    harvest_metadata = extra_meta.get("Forecast Harvest by Week HC", {})
    if harvest_metadata.get("packout_factor") is not None:
        quality_warnings.append(
            f"Forecast Harvest by Week HC includes a PACKOUT factor of {float(harvest_metadata['packout_factor']):.0%}; harvest values were imported as supplied and no additional packout adjustment was applied."
        )
    if "Loads per Week" in frames:
        quality_warnings.append("Loads per Week supplies week numbers but no crop year; weekly load quantities are shown by week only.")
        duplicate_weeks = extra_meta.get("Loads per Week", {}).get("duplicate_weeks", [])
        if duplicate_weeks:
            quality_warnings.append(
                f"Loads per Week repeats unlabeled week headers for weeks {', '.join(str(week) for week in duplicate_weeks)}; duplicate columns were excluded from the verified weekly series."
            )
    invalid_packout_cells = extra_meta.get("Packout %", {}).get("invalid_cells", [])
    if invalid_packout_cells:
        quality_warnings.append(
            f"Packout % excluded {len(invalid_packout_cells)} value(s) outside the valid 0–100% range at {', '.join(invalid_packout_cells)}."
        )
    if "POs" in frames:
        quality_warnings.append("POs reports update text '12.8.25', which is ambiguous and may be outdated; verify the source date before operational use.")
    timings["target_sheet_parsing_ms"] = (perf_counter() - parse_started) * 1000

    validation_started = perf_counter()
    if "inventory" not in semantic_sheets:
        quality_warnings.append("Current Week Model was not available, so current inventory cards remain unavailable.")
    if "inventory_timeline" not in semantic_sheets:
        quality_warnings.append("Forecasted Inventory Levels was not available, so the inventory timeline remains unavailable.")
    if "harvest_sales" not in semantic_sheets:
        quality_warnings.append("Forecast Harvest by Week HC, Sales by Week, and Packout % were not available, so Harvest & Sales remains unavailable.")
    if "purchasing_loads" not in semantic_sheets:
        quality_warnings.append("POs and Loads per Week were not available, so Purchasing & Loads remains unavailable.")
    timings["validation_ms"] = (perf_counter() - validation_started) * 1000
    timings["total_ms"] = (perf_counter() - total_started) * 1000
    LOGGER.info("excel.validation elapsed_ms=%.1f", timings["validation_ms"])
    LOGGER.info("excel.total content_id=%s elapsed_ms=%.1f deferred=%d", content_id[:12], timings["total_ms"], len(deferred))
    values_wb.close()
    formula_wb.close()

    return WorkbookReport(
        available_sheets=available,
        preferred_sheet=semantic_sheets.get("inventory", ""),
        frames=frames,
        sheet_status=statuses,
        warnings=quality_warnings,
        sheet_reports=reports,
        semantic_sheets=semantic_sheets,
        timings=timings,
        content_id=content_id,
        deferred_sheets=deferred,
    )


def frame_for(report: WorkbookReport, semantic: str) -> tuple[str | None, pd.DataFrame | None]:
    sheet = report.semantic_sheets.get(semantic)
    if not sheet or sheet not in report.frames:
        return None, None
    return sheet, report.frames[sheet]


def report_to_frame(report: WorkbookReport) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in report.sheet_reports.values():
        formula_cells = getattr(item, "formula_cells", 0)
        formula_errors = getattr(item, "formula_errors", 0)
        missing_formula_results = getattr(item, "missing_formula_results", 0)
        error_cells = getattr(item, "formula_error_cells", None)
        missing_cells = getattr(item, "missing_formula_cells", None)
        # Reports cached by an older parser may not have the newer cell lists.
        # Keep the counts, but say that locations were not captured rather than
        # fabricating an empty result.
        error_cell_label = ", ".join(error_cells) if error_cells else ("Not captured by cached parser" if formula_errors else "None")
        missing_cell_label = ", ".join(missing_cells) if missing_cells else ("Not captured by cached parser" if missing_formula_results else "None")
        rows.append({
            "Worksheet": getattr(item, "name", "Unknown worksheet"),
            "Status": getattr(item, "status", "Unknown status"),
            "Use": getattr(item, "semantic", None) or "Deferred",
            "Rows": getattr(item, "row_count", 0), "Elapsed ms": round(getattr(item, "elapsed_ms", 0.0), 1),
            "Formula cells": formula_cells, "Formula errors": formula_errors,
            "Missing cached results": missing_formula_results,
            "Formula error cells": error_cell_label,
            "Missing cached cells": missing_cell_label,
            "Missing values": ", ".join(f"{field} ({count})" for field, count in getattr(item, "missing_values", {}).items()) or "None",
        })
    return pd.DataFrame(rows)


def timings_to_frame(report: WorkbookReport) -> pd.DataFrame:
    return pd.DataFrame([{"Stage": key.replace("_", " ").title(), "Elapsed ms": round(value, 1)} for key, value in report.timings.items()])

"""Excel inspection, import, and source-data quality reporting."""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import re
from typing import Any

import openpyxl
import pandas as pd


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

SEMANTIC_SHEET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "inventory": ("forecasted inventory", "inventory levels", "inventory", "stock"),
    "harvest_sales": ("harvest", "sales", "packout"),
    "purchasing_loads": ("purchasing", "purchase", "loads", "load"),
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


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _header_map(columns: list[object]) -> dict[str, object]:
    normalized = {_norm(c): c for c in columns if c is not None and str(c).strip()}
    result: dict[str, object] = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if _norm(alias) in normalized:
                result[target] = normalized[_norm(alias)]
                break
    return result


def _formula_quality(workbook: openpyxl.Workbook, cached_workbook: openpyxl.Workbook, sheet: str) -> tuple[int, int, int]:
    formula_cells = formula_errors = missing_results = 0
    formula_sheet = workbook[sheet]
    cached_sheet = cached_workbook[sheet]
    for row in formula_sheet.iter_rows():
        for cell in row:
            if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                continue
            formula_cells += 1
            cached_value = cached_sheet[cell.coordinate].value
            if isinstance(cached_value, str) and cached_value.startswith("#"):
                formula_errors += 1
            elif cached_value is None:
                missing_results += 1
    return formula_cells, formula_errors, missing_results


def _classify_sheet(name: str, mapping: dict[str, object]) -> str | None:
    normalized_name = _norm(name)
    if "stored_lbs" in mapping or "forecast_demand_lbs" in mapping:
        return "inventory"
    if any(keyword in normalized_name for keyword in SEMANTIC_SHEET_KEYWORDS["purchasing_loads"]):
        return "purchasing_loads"
    if any(keyword in normalized_name for keyword in SEMANTIC_SHEET_KEYWORDS["harvest_sales"]):
        return "harvest_sales"
    return None


def _read_sheet(raw: pd.DataFrame, name: str) -> tuple[pd.DataFrame | None, str, int | None, dict[str, object]]:
    for header_row in range(min(20, len(raw))):
        candidate = raw.iloc[header_row].tolist()
        mapping = _header_map(candidate)
        if not ({"stored_lbs", "forecast_demand_lbs", "harvest_lbs", "sales_lbs", "packout_pct", "purchased_loads", "remaining_loads"} & set(mapping)):
            continue
        data = raw.iloc[header_row + 1 :].copy()
        data.columns = candidate
        data = data.rename(columns={source: target for target, source in mapping.items()})
        for target in ALIASES:
            if target not in data.columns:
                data[target] = pd.NA
        data = data[list(ALIASES)].dropna(how="all").reset_index(drop=True)
        return data, f"Imported using header row {header_row + 1}; mapped {len(mapping)} source fields.", header_row + 1, mapping
    return None, f"Unsupported sheet '{name}': no recognizable supported measures were found.", None, {}


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce imported fields while preserving missing values and formula errors."""
    result = frame.copy()
    for field_name in ["product", "variety", "classification", "size", "packaging_type", "units"]:
        if field_name in result:
            result[field_name] = result[field_name].where(result[field_name].notna(), pd.NA).astype("string").str.strip()
    if "classification" in result:
        result["classification"] = result["classification"].str.title()
    for field_name in ["stored_lbs", "forecast_demand_lbs", "harvest_lbs", "sales_lbs", "packout_pct", "purchased_loads", "remaining_loads", "year", "week"]:
        if field_name in result:
            result[field_name] = pd.to_numeric(result[field_name], errors="coerce")
    if "reporting_date" in result:
        result["reporting_date"] = pd.to_datetime(result["reporting_date"], errors="coerce")
    return result


def _quality_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {str(column): int(frame[column].isna().sum()) for column in frame.columns if frame[column].isna().any()}


def load_inventory_workbook(content: bytes) -> WorkbookReport:
    """Inspect every worksheet and import only sheets with recognized source measures."""
    excel = pd.ExcelFile(BytesIO(content), engine="openpyxl")
    available = list(excel.sheet_names)
    preferred = "Forecasted Inventory Levels" if "Forecasted Inventory Levels" in available else (available[0] if available else "")
    formula_workbook = openpyxl.load_workbook(BytesIO(content), data_only=False, read_only=False)
    cached_workbook = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=False)
    frames: dict[str, pd.DataFrame] = {}
    statuses: dict[str, str] = {}
    reports: dict[str, SheetReport] = {}
    semantic_sheets: dict[str, str] = {}
    warnings: list[str] = []

    for sheet in available:
        raw = pd.read_excel(excel, sheet_name=sheet, header=None)
        formula_cells, formula_errors, missing_results = _formula_quality(formula_workbook, cached_workbook, sheet)
        frame, status, header_row, mapping = _read_sheet(raw, sheet)
        semantic = _classify_sheet(sheet, mapping)
        report = SheetReport(name=sheet, status=status, semantic=semantic, header_row=header_row, formula_cells=formula_cells, formula_errors=formula_errors, missing_formula_results=missing_results)
        if formula_errors:
            warnings.append(f"{sheet}: {formula_errors} spreadsheet formula error(s) were found; values remain flagged.")
        if missing_results:
            warnings.append(f"{sheet}: {missing_results} formula result(s) have no cached value; they were not treated as zero.")
        statuses[sheet] = status
        if frame is not None:
            frame = normalize_frame(frame)
            frames[sheet] = frame
            report.frame = frame
            report.row_count = len(frame)
            report.columns = [str(column) for column in frame.columns]
            report.missing_values = _quality_counts(frame)
            if semantic and semantic not in semantic_sheets:
                semantic_sheets[semantic] = sheet
        reports[sheet] = report

    if preferred not in frames and semantic_sheets.get("inventory"):
        preferred = semantic_sheets["inventory"]
    if not frames:
        warnings.append("No worksheet matched a supported inventory, harvest, sales, packout, or purchasing measure. No values were invented.")
    elif "inventory" not in semantic_sheets:
        warnings.append("No worksheet with stored inventory or forecast demand was found. Inventory cards and timeline remain unavailable.")

    return WorkbookReport(available, preferred, frames, statuses, warnings, reports, semantic_sheets)


def frame_for(report: WorkbookReport, semantic: str) -> tuple[str | None, pd.DataFrame | None]:
    sheet = report.semantic_sheets.get(semantic)
    if not sheet or sheet not in report.frames:
        return None, None
    return sheet, report.frames[sheet]


def report_to_frame(report: WorkbookReport) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in report.sheet_reports.values():
        rows.append({
            "Worksheet": item.name,
            "Status": item.status,
            "Use": item.semantic or "Unsupported",
            "Rows": item.row_count,
            "Formula cells": item.formula_cells,
            "Formula errors": item.formula_errors,
            "Missing cached results": item.missing_formula_results,
            "Missing values": ", ".join(f"{field} ({count})" for field, count in item.missing_values.items()) or "None",
        })
    return pd.DataFrame(rows)

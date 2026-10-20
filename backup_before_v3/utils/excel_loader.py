"""Safe workbook inspection and normalization for the inventory dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re

import pandas as pd

ALIASES = {
    "product": ["product", "product name", "item", "item name", "description", "sku"],
    "variety": ["variety", "type", "sweet potato variety", "commodity"],
    "classification": ["classification", "class", "organic conventional", "organic/conventional", "production type"],
    "size": ["size", "pack size", "grade", "pack", "package size"],
    "stored_lbs": ["stored_lbs", "stored lbs", "inventory lbs", "inventory", "on hand lbs", "on hand", "ending inventory", "forecasted inventory"],
    "forecast_demand_lbs": ["forecast_demand_lbs", "forecast demand lbs", "forecast demand", "demand lbs", "sales forecast", "demand"],
    "year": ["year", "crop year", "crop_year"],
    "week": ["week", "week number", "week #", "wk"],
}


@dataclass
class WorkbookReport:
    available_sheets: list[str]
    preferred_sheet: str
    frames: dict[str, pd.DataFrame]
    sheet_status: dict[str, str]
    warnings: list[str]


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _header_map(columns: list[object]) -> dict[str, object]:
    normalized = {_norm(c): c for c in columns}
    result: dict[str, object] = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if _norm(alias) in normalized:
                result[target] = normalized[_norm(alias)]
                break
    return result


def _read_sheet(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, str]:
    for header_row in range(min(12, len(raw))):
        candidate = raw.iloc[header_row].tolist()
        mapping = _header_map(candidate)
        if not {"product", "stored_lbs"}.issubset(mapping):
            continue
        data = raw.iloc[header_row + 1 :].copy()
        data.columns = candidate
        data = data.rename(columns={source: target for target, source in mapping.items()})
        for target in ["variety", "classification", "size", "forecast_demand_lbs", "year", "week"]:
            if target not in data.columns:
                data[target] = pd.NA
        return data[["product", "variety", "classification", "size", "stored_lbs", "forecast_demand_lbs", "year", "week"]], f"Imported using header row {header_row + 1}; mapped product and inventory fields."
    return None, "No recognizable product and inventory columns found."


def load_inventory_workbook(content: bytes) -> WorkbookReport:
    excel = pd.ExcelFile(BytesIO(content), engine="openpyxl")
    available = list(excel.sheet_names)
    preferred = "Forecasted Inventory Levels" if "Forecasted Inventory Levels" in available else available[0]
    frames: dict[str, pd.DataFrame] = {}
    statuses: dict[str, str] = {}
    warnings: list[str] = []
    for sheet in available:
        raw = pd.read_excel(excel, sheet_name=sheet, header=None)
        frame, status = _read_sheet(raw)
        statuses[sheet] = status
        if frame is not None:
            frames[sheet] = frame
        if sheet == preferred and frame is None:
            warnings.append(f"The preferred worksheet '{sheet}' could not be imported. No inventory values were invented or replaced with zero.")
    if preferred not in frames and frames:
        preferred = next(iter(frames))
        warnings.append(f"Using '{preferred}' because the preferred inventory worksheet was unavailable.")
    if not frames:
        warnings.append("No worksheet matched the dashboard template. Download the sample template to see the expected columns.")
    return WorkbookReport(available, preferred, frames, statuses, warnings)

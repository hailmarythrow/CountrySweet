"""Workbook-backed comparison helpers for the X-Blocking Analysis page.

This module keeps inventory and demand comparisons source-backed. It exposes
qualified workbook reference prices when the Prices pivot can be reconciled,
but it never infers conversion eligibility or treats a scenario result as
actual opportunity cost.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .charts import inventory_periods


EXPECTED_SOURCES: dict[str, str] = {
    "Forecasted Inventory Levels": "Inventory balances and ACTUAL/FORECAST periods",
    "Sales by Week": "Planned sales / demand by reporting week",
    "Current Week Model": "Current inventory and workbook contribution-margin fields",
    "Current Month Model": "Potential monthly demand or margin support; not parsed by the dashboard",
    "Prices": "Workbook reference selling prices by product and classification",
    "PP Price Target": "Separate per-40-lb pricing and margin context; not used as a conversion price",
}


def pricing_assessment(report: Any) -> dict[str, Any]:
    """Assess workbook pricing availability without treating it as current-week pricing."""
    frames = getattr(report, "frames", {}) or {}
    prices = frames.get("Prices")
    if prices is not None and not prices.empty and "reference_price_lbs" in prices:
        valid = prices[
            prices["reference_price_lbs"].notna()
            & prices.get("price_consistent", pd.Series(False, index=prices.index)).astype(bool)
        ]
        if not valid.empty:
            return {
                "verified": True,
                "status": "Workbook reference prices available",
                "detail": "Prices contains plan-revenue-backed reference prices. The worksheet is filtered to Year = (All), so these are not asserted to be current Week 42 realized prices.",
                "source": "Prices",
            }
    current = frames.get("Current Week Model")
    if current is not None and "contribution_margin" in current:
        values = pd.to_numeric(current["contribution_margin"], errors="coerce").dropna()
        if not values.empty:
            return {
                "verified": False,
                "status": "Pricing data requires validation",
                "detail": "Current Week Model contains contribution-margin values, but the workbook does not identify compatible organic and conventional price/cost definitions for a conversion scenario.",
                "source": "Current Week Model",
            }
    return {
        "verified": False,
        "status": "Pricing data requires validation",
        "detail": "No verified comparable organic and conventional price-per-pound or contribution-margin pair is available in the parsed workbook sources.",
        "source": "Current Week Model, Current Month Model, PP Price Target, and Sales by Week",
    }


def reference_price_comparison(
    report: Any,
    varieties: list[str] | None = None,
    sizes: list[str] | None = None,
) -> dict[str, Any]:
    """Return a comparable organic/conventional reference-price pair.

    Prices are weighted by plan revenue divided by plan pounds when more than
    one source row matches. The source G-column value is accepted only when it
    agrees with F/E within a small tolerance. No current-week or realized-price
    claim is made because the workbook's Prices pivot is set to Year = (All).
    """
    varieties = varieties or []
    sizes = sizes or []
    if len(varieties) != 1 or len(sizes) != 1:
        return {
            "paired": False,
            "message": "Select one variety and one size to compare workbook reference prices.",
            "source": "Prices",
        }
    frames = getattr(report, "frames", {}) or {}
    prices = frames.get("Prices")
    if prices is None or prices.empty:
        return {
            "paired": False,
            "message": "Prices worksheet was not available in the uploaded workbook.",
            "source": "Prices",
        }
    required = {"variety", "size", "classification", "reference_price_lbs", "price_consistent"}
    if not required.issubset(prices.columns):
        return {
            "paired": False,
            "message": "Prices worksheet was present, but its reference-price fields could not be mapped reliably.",
            "source": "Prices",
        }
    selected_variety = str(varieties[0]).strip()
    selected_size = str(sizes[0]).strip()
    subset = prices[
        prices["variety"].astype("string").str.casefold().eq(selected_variety.casefold())
        & prices["size"].astype("string").str.casefold().eq(selected_size.casefold())
    ].copy()
    source_period = str(subset["source_period"].dropna().iloc[0]) if "source_period" in subset and subset["source_period"].notna().any() else "Prices · period not supplied"
    paired: dict[str, dict[str, Any]] = {}
    for classification in ("Organic", "Conventional"):
        rows = subset[
            subset["classification"].astype("string").str.casefold().eq(classification.casefold())
            & subset["price_consistent"].astype(bool)
            & subset["reference_price_lbs"].notna()
        ].copy()
        if rows.empty:
            continue
        plan_lbs = pd.to_numeric(rows.get("planned_lbs"), errors="coerce")
        plan_revenue = pd.to_numeric(rows.get("planned_revenue"), errors="coerce")
        if plan_lbs.notna().any() and plan_lbs.sum(min_count=1) > 0 and plan_revenue.notna().any():
            price = float(plan_revenue.sum(min_count=1) / plan_lbs.sum(min_count=1))
        else:
            price = float(pd.to_numeric(rows["reference_price_lbs"], errors="coerce").iloc[0])
        paired[classification] = {
            "price": price,
            "source_cells": ", ".join(str(value) for value in rows.get("source_cell", pd.Series(dtype="string")).dropna().tolist()),
            "rows": int(len(rows)),
        }
    if set(paired) != {"Organic", "Conventional"}:
        missing = " and ".join(classification for classification in ("Organic", "Conventional") if classification not in paired)
        return {
            "paired": False,
            "message": f"No verified {missing} reference price is available for {selected_variety} · {selected_size}.",
            "source": "Prices",
            "period": source_period,
        }
    organic_price = paired["Organic"]["price"]
    conventional_price = paired["Conventional"]["price"]
    return {
        "paired": True,
        "variety": selected_variety,
        "size": selected_size,
        "organic_price": organic_price,
        "conventional_price": conventional_price,
        "premium": organic_price - conventional_price,
        "gross_sales_difference_per_lb": conventional_price - organic_price,
        "period": source_period,
        "source": "Prices",
        "source_cells": {
            "Organic": paired["Organic"]["source_cells"],
            "Conventional": paired["Conventional"]["source_cells"],
        },
    }


def source_inventory(report: Any) -> pd.DataFrame:
    """Return an audit-friendly status table for the requested workbook sheets."""
    available = set(getattr(report, "available_sheets", []) or [])
    frames = getattr(report, "frames", {}) or {}
    rows: list[dict[str, str]] = []
    for worksheet, role in EXPECTED_SOURCES.items():
        if worksheet not in available:
            status = "Not present"
        elif worksheet in frames and frames[worksheet] is not None and not frames[worksheet].empty:
            status = "Parsed and available"
        else:
            status = "Present but not parsed"
        rows.append({"Worksheet": worksheet, "Status": status, "Use in this tab": role})
    return pd.DataFrame(rows)


def comparison_periods(inventory_frame: pd.DataFrame) -> pd.DataFrame:
    """Return selectable ACTUAL/FORECAST periods with state-safe keys."""
    parts: list[pd.DataFrame] = []
    for state in ("Actual", "Forecast"):
        periods = inventory_periods(inventory_frame, state)
        if periods.empty:
            continue
        periods = periods.copy()
        periods["state"] = state
        periods["selection_key"] = periods.apply(
            lambda row: f"{state.casefold()}::{row['key']}", axis=1
        )
        periods["selection_label"] = periods["label"].map(lambda label: f"{state} · {label}")
        parts.append(periods)
    if not parts:
        return pd.DataFrame(
            columns=["state", "key", "selection_key", "selection_label", "week", "year", "reporting_date"]
        )
    result = pd.concat(parts, ignore_index=True, sort=False)
    state_order = pd.Categorical(result["state"], ["Actual", "Forecast"], ordered=True)
    result = result.assign(_state_order=state_order).sort_values(
        ["_sort_key", "_state_order"], kind="stable"
    )
    return result.drop(columns=["_state_order"], errors="ignore").reset_index(drop=True)


def _period_mask(frame: pd.DataFrame, period: pd.Series, state: str | None = None) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    if state is not None and "inventory_state" in frame:
        mask &= frame["inventory_state"].astype("string").str.casefold().eq(state.casefold())
    if "week" in frame and pd.notna(period.get("week")):
        mask &= pd.to_numeric(frame["week"], errors="coerce").eq(float(period["week"]))
    if "year" in frame and pd.notna(period.get("year")) and frame["year"].notna().any():
        mask &= pd.to_numeric(frame["year"], errors="coerce").eq(float(period["year"]))
    return mask


def _dimension_filter(frame: pd.DataFrame, varieties: list[str], sizes: list[str]) -> pd.DataFrame:
    result = frame.copy()
    if varieties and "variety" in result:
        result = result[result["variety"].astype("string").isin(varieties)]
    if sizes and "size" in result:
        result = result[result["size"].astype("string").isin(sizes)]
    return result


def build_comparison(
    inventory_frame: pd.DataFrame,
    sales_frame: pd.DataFrame | None,
    period: pd.Series,
    varieties: list[str] | None = None,
    sizes: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate source values for one selected inventory period.

    Inventory is selected from the workbook's explicit ACTUAL/FORECAST state.
    Demand is matched by year/week in Sales by Week when that source is
    available. Missing values remain missing; they are never replaced by zero.
    """
    varieties = varieties or []
    sizes = sizes or []
    selected_state = str(period.get("state", ""))
    inventory = _dimension_filter(inventory_frame, varieties, sizes)
    inventory = inventory.loc[_period_mask(inventory, period, selected_state)].copy()
    if inventory.empty:
        return pd.DataFrame(), {"inventory_rows": 0, "demand_rows": 0, "demand_source": "Sales by Week"}

    group_fields = [field for field in ["product", "variety", "size", "classification"] if field in inventory]
    if not group_fields:
        return pd.DataFrame(), {"inventory_rows": len(inventory), "demand_rows": 0, "demand_source": "Sales by Week"}
    inventory_summary = (
        inventory.groupby(group_fields, dropna=False, as_index=False)["stored_lbs"]
        .sum(min_count=1)
        .rename(columns={"stored_lbs": "inventory_lbs"})
    )

    demand_summary = pd.DataFrame(columns=group_fields + ["demand_lbs"])
    demand_rows = 0
    if sales_frame is not None and not sales_frame.empty and "planned_sales_lbs" in sales_frame:
        sales = _dimension_filter(sales_frame, varieties, sizes)
        sales = sales.loc[_period_mask(sales, period)].copy()
        demand_rows = len(sales)
        demand_group_fields = [field for field in group_fields if field in sales]
        if not sales.empty and demand_group_fields:
            demand_summary = (
                sales.groupby(demand_group_fields, dropna=False, as_index=False)["planned_sales_lbs"]
                .sum(min_count=1)
                .rename(columns={"planned_sales_lbs": "demand_lbs"})
            )

    result = inventory_summary.merge(demand_summary, on=group_fields, how="left")
    result["variety"] = result["variety"].astype("string").fillna("Unmapped") if "variety" in result else "Unmapped"
    result["size"] = result["size"].astype("string").fillna("Unmapped") if "size" in result else "Unmapped"
    result["classification"] = (
        result["classification"].astype("string").fillna("Unclassified")
        if "classification" in result
        else "Unclassified"
    )
    return result.sort_values(["variety", "size", "classification"], kind="stable").reset_index(drop=True), {
        "inventory_rows": len(inventory),
        "demand_rows": demand_rows,
        "demand_source": "Sales by Week",
    }


def organic_horizon_options(inventory_frame: pd.DataFrame, period: pd.Series) -> list[str]:
    """Return horizon choices supported by future forecast periods."""
    if inventory_frame is None or inventory_frame.empty:
        return []
    forecasts = inventory_periods(inventory_frame, "Forecast")
    if forecasts.empty:
        return []
    selected_key = period.get("_sort_key")
    if pd.notna(selected_key) and "_sort_key" in forecasts:
        future = forecasts[forecasts["_sort_key"] > selected_key]
    else:
        selected_week = pd.to_numeric(period.get("week"), errors="coerce")
        future = forecasts[pd.to_numeric(forecasts["week"], errors="coerce") > selected_week]
    count = len(future)
    if count == 0:
        return []
    choices = ["1 week"]
    if count >= 4:
        choices.append("4 weeks")
    if count >= 8:
        choices.append("8 weeks")
    choices.append("Remaining available season")
    return choices


def build_organic_outlook(
    inventory_frame: pd.DataFrame,
    harvest_frame: pd.DataFrame | None,
    sales_frame: pd.DataFrame | None,
    period: pd.Series,
    horizon: str,
    varieties: list[str] | None = None,
    sizes: list[str] | None = None,
) -> dict[str, Any]:
    """Reconcile organic inventory movements without inventing an ending balance.

    Forecasted Inventory Levels is treated as the authoritative projected ending
    balance. Harvest and sales are explanatory workbook components only when
    their periods and units can be aligned to the selected horizon.
    """
    varieties = varieties or []
    sizes = sizes or []
    empty: dict[str, Any] = {
        "starting_lbs": None,
        "harvest_lbs": None,
        "sales_lbs": None,
        "ending_lbs": None,
        "reconciled": False,
        "future_periods": pd.DataFrame(),
        "warnings": [],
        "source_note": "No compatible future forecast periods are available for this selection.",
    }
    if inventory_frame is None or inventory_frame.empty:
        return empty

    forecasts = inventory_periods(inventory_frame, "Forecast")
    if forecasts.empty:
        return empty
    selected_key = period.get("_sort_key")
    if pd.notna(selected_key) and "_sort_key" in forecasts:
        future = forecasts[forecasts["_sort_key"] > selected_key].copy()
    else:
        selected_week = pd.to_numeric(period.get("week"), errors="coerce")
        future = forecasts[pd.to_numeric(forecasts["week"], errors="coerce") > selected_week].copy()
    if future.empty:
        return empty

    horizon_count = {
        "1 week": 1,
        "4 weeks": 4,
        "8 weeks": 8,
        "Remaining available season": len(future),
    }.get(horizon, 1)
    future = future.head(horizon_count).copy()
    future_weeks = set(pd.to_numeric(future["week"], errors="coerce").dropna().astype(int).tolist())
    future_year_weeks = {
        (int(row["year"]), int(row["week"]))
        for _, row in future.iterrows()
        if pd.notna(row.get("year")) and pd.notna(row.get("week"))
    }

    def organic_dimensions(frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None or frame.empty or "classification" not in frame:
            return pd.DataFrame()
        result = frame[frame["classification"].astype("string").str.casefold().eq("organic")].copy()
        if varieties and "variety" in result:
            result = result[result["variety"].astype("string").isin(varieties)]
        if sizes and "size" in result:
            result = result[result["size"].astype("string").isin(sizes)]
        return result

    def period_mask(frame: pd.DataFrame, selected: pd.Series, state: str) -> pd.Series:
        mask = frame["inventory_state"].astype("string").str.casefold().eq(state.casefold())
        mask &= pd.to_numeric(frame["week"], errors="coerce").eq(float(selected["week"]))
        if "year" in frame and pd.notna(selected.get("year")):
            mask &= pd.to_numeric(frame["year"], errors="coerce").eq(float(selected["year"]))
        return mask

    inventory = organic_dimensions(inventory_frame)
    starting = inventory.loc[period_mask(inventory, period, str(period.get("state", "Actual")))]
    ending_period = future.iloc[-1]
    ending = inventory.loc[period_mask(inventory, ending_period, "Forecast")]
    starting_lbs = pd.to_numeric(starting.get("stored_lbs"), errors="coerce").sum(min_count=1) if not starting.empty else None
    ending_lbs = pd.to_numeric(ending.get("stored_lbs"), errors="coerce").sum(min_count=1) if not ending.empty else None

    warnings: list[str] = []
    harvest = organic_dimensions(harvest_frame)
    if harvest.empty:
        harvest_lbs = None
        warnings.append("No organic harvest rows match the selected variety and size.")
    else:
        harvest = harvest[pd.to_numeric(harvest["week"], errors="coerce").isin(future_weeks)]
        harvest_lbs = pd.to_numeric(harvest.get("harvest_lbs"), errors="coerce").sum(min_count=1) if not harvest.empty else None
        if "year" not in harvest_frame.columns or harvest_frame["year"].isna().all():
            warnings.append("Forecast Harvest by Week HC does not supply a crop year; harvest is matched by week only.")

    sales = organic_dimensions(sales_frame)
    if sales.empty:
        sales_lbs = None
        warnings.append("No organic planned-sales rows match the selected variety and size.")
    else:
        if future_year_weeks and "year" in sales:
            sales_keys = list(zip(
                pd.to_numeric(sales["year"], errors="coerce"),
                pd.to_numeric(sales["week"], errors="coerce"),
            ))
            sales = sales[[key in future_year_weeks for key in sales_keys]]
        else:
            sales = sales[pd.to_numeric(sales["week"], errors="coerce").isin(future_weeks)]
        sales_lbs = pd.to_numeric(sales.get("planned_sales_lbs"), errors="coerce").sum(min_count=1) if not sales.empty else None

    reconciled = False
    if all(value is not None and pd.notna(value) for value in (starting_lbs, harvest_lbs, sales_lbs, ending_lbs)):
        expected = float(starting_lbs) + float(harvest_lbs) - float(sales_lbs)
        reconciled = abs(expected - float(ending_lbs)) <= max(1.0, abs(float(ending_lbs)) * 0.005)
        if not reconciled:
            warnings.append("Workbook projected ending inventory does not reconcile to the displayed harvest and sales components; the projected balance is shown as authoritative context.")
    elif ending_lbs is None:
        warnings.append("A compatible workbook-projected ending inventory is not available for this selection.")

    source_note = (
        f"Starting balance: Forecasted Inventory Levels · {period.get('label', 'selected period')}. "
        f"Ending balance: Forecasted Inventory Levels · {ending_period.get('label', 'selected forecast')}. "
        "Harvest is from Forecast Harvest by Week HC and planned sales are from Sales by Week."
    )
    return {
        "starting_lbs": starting_lbs,
        "harvest_lbs": harvest_lbs,
        "sales_lbs": sales_lbs,
        "ending_lbs": ending_lbs,
        "reconciled": reconciled,
        "future_periods": future,
        "warnings": warnings,
        "source_note": source_note,
    }


def classification_matrix(comparison: pd.DataFrame) -> pd.DataFrame:
    """Pivot the comparison into organic/conventional display columns."""
    if comparison is None or comparison.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for (variety, size), group in comparison.groupby(["variety", "size"], dropna=False):
        row: dict[str, Any] = {"Variety": variety, "Size": size}
        for classification, label in (("Organic", "Organic"), ("Conventional", "Conventional")):
            selected = group[group["classification"].astype("string").str.casefold() == classification.casefold()]
            row[f"{label} inventory (lbs)"] = selected["inventory_lbs"].sum(min_count=1) if not selected.empty else pd.NA
            row[f"{label} demand (lbs)"] = selected["demand_lbs"].sum(min_count=1) if not selected.empty else pd.NA
        rows.append(row)
    return pd.DataFrame(rows)


def reconciliation_report(
    inventory_frame: pd.DataFrame,
    sales_frame: pd.DataFrame | None,
    period: pd.Series,
    varieties: list[str] | None = None,
    sizes: list[str] | None = None,
) -> pd.DataFrame:
    """Return row-level source evidence for the selected week.

    Rows are never removed from this audit because they look unusual. A row
    is marked excluded only when it cannot contribute to the organic or
    conventional classified totals (for example, an unrecognized class).
    Process rows remain visible so their treatment can be confirmed against
    the workbook rather than silently being folded into a total.
    """
    varieties = varieties or []
    sizes = sizes or []
    inventory = _dimension_filter(inventory_frame, varieties, sizes)
    inventory = inventory.loc[_period_mask(inventory, period, str(period.get("state", "")))].copy()
    comparison = build_comparison(inventory_frame, sales_frame, period, varieties, sizes)[0]
    totals: dict[str, float | None] = {}
    if not comparison.empty and "classification" in comparison:
        for classification in ("Organic", "Conventional"):
            values = comparison.loc[
                comparison["classification"].astype("string").str.casefold() == classification.casefold(),
                "inventory_lbs",
            ]
            totals[classification] = float(values.sum(min_count=1)) if not values.empty and pd.notna(values.sum(min_count=1)) else None

    rows: list[dict[str, Any]] = []

    def safe_text(value: object, fallback: str = "") -> str:
        if value is None or pd.isna(value):
            return fallback
        return str(value).strip()

    def add_rows(frame: pd.DataFrame, measure: str, worksheet_fallback: str) -> None:
        if frame is None or frame.empty:
            return
        for _, row in frame.iterrows():
            classification = safe_text(row.get("classification"))
            normalized_class = classification.title() if classification else ""
            product = safe_text(row.get("product"))
            process_row = "process" in product.casefold() or "process" in safe_text(row.get("product_description")).casefold()
            classified = normalized_class in {"Organic", "Conventional"}
            if measure == "Inventory":
                included = classified
                reason = (
                    "Included in classified total; process row retained because the workbook supplies a recognized classification."
                    if process_row and included
                    else "Included in classified total."
                    if included
                    else "Excluded from classified totals because organic/conventional classification is missing or unrecognized; retained for review."
                )
                value = row.get("stored_lbs")
                reconciled = totals.get(normalized_class)
            else:
                included = classified
                reason = (
                    "Included in planned-demand total; process row retained for source reconciliation."
                    if process_row and included
                    else "Included in planned-demand total."
                    if included
                    else "Excluded from classified planned-demand totals because organic/conventional classification is missing or unrecognized; retained for review."
                )
                value = row.get("planned_sales_lbs")
                reconciled = None
            rows.append(
                {
                    "Measure": measure,
                    "Source worksheet": safe_text(row.get("source_sheet"), worksheet_fallback),
                    "Source cell": safe_text(row.get("source_cell"), "Unavailable"),
                    "Product code": safe_text(row.get("lookup_identifier"), "—"),
                    "Product": product or "—",
                    "Classification": normalized_class or "Unclassified",
                    "Source value (lbs)": value,
                    "Included": "Yes" if included else "No",
                    "Reason": reason,
                    "Reconciled classified total (lbs)": reconciled,
                }
            )

    add_rows(inventory, "Inventory", "Forecasted Inventory Levels")
    if sales_frame is not None and not sales_frame.empty:
        sales = _dimension_filter(sales_frame, varieties, sizes)
        sales = sales.loc[_period_mask(sales, period)].copy()
        add_rows(sales, "Planned demand", "Sales by Week")
    return pd.DataFrame(rows)

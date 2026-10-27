"""Reusable Plotly charts for verified workbook fields."""
from __future__ import annotations

import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def _display_variety(value: object) -> str | None:
    """Normalize embedded classification descriptions for chart labels only."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    marker = re.search(r"\b(?:Conv\.|Org\.)\s+([A-Za-z][A-Za-z'’-]*)", text, flags=re.IGNORECASE)
    return marker.group(1).strip() if marker else text


def inventory_comparison(frame: pd.DataFrame):
    group_fields = [field for field in ["product", "variety", "classification", "size"] if field in frame]
    summary = frame.groupby(group_fields, dropna=False, as_index=False)[["stored_lbs", "forecast_demand_lbs"]].sum(min_count=1)
    summary["product_label"] = summary[group_fields].astype("string").fillna("Missing").agg(" ".join, axis=1)
    chart_data = summary.melt(id_vars=["product_label"], value_vars=["stored_lbs", "forecast_demand_lbs"], var_name="measure", value_name="pounds").dropna(subset=["pounds"])
    chart_data["measure"] = chart_data["measure"].map({"stored_lbs": "In storage", "forecast_demand_lbs": "Forecast demand"})
    fig = px.bar(chart_data, x="pounds", y="product_label", color="measure", orientation="h", barmode="group", labels={"product_label": "Product", "pounds": "Pounds", "measure": ""}, color_discrete_map={"In storage": "#2f855a", "Forecast demand": "#e07a3f"})
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0), margin=dict(l=190, r=30, t=100, b=70), height=max(420, chart_data["product_label"].nunique() * 72 + 120))
    fig.update_yaxes(automargin=True)
    fig.update_xaxes(automargin=True)
    return fig


def inventory_periods(frame: pd.DataFrame, state: str) -> pd.DataFrame:
    """Return distinct workbook periods for an ACTUAL or FORECAST state.

    The Forecasted Inventory Levels parser preserves the workbook's state, week,
    and reporting-date fields. This helper keeps period selection tied to those
    source values instead of deriving a new forecast calendar.
    """
    required = {"inventory_state", "week"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=["key", "label", "week", "year", "reporting_date"])

    subset = frame[frame["inventory_state"].astype("string").str.casefold() == state.casefold()].copy()
    subset["week"] = pd.to_numeric(subset["week"], errors="coerce")
    subset = subset.dropna(subset=["week"])
    if subset.empty:
        return pd.DataFrame(columns=["key", "label", "week", "year", "reporting_date"])

    group_fields = ["week"]
    if "year" in subset.columns and subset["year"].notna().any():
        group_fields.insert(0, "year")
    aggregate = {"reporting_date": ("reporting_date", "max")} if "reporting_date" in subset.columns else {}
    periods = subset.groupby(group_fields, dropna=False, as_index=False).agg(**aggregate) if aggregate else subset[group_fields].drop_duplicates()
    if "reporting_date" not in periods:
        periods["reporting_date"] = pd.NaT
    periods["reporting_date"] = pd.to_datetime(periods["reporting_date"], errors="coerce")
    periods["week"] = pd.to_numeric(periods["week"], errors="coerce")
    if "year" in periods:
        periods["year"] = pd.to_numeric(periods["year"], errors="coerce")

    def period_label(row: pd.Series) -> str:
        week = f"Week {int(row['week'])}" if pd.notna(row["week"]) else "Week unavailable"
        date = row.get("reporting_date")
        date_label = f" · {date:%b %d, %Y}" if pd.notna(date) else ""
        return f"{week}{date_label}"

    def period_key(row: pd.Series) -> str:
        year = int(row["year"]) if "year" in row and pd.notna(row["year"]) else ""
        week = int(row["week"]) if pd.notna(row["week"]) else ""
        date = row["reporting_date"].isoformat() if pd.notna(row["reporting_date"]) else ""
        return f"{year}|{week}|{date}"

    periods["label"] = periods.apply(period_label, axis=1)
    periods["key"] = periods.apply(period_key, axis=1)
    periods["_sort_date"] = periods["reporting_date"].fillna(pd.Timestamp.min)
    periods["_sort_key"] = periods["_sort_date"].astype("int64")
    periods = periods.sort_values(["_sort_key", "week"], kind="stable").reset_index(drop=True)
    return periods.reset_index(drop=True)


def inventory_current_vs_projected(
    frame: pd.DataFrame,
    forecast_period_key: str | None = None,
    group_by: str = "Variety",
):
    """Compare the latest ACTUAL inventory with a selected FORECAST period."""
    required = {"inventory_state", "week", "stored_lbs"}
    if not required.issubset(frame.columns):
        return None

    actual_periods = inventory_periods(frame, "Actual")
    forecast_periods = inventory_periods(frame, "Forecast")
    if actual_periods.empty or forecast_periods.empty:
        return None
    actual_period = actual_periods.iloc[-1]
    if forecast_period_key and forecast_period_key in forecast_periods["key"].tolist():
        forecast_period = forecast_periods.loc[forecast_periods["key"] == forecast_period_key].iloc[0]
    else:
        later = forecast_periods[forecast_periods["_sort_key"] > actual_period["_sort_key"]] if "_sort_key" in forecast_periods else forecast_periods.iloc[0:0]
        forecast_period = (later.iloc[0] if not later.empty else forecast_periods.iloc[0])

    def period_mask(source: pd.DataFrame, period: pd.Series, state: str) -> pd.Series:
        mask = source["inventory_state"].astype("string").str.casefold().eq(state.casefold())
        mask &= pd.to_numeric(source["week"], errors="coerce").eq(float(period["week"]))
        if "year" in source and "year" in period and pd.notna(period["year"]):
            mask &= pd.to_numeric(source["year"], errors="coerce").eq(float(period["year"]))
        return mask

    actual_rows = frame.loc[period_mask(frame, actual_period, "Actual")].copy()
    forecast_rows = frame.loc[period_mask(frame, forecast_period, "Forecast")].copy()
    grouped_by_variety = group_by.casefold() == "variety"
    if grouped_by_variety:
        identity_fields = ["variety"] if "variety" in frame.columns else ["product"]
    else:
        identity_fields = [field for field in ["product", "variety", "classification", "size", "packaging_type"] if field in frame.columns]
    if not identity_fields or actual_rows.empty or forecast_rows.empty:
        return None

    actual_summary = actual_rows.groupby(identity_fields, dropna=False, as_index=False)["stored_lbs"].sum(min_count=1).rename(columns={"stored_lbs": "actual_lbs"})
    forecast_summary = forecast_rows.groupby(identity_fields, dropna=False, as_index=False)["stored_lbs"].sum(min_count=1).rename(columns={"stored_lbs": "projected_lbs"})
    summary = actual_summary.merge(forecast_summary, on=identity_fields, how="outer")

    def product_label(row: pd.Series) -> str:
        if grouped_by_variety:
            value = row.get("variety")
            if value is not None and not pd.isna(value) and str(value).strip():
                return _display_variety(value) or "Other / unclassified"
            return "Other / unclassified"
        parts = []
        for field in ["variety", "size", "classification"]:
            value = row.get(field)
            if value is not None and not pd.isna(value) and str(value).strip():
                text = _display_variety(value) if field == "variety" else str(value).strip()
                if text not in parts:
                    parts.append(text)
        if parts:
            return " · ".join(parts)
        product = row.get("product")
        return "Unknown product" if product is None or pd.isna(product) else str(product).strip()

    summary["product_label"] = summary.apply(product_label, axis=1)
    summary = summary.sort_values("product_label", key=lambda values: values.astype("string").str.casefold(), kind="stable")
    actual_label = str(actual_period["label"])
    forecast_label = str(forecast_period["label"])
    rows: list[dict[str, object]] = []
    for _, item in summary.iterrows():
        actual_value = pd.to_numeric(item.get("actual_lbs"), errors="coerce")
        projected_value = pd.to_numeric(item.get("projected_lbs"), errors="coerce")
        actual_display = float(actual_value) if pd.notna(actual_value) else None
        projected_display = float(projected_value) if pd.notna(projected_value) else None
        net_change = projected_display - actual_display if actual_display is not None and projected_display is not None else None
        base = {
            "product_label": item["product_label"],
            "actual_week": actual_label,
            "actual_lbs": actual_display,
            "forecast_week": forecast_label,
            "projected_lbs": projected_display,
            "net_change_lbs": net_change,
        }
        if actual_display is not None:
            rows.append({**base, "measure": "Actual Inventory", "pounds": actual_display})
        if projected_display is not None:
            rows.append({**base, "measure": "Projected Inventory", "pounds": projected_display})
    chart_data = pd.DataFrame(rows)
    if chart_data.empty:
        return None

    fig = px.bar(
        chart_data,
        x="pounds",
        y="product_label",
        color="measure",
        orientation="h",
        barmode="group",
        custom_data=["product_label", "actual_week", "actual_lbs", "forecast_week", "projected_lbs", "net_change_lbs"],
        labels={"product_label": "Variety" if grouped_by_variety else "Product", "pounds": "Pounds", "measure": ""},
        color_discrete_map={"Actual Inventory": "#32936B", "Projected Inventory": "#F47C35"},
    )
    actual_hover = (
        "<b>%{customdata[0]}</b><br>"
        "Actual inventory week: %{customdata[1]}<br>"
        "Actual inventory: %{customdata[2]:,.0f} lbs<br>"
        "Projected net change: %{customdata[5]:+,.0f} lbs<extra></extra>"
    )
    projected_hover = (
        "<b>%{customdata[0]}</b><br>"
        "Selected forecast week: %{customdata[3]}<br>"
        "Projected inventory: %{customdata[4]:,.0f} lbs<br>"
        "Projected net change: %{customdata[5]:+,.0f} lbs<extra></extra>"
    )
    for trace in fig.data:
        trace.hovertemplate = actual_hover if trace.name == "Actual Inventory" else projected_hover
    # Plotly renders the first horizontal grouped trace below the second. Keep
    # the legend ranked Actual → Projected while placing Actual above Projected.
    fig.data = tuple(sorted(fig.data, key=lambda trace: 0 if trace.name == "Projected Inventory" else 1))
    for trace in fig.data:
        trace.legendrank = 1 if trace.name == "Actual Inventory" else 2
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        margin=dict(l=210, r=30, t=100, b=70),
        height=max(400, min(720, chart_data["product_label"].nunique() * 55 + 140)),
        bargap=0.24,
    )
    fig.update_yaxes(automargin=True, categoryorder="array", categoryarray=summary["product_label"].tolist(), tickfont=dict(size=12))
    fig.update_xaxes(automargin=True, zeroline=True, zerolinecolor="#718096", zerolinewidth=1)
    return fig


def inventory_timeline(frame: pd.DataFrame):
    group_fields = [field for field in ["year", "week"] if field in frame and frame[field].notna().any()]
    if not group_fields:
        return None
    timeline = frame.groupby(group_fields, dropna=False, as_index=False)[["stored_lbs", "forecast_demand_lbs"]].sum(min_count=1).sort_values(group_fields)
    if "year" in timeline and "week" in timeline:
        timeline["period"] = timeline["year"].astype("Int64").astype(str) + " W" + timeline["week"].astype("Int64").astype(str).str.zfill(2)
    else:
        timeline["period"] = timeline[group_fields[0]].astype(str)
    chart_data = timeline.melt(id_vars=["period"], value_vars=["stored_lbs", "forecast_demand_lbs"], var_name="measure", value_name="pounds").dropna(subset=["pounds"])
    chart_data["measure"] = chart_data["measure"].map({"stored_lbs": "In storage", "forecast_demand_lbs": "Forecast demand"})
    return px.line(chart_data, x="period", y="pounds", color="measure", markers=True, labels={"period": "Reporting period", "pounds": "Pounds", "measure": ""})


def classification_comparison(frame: pd.DataFrame):
    summary = frame.dropna(subset=["classification"]).groupby("classification", as_index=False)["stored_lbs"].sum(min_count=1)
    return px.bar(summary, x="classification", y="stored_lbs", color="classification", labels={"stored_lbs": "Stored pounds", "classification": "Classification"}, color_discrete_map={"Organic": "#2f855a", "Conventional": "#e07a3f"})


def inventory_mix_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the verified stored-pound population used by the mix chart."""
    if "classification" not in frame or "stored_lbs" not in frame:
        return pd.DataFrame(columns=["classification", "stored_lbs"])
    summary = (
        frame.dropna(subset=["classification", "stored_lbs"])
        .groupby("classification", as_index=False)["stored_lbs"]
        .sum(min_count=1)
    )
    return summary.dropna(subset=["stored_lbs"])


def inventory_mix_donut(frame: pd.DataFrame):
    """Show stored pounds by verified classification with a compact center total."""
    summary = inventory_mix_summary(frame)
    if summary.empty:
        return None
    colors = {"Organic": "#3c9672", "Conventional": "#f47c35"}
    total_stored = float(summary["stored_lbs"].sum())
    fig = go.Figure(
        go.Pie(
            labels=summary["classification"],
            values=summary["stored_lbs"],
            hole=0.60,
            sort=False,
            marker=dict(colors=[colors.get(value, "#8fa3b5") for value in summary["classification"]]),
            textinfo="none",
            hovertemplate="%{label}<br>%{value:,.0f} lbs<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=210,
        showlegend=False,
        annotations=[
            dict(text=f"<b>{total_stored:,.0f}</b>", x=0.5, y=0.58, xref="paper", yref="paper", showarrow=False, font=dict(size=20, color="#F5F7FA")),
            dict(text="<b>lbs</b>", x=0.5, y=0.45, xref="paper", yref="paper", showarrow=False, font=dict(size=17, color="#F5F7FA")),
            dict(text="in storage", x=0.5, y=0.345, xref="paper", yref="paper", showarrow=False, font=dict(size=12, color="#B9C7D5")),
        ],
    )
    return fig


def dimension_bar(frame: pd.DataFrame, dimension: str, title: str):
    summary = frame.dropna(subset=[dimension]).groupby(dimension, as_index=False)["stored_lbs"].sum(min_count=1).sort_values("stored_lbs", ascending=False)
    return px.bar(summary, x="stored_lbs", y=dimension, orientation="h", labels={"stored_lbs": "Stored pounds", dimension: title})


def measure_timeline(frame: pd.DataFrame, measures: list[str]):
    values = [measure for measure in measures if measure in frame and frame[measure].notna().any()]
    if not values:
        return None
    if "week" in frame and frame["week"].notna().any():
        group_fields = ["week"]
        if "year" in frame and frame["year"].notna().any():
            group_fields = ["year", "week"]
        timeline = frame.groupby(group_fields, as_index=False)[values].sum(min_count=1).sort_values(group_fields)
        if group_fields == ["year", "week"]:
            timeline["period"] = timeline["year"].astype("Int64").astype(str) + " W" + timeline["week"].astype("Int64").astype(str).str.zfill(2)
            x_label = "Reporting period"
        else:
            timeline["period"] = "Week " + timeline["week"].astype("Int64").astype(str)
            x_label = "Workbook week (crop year not supplied)"
        chart_data = timeline.melt(id_vars=["period"], value_vars=values, var_name="measure", value_name="value").dropna(subset=["value"])
        return px.line(chart_data, x="period", y="value", color="measure", markers=True, labels={"period": x_label, "value": "Pounds (as supplied)", "measure": ""})
    if "period_label" in frame and frame["period_label"].notna().any():
        timeline = frame.groupby("period_label", as_index=False)[values].sum(min_count=1)
        chart_data = timeline.melt(id_vars=["period_label"], value_vars=values, var_name="measure", value_name="value").dropna(subset=["value"])
        return px.line(chart_data, x="period_label", y="value", color="measure", markers=True, labels={"period_label": "Workbook period", "value": "Pounds (as supplied)", "measure": ""})
    group_fields = [field for field in ["year", "week"] if field in frame and frame[field].notna().any()]
    if not group_fields:
        return None
    values = [measure for measure in measures if measure in frame and frame[measure].notna().any()]
    if not values:
        return None
    timeline = frame.groupby(group_fields, dropna=False, as_index=False)[values].sum(min_count=1).sort_values(group_fields)
    if "year" in timeline and "week" in timeline:
        timeline["period"] = timeline["year"].astype("Int64").astype(str) + " W" + timeline["week"].astype("Int64").astype(str).str.zfill(2)
    else:
        timeline["period"] = timeline[group_fields[0]].astype(str)
    chart_data = timeline.melt(id_vars=["period"], value_vars=values, var_name="measure", value_name="value").dropna(subset=["value"])
    return px.line(chart_data, x="period", y="value", color="measure", markers=True, labels={"period": "Reporting period", "value": "Value", "measure": ""})


def packout_chart(frame: pd.DataFrame):
    if "packout_pct" not in frame or not frame["packout_pct"].notna().any():
        return None
    if "period_label" in frame and frame["period_label"].notna().any():
        summary = frame.dropna(subset=["packout_pct", "period_label"]).groupby("period_label", as_index=False)["packout_pct"].mean()
        if "month" in frame:
            order = frame.dropna(subset=["packout_pct", "period_label", "month"])[["period_label", "month", "year"]].drop_duplicates()
            order["month_number"] = pd.to_datetime(order["month"], format="%B", errors="coerce").dt.month
            order["year_sort"] = pd.to_numeric(order["year"], errors="coerce").fillna(-1)
            order = order.sort_values(["year_sort", "month_number"])
            category_order = order["period_label"].drop_duplicates().tolist()
        else:
            category_order = []
        fig = px.line(summary, x="period_label", y="packout_pct", markers=True, labels={"period_label": "Month", "packout_pct": "Packout percentage"})
        if category_order:
            fig.update_xaxes(categoryorder="array", categoryarray=category_order)
        fig.update_yaxes(tickformat=".0%")
        return fig
    fields = [field for field in ["year", "week"] if field in frame and frame[field].notna().any()]
    if fields:
        summary = frame.groupby(fields, as_index=False)["packout_pct"].mean().sort_values(fields)
        summary["period"] = summary[fields].astype("Int64").astype(str).agg(" W".join, axis=1) if len(fields) > 1 else summary[fields[0]].astype(str)
        fig = px.line(summary, x="period", y="packout_pct", markers=True, labels={"period": "Reporting period", "packout_pct": "Packout percentage"})
        fig.update_yaxes(tickformat=".0%")
        return fig
    summary = frame.dropna(subset=["packout_pct"]).reset_index().rename(columns={"index": "record"})
    fig = px.bar(summary, x="record", y="packout_pct", labels={"record": "Record", "packout_pct": "Packout percentage"})
    fig.update_yaxes(tickformat=".0%")
    return fig


def load_comparison(frame: pd.DataFrame):
    measures = [measure for measure in ["purchased_loads", "remaining_loads"] if measure in frame and frame[measure].notna().any()]
    if not measures:
        return None
    summary = frame[measures].sum(min_count=1).reset_index()
    summary.columns = ["measure", "loads"]
    return px.bar(summary, x="measure", y="loads", labels={"measure": "Load measure", "loads": "Loads"})


def loads_timeline(frame: pd.DataFrame):
    if "loads_per_week" not in frame or not frame["loads_per_week"].notna().any() or "week" not in frame:
        return None
    weekly = frame.dropna(subset=["loads_per_week", "week"]).groupby("week", as_index=False)["loads_per_week"].sum(min_count=1).sort_values("week")
    if weekly.empty:
        return None
    weekly["period"] = "Week " + weekly["week"].astype(int).astype(str)
    fig = px.line(
        weekly,
        x="period",
        y="loads_per_week",
        markers=True,
        labels={"period": "Workbook week (crop year not supplied)", "loads_per_week": "Loads"},
    )
    fig.update_xaxes(categoryorder="array", categoryarray=weekly["period"].tolist(), tickangle=0)
    return fig

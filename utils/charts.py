"""Reusable Plotly charts for verified workbook fields."""
from __future__ import annotations

import pandas as pd
import plotly.express as px


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

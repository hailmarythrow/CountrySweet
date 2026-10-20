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
    fields = [field for field in ["year", "week"] if field in frame and frame[field].notna().any()]
    if fields:
        summary = frame.groupby(fields, as_index=False)["packout_pct"].mean().sort_values(fields)
        summary["period"] = summary[fields].astype("Int64").astype(str).agg(" W".join, axis=1) if len(fields) > 1 else summary[fields[0]].astype(str)
        return px.line(summary, x="period", y="packout_pct", markers=True, labels={"period": "Reporting period", "packout_pct": "Packout percentage"})
    summary = frame.dropna(subset=["packout_pct"]).reset_index().rename(columns={"index": "record"})
    return px.bar(summary, x="record", y="packout_pct", labels={"record": "Record", "packout_pct": "Packout percentage"})


def load_comparison(frame: pd.DataFrame):
    measures = [measure for measure in ["purchased_loads", "remaining_loads"] if measure in frame and frame[measure].notna().any()]
    if not measures:
        return None
    summary = frame[measures].sum(min_count=1).reset_index()
    summary.columns = ["measure", "loads"]
    return px.bar(summary, x="measure", y="loads", labels={"measure": "Load measure", "loads": "Loads"})

"""Small reusable UI components for source attribution and quality messaging."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def source_caption(sheet: str | None, frame: pd.DataFrame | None, period: str) -> None:
    if sheet and frame is not None:
        st.caption(f"Source worksheet: **{sheet}** · Reporting period: **{period}** · Values shown as supplied")
    else:
        st.caption("No verified workbook worksheet is available for this view.")


def render_quality_alerts(warnings: list[str]) -> None:
    for warning in warnings:
        st.warning(warning, icon="⚠️")


def metric_or_unavailable(label: str, value: object, suffix: str = "") -> None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        st.metric(label, "Unavailable")
    else:
        st.metric(label, f"{value}{suffix}")

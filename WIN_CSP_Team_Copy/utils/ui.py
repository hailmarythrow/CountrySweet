"""Small reusable UI components for source attribution and quality messaging."""
from __future__ import annotations

import logging
from time import perf_counter

import pandas as pd
import streamlit as st

from utils.theme import style_figure


LOGGER = logging.getLogger("country_sweet.dashboard")


def source_caption(sheet: str | None, frame: pd.DataFrame | None, period: str) -> None:
    if sheet and frame is not None:
        st.caption(f"Source worksheet: **{sheet}** · Reporting period: **{period}** · Values shown as supplied")
    else:
        st.caption("No verified workbook worksheet is available for this view.")


def render_quality_alerts(warnings: list[str]) -> None:
    for warning in warnings:
        st.warning(warning, icon="⚠️")


def render_chart(builder, label: str, alt: str):
    """Build, theme, and render one chart while recording its elapsed time."""
    started = perf_counter()
    figure = builder()
    elapsed_ms = (perf_counter() - started) * 1000
    LOGGER.info("dashboard.chart_generation chart=%s elapsed_ms=%.1f", label, elapsed_ms)
    st.session_state.setdefault("chart_timings", {})[label] = elapsed_ms
    if figure is None:
        return None
    st.plotly_chart(style_figure(figure), width="stretch", theme=None, alt=alt)
    return figure


def metric_or_unavailable(label: str, value: object, suffix: str = "") -> None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        st.metric(label, "Unavailable")
    else:
        st.metric(label, f"{value}{suffix}")

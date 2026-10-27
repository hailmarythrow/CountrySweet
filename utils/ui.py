"""Small reusable UI components for source attribution and quality messaging."""
from __future__ import annotations

import html
import logging
from time import perf_counter

import pandas as pd
import streamlit as st

from utils.excel_loader import WorkbookReport, report_to_frame
from utils.theme import style_figure


LOGGER = logging.getLogger("country_sweet.dashboard")


def source_caption(sheet: str | None, frame: pd.DataFrame | None, period: str) -> None:
    if sheet and frame is not None:
        st.caption(f"Source worksheet: **{sheet}** · Reporting period: **{period}** · Values shown as supplied")
    else:
        st.caption("No verified workbook worksheet is available for this view.")


def render_quality_alerts(warnings: list[str]) -> None:
    """Backward-compatible compact warning summary."""
    if warnings:
        st.warning(f"Data quality review: {len(warnings)} issue(s) detected. Expand the details panel below.", icon="⚠️")


def render_upload_success(filename: str, record_count: int, *, source_type: str = "upload") -> None:
    """Keep a durable, prominent confirmation above the worksheet selector."""
    heading = "Excel workbook uploaded successfully!" if source_type == "upload" else "Excel workbook loaded successfully!"
    st.markdown(
        "<div class='cs-upload-feedback cs-upload-feedback--success'>"
        "<div class='cs-upload-feedback-icon' aria-hidden='true'>✓</div>"
        "<div class='cs-upload-feedback-copy'>"
        f"<div class='cs-upload-feedback-title'>{heading}</div>"
        f"<div class='cs-upload-feedback-message'>We've received your Excel workbook and loaded <strong>{record_count:,} inventory records</strong>. Your dashboard is ready to explore.</div>"
        f"<div class='cs-upload-feedback-file'>File: <strong>{html.escape(filename)}</strong></div>"
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_upload_error(message: str, *, filename: str | None = None, retained_previous: bool = False) -> None:
    """Show a durable upload error without implying that the file was accepted."""
    file_suffix = f" File: <strong>{html.escape(filename)}</strong>." if filename else ""
    retained_suffix = " The previous successfully loaded data remains active." if retained_previous else ""
    st.markdown(
        "<div class='cs-upload-feedback cs-upload-feedback--error'>"
        "<div class='cs-upload-feedback-icon' aria-hidden='true'>!</div>"
        "<div class='cs-upload-feedback-copy'>"
        "<div class='cs-upload-feedback-title'>Excel workbook upload failed</div>"
        f"<div class='cs-upload-feedback-message'>{html.escape(message)}{file_suffix}{html.escape(retained_suffix)}</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_quality_summary(report: WorkbookReport) -> None:
    """Render one compact quality summary with expandable source details."""
    formula_warnings = [warning for warning in report.warnings if "formula error" in warning.lower() or "cached value" in warning.lower()]
    review_warnings = [warning for warning in report.warnings if warning not in formula_warnings]
    critical = sum(item.formula_errors for item in report.sheet_reports.values())
    missing_cached = sum(item.missing_formula_results for item in report.sheet_reports.values())
    if critical or missing_cached or review_warnings:
        cached_text = f" and {missing_cached} missing cached result(s)" if missing_cached else ""
        st.markdown(
            "<div class='cs-upload-feedback cs-upload-feedback--quality'>"
            "<div class='cs-upload-feedback-icon' aria-hidden='true'>!</div>"
            "<div class='cs-upload-feedback-copy'>"
            "<div class='cs-upload-feedback-title'>Your workbook was uploaded, but we found some items to review.</div>"
            f"<div class='cs-upload-feedback-message'>We successfully loaded your workbook and detected <strong>{critical} formula error(s)</strong>{cached_text} and <strong>{len(review_warnings)} review warning(s)</strong>. You can continue exploring the loaded data, but some affected calculations may need verification.</div>"
            "</div></div>",
            unsafe_allow_html=True,
        )
    with st.expander("View data-quality details"):
        if report.warnings:
            for warning in report.warnings:
                st.caption(f"• {warning}")
        details = report_to_frame(report)
        details = details[(details["Formula errors"] > 0) | (details["Missing cached results"] > 0) | (details["Status"].astype(str).str.contains("Imported", na=False))]
        st.dataframe(details, width="stretch", hide_index=True)


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

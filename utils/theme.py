"""Centralized dashboard theme tokens and Plotly styling."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st


def theme_tokens(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "background": "#111827", "sidebar": "#172033", "card": "#1f2937",
            "text": "#f3f4f6", "heading": "#F9FAFB", "muted": "#cbd5e1", "border": "#374151",
            "nav_background": "#111827", "tab_inactive": "#B9C2CF",
            "accent": "#FF4655", "accent_strong": "#ff8b96", "hover": "#263449",
            "chart": "#111827", "grid": "#374151",
        }
    return {
        "background": "#F7F8F6", "sidebar": "#eef3ec", "card": "#ffffff",
        "text": "#18332a", "heading": "#17212F", "muted": "#52645a", "border": "#d8e3d5",
        "nav_background": "#F7F8F6", "tab_inactive": "#666D75",
        "accent": "#FF4655", "accent_strong": "#c92f3d", "hover": "#f8e4df",
        "chart": "#ffffff", "grid": "#d8e3d5",
    }


def apply_theme(dark: bool) -> None:
    colors = theme_tokens(dark)
    st.markdown(f"""<style>
    :root {{
        --cs-background: {colors['background']}; --cs-sidebar: {colors['sidebar']};
        --cs-card: {colors['card']}; --cs-text: {colors['text']}; --cs-muted: {colors['muted']};
        --cs-heading: {colors['heading']}; --cs-border: {colors['border']};
        --cs-nav-background: {colors['nav_background']}; --cs-tab-inactive: {colors['tab_inactive']};
        --cs-accent: {colors['accent']};
        --cs-accent-strong: {colors['accent_strong']}; --cs-hover: {colors['hover']};
    }}
    [data-testid="stAppViewContainer"] {{ background: var(--cs-background); color: var(--cs-text); }}
    [data-testid="stHeader"] {{ background: var(--cs-background); }}
    [data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3 {{ color: var(--cs-heading) !important; }}
    section[data-testid="stSidebar"] {{ background: var(--cs-sidebar); }}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {{ color: var(--cs-text); }}
    div[data-testid="stMetric"] {{ background: var(--cs-card); border: 1px solid var(--cs-border); padding: 1rem; border-radius: .75rem; }}
    div[data-testid="stMetric"] [data-testid="stMetricLabel"], div[data-testid="stMetric"] [data-testid="stMetricValue"], div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{ color: var(--cs-text) !important; }}
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ font-size: 1.6rem; }}
    div[data-baseweb="select"] > div {{ background: var(--cs-card); color: var(--cs-text); border-color: var(--cs-border); }}
    div[data-baseweb="select"] span, div[data-baseweb="select"] input {{ color: var(--cs-text) !important; }}
    div.stButton > button, div[data-testid="stDownloadButton"] button {{ background: var(--cs-card); color: var(--cs-text); border-color: var(--cs-border); }}
    div.stButton > button:hover, div[data-testid="stDownloadButton"] button:hover {{ background: var(--cs-hover); color: var(--cs-accent-strong); border-color: var(--cs-accent); }}
    div[data-testid="stTabs"], div[data-testid="stTabs"] [data-baseweb="tab-list"] {{ background: var(--cs-nav-background); border-bottom: 1px solid var(--cs-border); }}
    div[data-testid="stTabs"] [role="tab"], div[data-testid="stTabs"] [data-baseweb="tab"], div[data-testid="stTabs"] [role="tab"] *, div[data-testid="stTabs"] [data-baseweb="tab"] * {{ color: var(--cs-tab-inactive) !important; background: transparent !important; opacity: 1 !important; -webkit-text-fill-color: var(--cs-tab-inactive) !important; }}
    div[data-testid="stTabs"] [role="tab"]:hover, div[data-testid="stTabs"] [data-baseweb="tab"]:hover, div[data-testid="stTabs"] [role="tab"]:focus-visible, div[data-testid="stTabs"] [data-baseweb="tab"]:focus-visible, div[data-testid="stTabs"] [role="tab"]:hover *, div[data-testid="stTabs"] [data-baseweb="tab"]:hover *, div[data-testid="stTabs"] [role="tab"]:focus-visible *, div[data-testid="stTabs"] [data-baseweb="tab"]:focus-visible * {{ color: var(--cs-accent-strong) !important; background: var(--cs-hover) !important; opacity: 1 !important; -webkit-text-fill-color: var(--cs-accent-strong) !important; }}
    div[data-testid="stTabs"] [role="tab"][aria-selected="true"], div[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {{ color: var(--cs-accent) !important; border-bottom: 3px solid var(--cs-accent) !important; opacity: 1 !important; -webkit-text-fill-color: var(--cs-accent) !important; }}
    div[data-testid="stTabs"] [role="tab"][aria-selected="true"] *, div[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] * {{ color: var(--cs-accent) !important; opacity: 1 !important; -webkit-text-fill-color: var(--cs-accent) !important; }}
    div[data-testid="stDataFrame"] {{ border: 1px solid var(--cs-border); border-radius: .5rem; }}
    .cs-brand-header {{ display: flex; align-items: center; gap: .8rem; min-height: 4rem; white-space: nowrap; }}
    .cs-brand-header img {{ display: block; width: auto; height: clamp(2.75rem, 6vw, 4rem); object-fit: contain; }}
    .cs-dashboard-title {{ color: var(--cs-text); font-size: clamp(1.25rem, 3vw, 2.2rem); line-height: 1.1; font-weight: 700; margin: 0; }}
    </style>""", unsafe_allow_html=True)


def style_figure(fig: go.Figure, dark: bool | None = None) -> go.Figure:
    if dark is None:
        dark = bool(st.session_state.get("dark_mode", False))
    colors = theme_tokens(dark)
    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white",
        paper_bgcolor=colors["chart"], plot_bgcolor=colors["chart"],
        font_color=colors["text"], legend=dict(font=dict(color=colors["text"]), bgcolor=colors["chart"]),
        xaxis=dict(gridcolor=colors["grid"]), yaxis=dict(gridcolor=colors["grid"]),
    )
    return fig

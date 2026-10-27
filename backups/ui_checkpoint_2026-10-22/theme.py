"""Centralized dashboard theme tokens and Plotly styling."""
from __future__ import annotations

import base64
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st


def theme_tokens(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "background": "#0B1622", "sidebar": "#101D2B", "card": "#122333",
            "text": "#F1F5F8", "heading": "#F1F5F8", "muted": "#B5C2CC", "border": "#2A3C4B",
            "nav_background": "#101D2B", "tab_inactive": "#B5C2CC",
            "accent": "#F47C35", "accent_strong": "#FFB07C", "hover": "#192B3A",
            "chart": "#0B1622", "grid": "#2A3C4B",
        }
    return {
        "background": "#F7F8F6", "sidebar": "#eef3ec", "card": "#ffffff",
        "text": "#18332a", "heading": "#17212F", "muted": "#52645a", "border": "#d8e3d5",
        "nav_background": "#F7F8F6", "tab_inactive": "#666D75",
        "accent": "#FF4655", "accent_strong": "#c92f3d", "hover": "#f8e4df",
        "chart": "#ffffff", "grid": "#d8e3d5",
    }


def apply_theme(dark: bool, header_image_path: Path | None = None) -> None:
    colors = theme_tokens(dark)
    header_image = ""
    if header_image_path and header_image_path.exists():
        encoded = base64.b64encode(header_image_path.read_bytes()).decode("ascii")
        header_image = f"url(data:image/png;base64,{encoded})"
    header_background = f"linear-gradient(90deg, rgba(11,22,34,.98) 0%, rgba(11,22,34,.86) 54%, rgba(11,22,34,.55) 100%), {header_image}" if header_image else "linear-gradient(120deg, #0B1622 0%, #192B3A 100%)"
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
    section[data-testid="stSidebar"] {{ background: var(--cs-sidebar); border-right: 1px solid var(--cs-border); width: 232px; min-width: 232px; }}
    section[data-testid="stSidebar"] > div:first-child {{ width: 232px; }}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {{ color: var(--cs-text); }}
    section[data-testid="stSidebar"] div.stButton {{ margin: .12rem 0; }}
    section[data-testid="stSidebar"] div.stButton > button {{ justify-content: flex-start; text-align: left; min-height: 2.35rem; padding: .35rem .7rem; border-radius: .55rem; }}
    section[data-testid="stSidebar"] div.stButton > button p {{ text-align: left; width: 100%; }}
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"] {{ background: transparent; color: var(--cs-muted); border-color: transparent; }}
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"]:hover {{ background: var(--cs-hover); color: var(--cs-text); border-color: var(--cs-border); }}
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"] {{ background: rgba(244,124,53,.16); color: var(--cs-accent); border-color: rgba(244,124,53,.44); }}
    div[data-testid="stMetric"] {{ background: var(--cs-card); border: 1px solid var(--cs-border); padding: 1rem; border-radius: .75rem; }}
    div[data-testid="stMetric"] [data-testid="stMetricLabel"], div[data-testid="stMetric"] [data-testid="stMetricValue"], div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{ color: var(--cs-text) !important; }}
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ font-size: 1.6rem; }}
    div[data-baseweb="select"] > div {{ background: var(--cs-card); color: var(--cs-text); border-color: var(--cs-border); }}
    div[data-baseweb="select"] span, div[data-baseweb="select"] input {{ color: var(--cs-text) !important; }}
    div[data-testid="stFileUploader"] {{ background: var(--cs-card); border: 1px solid #d97706; border-radius: .8rem; padding: .35rem; }}
    div[data-testid="stFileUploader"] section {{ border-color: #d97706 !important; background: transparent !important; }}
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
    div.st-key-hero_header {{
        background-image: {header_background}; background-position: center; background-size: cover;
        border: 1px solid var(--cs-border); border-radius: 1rem; padding: 1rem 1.15rem .8rem; margin-bottom: .85rem;
    }}
    .cs-kpi-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .7rem; margin: .7rem 0 .8rem; }}
    .cs-kpi-card {{ background: var(--cs-card); border: 1px solid var(--cs-border); border-radius: .7rem; padding: .75rem .9rem; min-height: 6.3rem; }}
    .cs-kpi-icon {{ font-size: 1.1rem; color: #3c9672; line-height: 1; margin-bottom: .45rem; }}
    .cs-kpi-card.demand .cs-kpi-icon {{ color: #f47c35; }}
    .cs-kpi-card.balance .cs-kpi-icon {{ color: #f4b183; }}
    .cs-kpi-label {{ color: var(--cs-muted); font-size: .86rem; }}
    .cs-kpi-value {{ color: var(--cs-heading); font-size: 1.55rem; font-weight: 700; margin-top: .25rem; }}
    .cs-kpi-support {{ color: var(--cs-muted); font-size: .76rem; margin-top: .25rem; }}
    @media (max-width: 760px) {{ .cs-kpi-grid {{ grid-template-columns: 1fr; }} }}
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

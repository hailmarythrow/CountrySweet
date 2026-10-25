"""Centralized dashboard theme tokens and Plotly styling."""
from __future__ import annotations

import base64
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st


def theme_tokens(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "background": "#07111B", "sidebar": "#0D1B2A", "card": "#172B3D",
            "panel": "#122537", "input": "#203348", "text": "#F5F7FA", "heading": "#F5F7FA", "muted": "#B9C7D5", "border": "#34495D",
            "nav_background": "#0D1B2A", "tab_inactive": "#B9C7D5",
            "accent": "#F28C45", "accent_strong": "#FFB27C", "hover": "#1B3044",
            "chart": "#122537", "grid": "#34495D",
        }
    return {
        "background": "#F7F8F6", "sidebar": "#eef3ec", "card": "#ffffff",
        "panel": "#ffffff", "input": "#ffffff",
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
    header_background = f"linear-gradient(90deg, rgba(5,15,26,.97) 0%, rgba(5,15,26,.86) 42%, rgba(5,15,26,.58) 72%, rgba(5,15,26,.42) 100%), {header_image}" if header_image else "linear-gradient(120deg, #0B1622 0%, #192B3A 100%)"
    st.markdown(f"""<style>
    :root {{
        --cs-background: {colors['background']}; --cs-sidebar: {colors['sidebar']};
        --cs-card: {colors['card']}; --cs-text: {colors['text']}; --cs-muted: {colors['muted']};
        --cs-panel: {colors['panel']}; --cs-input: {colors['input']};
        --cs-heading: {colors['heading']}; --cs-border: {colors['border']};
        --cs-nav-background: {colors['nav_background']}; --cs-tab-inactive: {colors['tab_inactive']};
        --cs-accent: {colors['accent']};
        --cs-accent-strong: {colors['accent_strong']}; --cs-hover: {colors['hover']};
    }}
    [data-testid="stAppViewContainer"] {{ background: var(--cs-background); color: var(--cs-text); }}
    /* Keep the first content block below Streamlit's toolbar.  The explicit
       container selector avoids relying on a negative offset or a fixed hero
       height, both of which can crop the rounded header at the top of a page. */
    #stMainBlockContainer, [data-testid="stMainBlockContainer"] {{
        width: 100% !important; max-width: none !important; box-sizing: border-box;
        padding-top: 2rem !important; padding-right: 1.25rem !important;
        padding-bottom: 1.1rem !important; padding-left: 1.25rem !important;
    }}
    [data-testid="stHeader"] {{ background: var(--cs-background); }}
    [data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3 {{ color: var(--cs-heading) !important; }}
    section[data-testid="stSidebar"] {{ background: var(--cs-sidebar); border-right: 1px solid var(--cs-border); width: 245px !important; min-width: 245px !important; max-width: 245px !important; flex: 0 0 245px !important; }}
    section[data-testid="stSidebar"][data-collapsed="true"] {{ width: 0 !important; min-width: 0 !important; max-width: 0 !important; flex: 0 0 0 !important; }}
    /* Let the sidebar content fill the fixed 245px rail while retaining its
       built-in horizontal inset for branding and controls. */
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ width: 100% !important; max-width: none !important; min-width: 0 !important; }}
    section[data-testid="stSidebar"] > div:first-child {{ width: 100% !important; max-width: none !important; min-width: 0 !important; padding-top: 0 !important; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"], section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{ padding-top: 0 !important; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{ padding-bottom: 16px !important; overflow-y: auto; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > div[data-testid="stVerticalBlock"] {{ min-height: calc(100vh - 26px); display: flex; flex-direction: column; }}
    section[data-testid="stSidebar"] > div:first-child > div, section[data-testid="stSidebar"] > div:first-child > div > div {{ padding-top: 0 !important; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {{ height: 0 !important; min-height: 0 !important; margin-bottom: 9px !important; padding: 0 !important; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {{ position: relative; top: 30px; z-index: 3; visibility: visible !important; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button {{ visibility: visible !important; }}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {{ color: var(--cs-text); }}
    div.st-key-sidebar_branding {{ width: 100% !important; max-width: none !important; display: flex !important; flex-direction: column !important; align-items: center !important; justify-content: center !important; gap: 0 !important; text-align: center !important; box-sizing: border-box; margin: 0 !important; padding: .1rem 0 .35rem; transform: none !important; }}
    div.st-key-sidebar_branding > div {{ margin: 0 !important; padding: 0 !important; }}
    div.st-key-sidebar_branding [data-testid="stImage"] {{ display: flex; align-items: center; justify-content: center; width: 100%; margin: 0 0 .25rem !important; padding: 0 !important; line-height: 0 !important; }}
    div.st-key-sidebar_branding [data-testid="stImage"] > div {{ margin: 0 !important; padding: 0 !important; line-height: 0 !important; }}
    div.st-key-sidebar_branding [data-testid="stImage"] img {{ display: block; width: 135px !important; height: auto !important; object-fit: contain; margin: 0 auto; }}
    div.st-key-sidebar_branding [data-testid="stMarkdownContainer"] {{ text-align: center; margin: 0 !important; padding: 0 !important; }}
    div.st-key-sidebar_branding [data-testid="stMarkdownContainer"] p {{ margin: 0 !important; padding: 0 !important; }}
    .cs-sidebar-company {{ color: var(--cs-text); font-size: 18px; font-weight: 600; line-height: 1.2; margin: 0; }}
    .cs-sidebar-tagline {{ color: var(--cs-muted); font-size: 12.5px; font-weight: 400; line-height: 1.2; margin: .15rem 0 0; }}
    /* Keep the existing compact navigation column while the branding group
       uses the full visible sidebar width above it. */
    div.st-key-sidebar_navigation {{ width: 100%; max-width: none; }}
    section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(> div.st-key-sidebar_settings) {{ margin-top: auto !important; flex: 0 0 auto !important; }}
    div.st-key-sidebar_settings {{ width: 100%; max-width: none; flex: 0 0 auto !important; padding-bottom: 0; }}
    div.st-key-sidebar_settings hr {{ margin: 0 0 12px !important; }}
    section[data-testid="stSidebar"] div.stButton {{ margin: .04rem 0; }}
    section[data-testid="stSidebar"] div.stButton > button {{ justify-content: flex-start; text-align: left; min-height: 2.1rem; padding: .25rem .35rem; border-radius: .5rem; }}
    section[data-testid="stSidebar"] div.stButton > button p {{ text-align: left; width: 100%; }}
    section[data-testid="stSidebar"] div.st-key-sidebar_navigation div.stButton > button > div,
    section[data-testid="stSidebar"] div.st-key-sidebar_navigation div.stButton > button > div > span,
    section[data-testid="stSidebar"] div.st-key-sidebar_navigation div.stButton > button [data-testid="stMarkdownContainer"],
    section[data-testid="stSidebar"] div.st-key-sidebar_settings div.stButton > button > div,
    section[data-testid="stSidebar"] div.st-key-sidebar_settings div.stButton > button > div > span,
    section[data-testid="stSidebar"] div.st-key-sidebar_settings div.stButton > button [data-testid="stMarkdownContainer"] {{ width: 100% !important; }}
    section[data-testid="stSidebar"] div.st-key-sidebar_navigation div.stButton > button [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] div.st-key-sidebar_settings div.stButton > button [data-testid="stMarkdownContainer"] p {{ display: flex; align-items: center; gap: 8px; width: 100% !important; margin: 0; padding: 0; font-size: .875rem; line-height: 1.2; }}
    section[data-testid="stSidebar"] div.st-key-sidebar_navigation div.stButton > button [data-testid="stMarkdownContainer"] p > span[role="img"],
    section[data-testid="stSidebar"] div.st-key-sidebar_settings div.stButton > button [data-testid="stMarkdownContainer"] p > span[role="img"] {{ display: inline-flex !important; align-items: center; justify-content: center; width: 24px; height: 24px; flex: 0 0 24px; font-size: 24px !important; line-height: 24px; vertical-align: middle; font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24; }}
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"] {{ background: transparent; color: var(--cs-muted); border-color: transparent; }}
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"]:hover {{ background: var(--cs-hover); color: var(--cs-text); border-color: var(--cs-border); }}
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"] {{ background: rgba(244,124,53,.16); color: var(--cs-accent); border-color: rgba(244,124,53,.44); }}
    div[data-testid="stMetric"] {{ background: var(--cs-card); border: 1px solid var(--cs-border); padding: .65rem .8rem; border-radius: .65rem; }}
    div[data-testid="stMetric"] [data-testid="stMetricLabel"], div[data-testid="stMetric"] [data-testid="stMetricValue"], div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{ color: var(--cs-text) !important; }}
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {{ font-size: 1.45rem; }}
    div[data-baseweb="select"] > div {{ background: var(--cs-input); color: var(--cs-text); border-color: var(--cs-border); }}
    div[data-baseweb="select"] span, div[data-baseweb="select"] input {{ color: var(--cs-text) !important; }}
    .cs-inline-icon {{ display: inline-block; vertical-align: middle; flex: 0 0 auto; }}
    div.st-key-upload_panel {{ width: 100%; box-sizing: border-box; background: rgba(9,23,36,.90); border: 1px solid rgba(255,255,255,.13); border-radius: 12px; padding: 16px 18px 17px; margin: .05rem 0 0; row-gap: 0 !important; }}
    div.st-key-upload_panel > div {{ width: 100%; }}
    .cs-upload-intro {{ display: flex; align-items: center; gap: 16px; min-width: 0; margin: 0 0 26px; }}
    .cs-upload-icon-wrap {{ display: flex; align-items: center; justify-content: center; flex: 0 0 58px; width: 58px; height: 64px; }}
    .cs-excel-upload-icon {{ display: block; width: 58px; height: 64px; }}
    .cs-upload-copy {{ min-width: 0; display: flex; flex-direction: column; gap: 0; }}
    .cs-upload-heading {{ color: var(--cs-heading); font-size: 17px; font-weight: 700; line-height: 1.2; margin: 0 0 4px; }}
    .cs-upload-subtitle {{ color: var(--cs-text); font-size: 13px; font-weight: 400; line-height: 1.3; margin: 0 0 3px; }}
    .cs-upload-support {{ color: var(--cs-muted); font-size: 12px; font-weight: 400; line-height: 1.25; margin: 0; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] {{ background: transparent; border: 0; padding: 0; margin: 0; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] section, div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] {{ border: 0 !important; background: transparent !important; padding: 0 !important; min-height: 0 !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] {{ display: flex; flex-direction: column; gap: 0; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzoneInstructions"] {{ display: none !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] > span {{ display: block !important; width: 100% !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button {{ width: 100% !important; min-height: 49px !important; background: var(--cs-accent) !important; color: #091522 !important; border: 0 !important; border-radius: 9px !important; font-weight: 700 !important; transition: background-color .15s ease; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button:hover, div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button:focus-visible {{ background: #FF9455 !important; color: #091522 !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button > div {{ justify-content: center !important; width: 100% !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button [data-testid="stIconMaterial"] {{ color: #091522 !important; font-size: 20px !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button p {{ color: transparent !important; font-size: 0 !important; line-height: 1.2; }}
    div.st-key-upload_panel [data-testid="stFileUploaderDropzone"] button p::after {{ content: "Choose Excel File"; color: #091522; font-size: 1rem; font-weight: 700; }}
    .cs-upload-state {{ display: none !important; }}
    div.st-key-upload_panel:has(.cs-upload-state--replace) [data-testid="stFileUploaderDropzone"] button p::after {{ content: "Replace Excel File"; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] button[aria-label*="Help"] {{ width: auto; min-height: 1rem; background: transparent !important; color: var(--cs-muted) !important; border: 0 !important; padding: 0; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] button[aria-label*="Help"] p {{ color: var(--cs-muted) !important; font-size: 0 !important; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] button[aria-label*="Help"] p::after {{ content: "?"; color: var(--cs-muted); font-size: .78rem; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] small {{ display: none !important; }}
    div.st-key-upload_panel div[data-testid="stFileUploader"] label {{ display: none !important; }}
    div.st-key-upload_panel [data-testid="stFileUploaderFileData"] {{ color: var(--cs-text); }}
    div.st-key-upload_panel [data-testid="stCaptionContainer"] {{ margin: .55rem 0 0; color: var(--cs-muted); font-size: .72rem; line-height: 1.25; }}
    div.st-key-filters_panel {{ background: var(--cs-panel); border: 1px solid var(--cs-border); border-radius: .7rem; padding: .65rem .75rem .35rem; margin: .55rem 0 .7rem; }}
    div.st-key-filters_panel h4 {{ margin: 0 0 .08rem; }}
    div.st-key-filters_panel [data-testid="stCaptionContainer"] {{ margin: 0 0 .15rem; line-height: 1.2; }}
    div.st-key-filters_panel [data-testid="stWidgetLabel"] {{ margin-bottom: .12rem; }}
    div.st-key-filters_panel div[data-baseweb="select"] > div {{ min-height: 2rem; }}
    div.st-key-filters_panel [data-testid="stHorizontalBlock"] {{ gap: .55rem; }}
    div.st-key-inventory_panel {{ background: var(--cs-panel); border: 1px solid var(--cs-border); border-radius: .7rem; padding: .55rem .65rem .25rem; min-height: 23rem; }}
    div.st-key-mix_panel {{ background: var(--cs-panel); border: 1px solid var(--cs-border); border-radius: .7rem; padding: .55rem .65rem 25.8px; min-height: 0; }}
    div.st-key-inventory_panel h4 {{ margin: 0 0 .2rem; }}
    div.st-key-mix_panel h4 {{ margin: 0 0 .25rem; font-size: 21px; font-weight: 700; }}
    div.st-key-mix_panel .cs-mix-subtitle {{ color: var(--cs-muted); font-size: 13px; font-weight: 400; line-height: 1.25; margin: 0 0 .25rem; }}
    div.st-key-mix_panel [data-testid="stHorizontalBlock"] {{ align-items: center !important; gap: .45rem; margin-top: 12px; }}
    div.st-key-mix_panel [data-testid="stPlotlyChart"] {{ margin: 0; }}
    div.st-key-mix_panel .cs-mix-legend {{ display: flex; flex-direction: column; justify-content: center; gap: 20px; min-height: 210px; padding: 0 .15rem; }}
    div.st-key-mix_panel .cs-mix-legend-item {{ display: grid; grid-template-columns: 18px minmax(0, 1fr); column-gap: 9px; align-items: start; }}
    div.st-key-mix_panel .cs-mix-dot {{ width: 16px; height: 16px; border-radius: 50%; margin-top: 3px; }}
    div.st-key-mix_panel .cs-mix-dot.organic {{ background: #3c9672; }}
    div.st-key-mix_panel .cs-mix-dot.conventional {{ background: #f47c35; }}
    div.st-key-mix_panel .cs-mix-legend-label {{ color: var(--cs-text); font-size: 15px; font-weight: 500; line-height: 1.15; }}
    div.st-key-mix_panel .cs-mix-legend-value {{ color: #FFFFFF; font-size: 19px; font-weight: 700; line-height: 1.2; margin-top: 3px; }}
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
    div.st-key-hero_header h3 {{ color: var(--cs-heading) !important; font-size: clamp(1.7rem, 3vw, 2.35rem); margin: .05rem 0 .2rem; }}
    div.st-key-hero_header [data-testid="stCaptionContainer"] {{ color: var(--cs-muted); margin: 0; line-height: 1.25; }}
    div.st-key-header_copy {{ row-gap: 12px !important; justify-content: flex-start !important; }}
    div.st-key-header_copy [data-testid="stCaptionContainer"] {{ margin: 0 !important; }}
    div.st-key-header_copy h3 {{ margin: 0 0 14px !important; }}
    div.st-key-header_copy [data-testid="stMarkdownContainer"] p {{ margin: 0 !important; }}
    div.st-key-hero_header {{
        position: relative; isolation: isolate; background: #07111B;
        border: none; outline: none; box-shadow: none; border-radius: 0; padding: .55rem .85rem .45rem; margin-bottom: .65rem;
    }}
    div.st-key-hero_header::before {{
        content: ""; position: absolute; inset: 0; z-index: 0; pointer-events: none;
        background-image:
            linear-gradient(to right, #07111B 0, rgba(7,17,27,0) 38px, rgba(7,17,27,0) calc(100% - 30px), #07111B 100%),
            {header_background};
        background-position: center, center;
        background-size: 100% 100%, cover;
        background-repeat: no-repeat;
        -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 36%, #000 82%, transparent 100%);
        mask-image: linear-gradient(to bottom, transparent 0%, #000 36%, #000 82%, transparent 100%);
        -webkit-mask-size: 100% 100%; mask-size: 100% 100%;
        -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
    }}
    div.st-key-hero_header > div {{ position: relative; z-index: 1; }}
    div.st-key-hero_header [data-testid="stHorizontalBlock"] {{ gap: .8rem; }}
    div.st-key-hero_header [data-testid="stColumn"] {{ padding-top: .15rem; }}
    div.st-key-hero_header [data-testid="stMetric"] {{ padding: .35rem .5rem; margin: 0; }}
    div.st-key-header_right_stack {{ width: 100% !important; max-width: none !important; }}
    div.st-key-hero_header div.st-key-header_right_stack .cs-weather-widget {{ max-width: 270px; margin-left: auto; margin-right: 0; margin-bottom: 5px; }}
    div.st-key-hero_header div.st-key-header_right_stack div.st-key-upload_panel {{ width: 100%; max-width: 500px; margin-left: auto; }}
    div.st-key-hero_header .cs-weather-widget {{
        width: fit-content; max-width: 270px; min-height: 88px; box-sizing: border-box;
        display: flex; align-items: center; gap: 15px; padding: 14px 11px 14px 16px;
        background: linear-gradient(110deg, rgba(7,17,27,.94) 0%, rgba(12,24,36,.84) 50%, rgba(25,32,39,.68) 100%);
        backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
        border: 1px solid rgba(255,255,255,.07);
        border-radius: 14px; color: var(--cs-text);
    }}
    div.st-key-hero_header .cs-weather-icon {{
        display: flex; align-items: center; justify-content: center;
        width: 42px; min-width: 42px; height: 42px; color: #D9E3ED;
    }}
    div.st-key-hero_header .cs-weather-svg {{ display: block; width: 42px; height: 42px; fill: currentColor; stroke: none; }}
    div.st-key-hero_header .cs-weather-sun-fill {{ fill: #F9BF59; }}
    div.st-key-hero_header .cs-weather-sun-ray {{ fill: none; stroke: #F9BF59; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }}
    div.st-key-hero_header .cs-weather-moon-fill {{ fill: #D7E3F1; }}
    div.st-key-hero_header .cs-weather-cloud-fill {{ fill: #F5F7FA; }}
    div.st-key-hero_header .cs-weather-rain-fill {{ fill: #A9D5F5; }}
    div.st-key-hero_header .cs-weather-snow-fill {{ fill: #D8ECFA; }}
    div.st-key-hero_header .cs-weather-lightning-fill {{ fill: #F3C56B; }}
    div.st-key-hero_header .cs-weather-fog-fill {{ fill: #B9C7D5; }}
    div.st-key-hero_header .cs-weather-sun {{ color: #F6C85F; }}
    div.st-key-hero_header .cs-weather-moon {{ color: #D7E3F1; }}
    div.st-key-hero_header .cs-weather-cloud, div.st-key-hero_header .cs-weather-cloud_sun,
    div.st-key-hero_header .cs-weather-cloud_moon {{ color: #D9E3ED; }}
    div.st-key-hero_header .cs-weather-rain {{ color: #A9D5F5; }}
    div.st-key-hero_header .cs-weather-snow {{ color: #D8ECFA; }}
    div.st-key-hero_header .cs-weather-thunder {{ color: #F3C56B; }}
    div.st-key-hero_header .cs-weather-fog, div.st-key-hero_header .cs-weather-widget--unavailable {{ color: #B9C7D5; }}
    div.st-key-hero_header .cs-weather-info {{ min-width: 0; display: flex; flex-direction: column; gap: 2px; }}
    div.st-key-hero_header .cs-weather-location {{ color: var(--cs-text); font-size: 13px; font-weight: 500; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    div.st-key-hero_header .cs-weather-temperature {{ color: #FFFFFF; font-size: 24px; font-weight: 600; line-height: 1.05; }}
    div.st-key-hero_header .cs-weather-condition {{ color: var(--cs-muted); font-size: 12px; font-weight: 400; line-height: 1.2; }}
    div.st-key-hero_header .cs-weather-widget--unavailable .cs-weather-condition {{ color: var(--cs-muted); }}
    div.st-key-hero_header [data-testid="stMarkdownContainer"] p {{ margin: .08rem 0; line-height: 1.25; }}
    .cs-kpi-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem; margin: .65rem 0 .5rem; }}
    .cs-kpi-card {{ background: var(--cs-card); border: 1px solid var(--cs-border); border-radius: .65rem; padding: .7rem .85rem; height: 7.5rem; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; }}
    .cs-kpi-icon {{ color: #3c9672; line-height: 1; margin-bottom: .45rem; }}
    .cs-kpi-icon .cs-inline-icon {{ width: 1.45rem; height: 1.45rem; }}
    .cs-kpi-card.demand .cs-kpi-icon {{ color: #f47c35; }}
    .cs-kpi-card.balance .cs-kpi-icon {{ color: #f4b183; }}
    .cs-kpi-label {{ color: var(--cs-muted); font-size: .86rem; }}
    .cs-kpi-value {{ color: var(--cs-heading); font-size: 1.5rem; font-weight: 700; margin-top: .2rem; }}
    .cs-kpi-support {{ color: var(--cs-muted); font-size: .74rem; margin-top: auto; line-height: 1.15; }}
    div[data-testid="stAlert"] {{ padding: .5rem .7rem; margin: .35rem 0; }}
    div[data-testid="stExpander"] {{ margin: .25rem 0; }}
    @media (max-width: 760px) {{
        #stMainBlockContainer, [data-testid="stMainBlockContainer"] {{ padding-left: .75rem !important; padding-right: .75rem !important; }}
        .cs-kpi-grid {{ grid-template-columns: 1fr; }}
    }}
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

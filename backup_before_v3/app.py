"""Country Sweet Produce inventory visualization dashboard."""
from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from utils.excel_loader import load_inventory_workbook

LOGO_PATH = Path(__file__).parent / "assets" / "country_sweet_logo.png"
LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")

st.set_page_config(page_title="Country Sweet | Inventory Dashboard", page_icon=str(LOGO_PATH), layout="wide")

REQUIRED = ["product", "variety", "classification", "size", "stored_lbs", "forecast_demand_lbs"]
DEMO_ROWS = [
    ["Conventional Orange USDA No. 1", "Orange", "Conventional", "USDA No. 1", 55000, 41000],
    ["Organic Orange USDA No. 1", "Orange", "Organic", "USDA No. 1", 30000, 18500],
    ["Conventional Purple Small", "Purple", "Conventional", "Small", 21000, 24000],
    ["Organic Purple Small", "Purple", "Organic", "Small", 14000, 8000],
    ["Organic White Medium", "White", "Organic", "Medium", 9000, 6500],
]
DEFAULT_LOCATION = {
    "name": "Bakersfield",
    "admin1": "California",
    "country_code": "US",
    "latitude": 35.3733,
    "longitude": -119.0187,
}
WEATHER_CODES = {
    0: ("Clear sky", "☀️"), 1: ("Mainly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Fog", "🌫️"), 48: ("Rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Heavy drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "❄️"), 75: ("Heavy snow", "❄️"),
    80: ("Rain showers", "🌦️"), 81: ("Rain showers", "🌧️"), 82: ("Heavy showers", "⛈️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm", "⛈️"), 99: ("Thunderstorm", "⛈️"),
}


def get_welcome(timezone: str = "America/Los_Angeles") -> tuple[str, str, str]:
    now = datetime.now(ZoneInfo(timezone))
    greeting = "Good Morning" if now.hour < 12 else "Good Afternoon" if now.hour < 17 else "Good Evening"
    return greeting, now.strftime("%A, %B %d, %Y"), now.strftime("%I:%M:%S %p %Z")


def demo_data() -> pd.DataFrame:
    return pd.DataFrame(DEMO_ROWS, columns=REQUIRED)


def excel_template() -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        demo_data().to_excel(writer, index=False, sheet_name="inventory")
    return bio.getvalue()


def clean_data(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    extras = [c for c in ["year", "week"] if c in df.columns]
    df = df[REQUIRED + extras].dropna(how="all")
    for field in ["product", "variety", "classification", "size"]:
        df[field] = df[field].fillna("Unknown").astype(str).str.strip()
    df["classification"] = df["classification"].str.title()
    df["stored_lbs"] = pd.to_numeric(df["stored_lbs"], errors="coerce")
    df["forecast_demand_lbs"] = pd.to_numeric(df["forecast_demand_lbs"], errors="coerce")
    if df["stored_lbs"].isna().any():
        raise ValueError("Stored inventory quantities must be numbers; unavailable values were not replaced with zero.")
    if (df[["stored_lbs", "forecast_demand_lbs"]].fillna(0) < 0).any().any():
        raise ValueError("Inventory quantities cannot be negative.")
    if df.empty:
        raise ValueError("The file has no data rows.")
    return df


@st.cache_data(ttl=900, show_spinner=False)
def search_us_locations(query: str) -> tuple[list[dict], str | None]:
    query = query.strip()
    if not query:
        return [], None
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={
                "name": query,
                "count": 10,
                "language": "en",
                "format": "json",
                "countryCode": "US",
            },
            timeout=8,
        )
        response.raise_for_status()
        raw_results = response.json().get("results") or []
    except (requests.RequestException, ValueError):
        return [], "Location search is temporarily unavailable."

    locations = []
    seen = set()
    for result in raw_results:
        if str(result.get("country_code", "")).upper() != "US":
            continue
        location = {
            "name": str(result.get("name") or "Unknown city"),
            "admin1": str(result.get("admin1") or ""),
            "country_code": "US",
            "latitude": result.get("latitude"),
            "longitude": result.get("longitude"),
        }
        if location["latitude"] is None or location["longitude"] is None:
            continue
        key = (location["name"].casefold(), location["admin1"].casefold(), round(float(location["latitude"]), 4), round(float(location["longitude"]), 4))
        if key in seen:
            continue
        seen.add(key)
        locations.append(location)
    return locations, None


def location_label(location: dict) -> str:
    return f"{location['name']}, {location['admin1']}" if location.get("admin1") else location["name"]


@st.cache_data(ttl=900, show_spinner=False)
def get_weather(latitude: float, longitude: float) -> dict:
    response = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": latitude, "longitude": longitude, "current": "temperature_2m,weather_code", "temperature_unit": "fahrenheit", "timezone": "auto"}, timeout=8)
    response.raise_for_status()
    current = response.json()["current"]
    condition, icon = WEATHER_CODES.get(int(current["weather_code"]), ("Current conditions", "🌡️"))
    return {"temperature": current["temperature_2m"], "condition": condition, "icon": icon}


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


def style_figure(fig):
    colors = theme_tokens(st.session_state.dark_mode)
    fig.update_layout(
        template="plotly_dark" if st.session_state.dark_mode else "plotly_white",
        paper_bgcolor=colors["chart"], plot_bgcolor=colors["chart"],
        font_color=colors["text"], legend=dict(font=dict(color=colors["text"]), bgcolor=colors["chart"]),
        xaxis=dict(gridcolor=colors["grid"]), yaxis=dict(gridcolor=colors["grid"]),
    )
    return fig


if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
if "timezone" not in st.session_state:
    st.session_state.timezone = "America/Los_Angeles"

top_left, top_right = st.columns([5, 1])
with top_left:
    st.markdown(f"<div class='cs-brand-header'><img src='{LOGO_DATA_URI}' alt='Country Sweet Produce logo'><span class='cs-dashboard-title'>Inventory Dashboard</span></div>", unsafe_allow_html=True)
with top_right:
    st.session_state.dark_mode = st.toggle("Dark mode", value=st.session_state.dark_mode)
apply_theme(st.session_state.dark_mode)

with st.sidebar:
    st.header("Dashboard settings")
    tz_options = ["America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "UTC"]
    st.session_state.timezone = st.selectbox("Welcome time zone", tz_options, index=tz_options.index(st.session_state.timezone))
    st.divider()
    st.header("Data source")
    st.caption("Upload the primary Forecasted Inventory Levels worksheet or use the included demo data.")
    uploaded = st.file_uploader("Inventory workbook", type=["xlsx"])
    st.download_button("Download sample Excel template", data=excel_template(), file_name="inventory_template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")

if uploaded is None:
    df = demo_data()
    source_name, source_sheet, source_status = "Demonstration data", "demo_data", "Demonstration data"
    workbook_report = None
else:
    workbook_report = load_inventory_workbook(uploaded.getvalue())
    source_name = uploaded.name
    with st.sidebar:
        sheet_options = workbook_report.available_sheets
        selected_sheet = st.selectbox("Worksheet", sheet_options, index=sheet_options.index(workbook_report.preferred_sheet) if workbook_report.preferred_sheet in sheet_options else 0)
    try:
        df = clean_data(workbook_report.frames[selected_sheet])
        source_sheet, source_status = selected_sheet, "Forecast / workbook data"
    except Exception as exc:
        st.error(f"Could not import worksheet '{selected_sheet}': {exc}")
        st.stop()

with st.sidebar:
    st.header("Filters")
    classes = sorted(df["classification"].unique().tolist())
    class_filter = st.multiselect("Classification", classes, default=classes)
    varieties = sorted(df["variety"].unique().tolist())
    variety_filter = st.multiselect("Variety", varieties, default=varieties)
    sizes = sorted(df["size"].unique().tolist())
    size_filter = st.multiselect("Size", sizes, default=sizes)
    if st.button("Reset filters", width="stretch"):
        st.rerun()

filtered = df[df["classification"].isin(class_filter) & df["variety"].isin(variety_filter) & df["size"].isin(size_filter)].copy()


def apply_weather_location(location: dict) -> None:
    st.session_state.weather_location = location
    st.session_state.weather_location_search = ""
    st.session_state.weather_location_popover = False
    st.rerun()


@st.fragment(run_every="1s")
def render_header() -> None:
    greeting, date, clock = get_welcome(st.session_state.timezone)
    if "weather_location" not in st.session_state:
        st.session_state.weather_location = DEFAULT_LOCATION.copy()
    selected_location = st.session_state.weather_location
    hcol, wcol = st.columns([3, 1])
    with hcol:
        st.subheader(greeting)
        st.caption(f"{date} • {clock}")
        st.write("A clear view of stored inventory, demand coverage, and product mix.")
    with wcol:
        st.markdown("**Current weather**")
        location_col, edit_col = st.columns([5, 1], gap="small", vertical_alignment="center")
        with location_col:
            st.caption(location_label(selected_location))
        with edit_col:
            location_popover = st.popover(
                " ",
                type="tertiary",
                icon=":material/edit_location:",
                help="Change weather location",
                key="weather_location_popover",
                on_change="rerun",
            )
            with location_popover:
                if location_popover.open:
                    st.caption("Search for a U.S. city or state")
                    search_query = st.text_input(
                        "Search U.S. city",
                        placeholder="City or state",
                        label_visibility="collapsed",
                        key="weather_location_search",
                    )
                    if search_query.strip():
                        search_results, search_error = search_us_locations(search_query)
                        if search_error:
                            st.caption(search_error)
                        elif search_results:
                            labels = [location_label(location) for location in search_results]
                            selected_label = st.selectbox("Matching U.S. cities", labels, key="weather_location_result")
                            location_to_apply = search_results[labels.index(selected_label)]
                            st.button(
                                "Use this location",
                                key="weather_location_apply",
                                on_click=apply_weather_location,
                                args=(location_to_apply,),
                            )
                        else:
                            st.caption(f"No U.S. cities found for “{search_query.strip()}”.")
        try:
            weather = get_weather(selected_location["latitude"], selected_location["longitude"])
            st.metric(location_label(selected_location), f"{weather['temperature']:.0f} °F")
            st.caption(f"{weather['icon']} {weather['condition']}")
        except Exception:
            st.caption("Weather is unavailable right now. Inventory data is still available.")


render_header()
st.caption(f"Source: **{source_name}** · Worksheet: **{source_sheet}** · Refreshed: **{datetime.now().strftime('%b %d, %Y %I:%M %p')} · {source_status}**")

if workbook_report:
    for message in workbook_report.warnings:
        st.warning(message)
    with st.expander("Workbook import details"):
        st.write(f"Available worksheets: {', '.join(workbook_report.available_sheets)}")
        for sheet, status in workbook_report.sheet_status.items():
            st.write(f"**{sheet}** — {status}")

if filtered.empty:
    st.warning("No rows match the current filters. Change your selections to continue.", icon="⚠️")
    st.stop()

stored = float(filtered["stored_lbs"].sum())
demand_value = filtered["forecast_demand_lbs"].sum(min_count=1)
demand = float(demand_value) if pd.notna(demand_value) else None
balance = stored - demand if demand is not None else None
k1, k2, k3 = st.columns(3)
k1.metric("Inventory in storage", f"{stored:,.0f} lbs")
k2.metric("Forecast demand", f"{demand:,.0f} lbs" if demand is not None else "Unavailable")
k3.metric("Net balance", f"{balance:+,.0f} lbs" if balance is not None else "Unavailable")
st.caption(f"Showing {len(filtered):,} visible inventory records")

overview, trends, organic, breakdown, explorer = st.tabs(["Overview", "Inventory trends", "Organic vs. conventional", "Product breakdown", "Data explorer"])

with overview:
    st.subheader("Inventory overview")
    summary = filtered.groupby(["product", "variety", "classification", "size"], as_index=False)[["stored_lbs", "forecast_demand_lbs"]].sum(min_count=1)
    summary["balance_lbs"] = summary["stored_lbs"] - summary["forecast_demand_lbs"]
    chart_data = summary.melt(id_vars=["product", "classification"], value_vars=["stored_lbs", "forecast_demand_lbs"], var_name="measure", value_name="pounds").dropna(subset=["pounds"])
    chart_data["measure"] = chart_data["measure"].map({"stored_lbs": "In storage", "forecast_demand_lbs": "Forecast demand"})
    fig = px.bar(chart_data, x="pounds", y="product", color="measure", orientation="h", barmode="group", labels={"product": "Product", "pounds": "Pounds", "measure": ""}, color_discrete_map={"In storage": "#2f855a", "Forecast demand": "#e07a3f"})
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        margin=dict(l=180, r=30, t=100, b=70),
        height=max(420, chart_data["product"].nunique() * 72 + 120),
    )
    fig.update_yaxes(automargin=True)
    fig.update_xaxes(automargin=True)
    st.plotly_chart(style_figure(fig), width="stretch", theme=None, alt="Horizontal comparison of stored inventory and supplied forecast demand by product")
    st.download_button("Download filtered summary (CSV)", data=summary.to_csv(index=False).encode("utf-8"), file_name="inventory_summary.csv", mime="text/csv")

with trends:
    st.subheader("Weekly inventory trends")
    if {"year", "week"}.issubset(filtered.columns) and filtered["year"].notna().any() and filtered["week"].notna().any():
        trend = filtered.groupby(["year", "week"], as_index=False)["stored_lbs"].sum().sort_values(["year", "week"])
        trend["period"] = trend["year"].astype(str) + " W" + trend["week"].astype(str).str.zfill(2)
        trend_fig = px.line(trend, x="period", y="stored_lbs", markers=True, labels={"period": "Reporting week", "stored_lbs": "Stored pounds"})
        st.plotly_chart(style_figure(trend_fig), width="stretch", theme=None, alt="Weekly stored inventory trend")
    else:
        st.info("Weekly trend data will appear when the uploaded worksheet includes year and week fields.")

with organic:
    st.subheader("Organic versus conventional inventory")
    class_summary = filtered.groupby("classification", as_index=False)["stored_lbs"].sum()
    class_fig = px.bar(class_summary, x="classification", y="stored_lbs", color="classification", labels={"stored_lbs": "Stored pounds", "classification": "Classification"}, color_discrete_map={"Organic": "#2f855a", "Conventional": "#e07a3f"})
    st.plotly_chart(style_figure(class_fig), width="stretch", theme=None, alt="Stored inventory by organic and conventional classification")

with breakdown:
    st.subheader("Product breakdown")
    left, right = st.columns(2)
    with left:
        variety_summary = filtered.groupby("variety", as_index=False)["stored_lbs"].sum().sort_values("stored_lbs", ascending=False)
        variety_fig = px.bar(variety_summary, x="stored_lbs", y="variety", orientation="h", labels={"stored_lbs": "Stored pounds", "variety": "Variety"})
        st.plotly_chart(style_figure(variety_fig), width="stretch", theme=None, alt="Stored inventory by variety")
    with right:
        size_summary = filtered.groupby("size", as_index=False)["stored_lbs"].sum().sort_values("stored_lbs", ascending=False)
        size_fig = px.bar(size_summary, x="stored_lbs", y="size", orientation="h", labels={"stored_lbs": "Stored pounds", "size": "Size"})
        st.plotly_chart(style_figure(size_fig), width="stretch", theme=None, alt="Stored inventory by size")

with explorer:
    st.subheader("Data explorer")
    search = st.text_input("Search product, variety, classification, or size", placeholder="Type to filter visible records")
    explorer_df = filtered[filtered.astype(str).apply(lambda row: row.str.contains(search, case=False, na=False).any(), axis=1)] if search else filtered
    st.dataframe(explorer_df, width="stretch", hide_index=True, alt="Searchable inventory records", column_config={"stored_lbs": st.column_config.NumberColumn("Stored (lbs)", format="%,d"), "forecast_demand_lbs": st.column_config.NumberColumn("Demand (lbs)", format="%,d"), "balance_lbs": st.column_config.NumberColumn("Balance (lbs)", format="%,d")})

st.caption("The dashboard visualizes supplied inventory and demand values. It does not calculate forecasts, recommend purchases, optimize profit, or make X-blocking decisions.")

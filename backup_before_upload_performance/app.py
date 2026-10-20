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

from utils.charts import (
    classification_comparison,
    dimension_bar,
    inventory_comparison,
    inventory_timeline,
    load_comparison,
    measure_timeline,
    packout_chart,
)
from utils.excel_loader import frame_for, load_inventory_workbook, normalize_frame, report_to_frame
from utils.theme import apply_theme, style_figure
from utils.ui import render_quality_alerts, source_caption
from utils.validation import apply_filters, filter_caption, options, source_period

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
    st.caption("Upload the original workbook or use the included demonstration records.")
    uploaded = st.file_uploader("Upload Excel Workbook", type=["xlsx"], help="Forecasted Inventory Levels is the first worksheet used for inventory views.")
    st.download_button("Download sample Excel template", data=excel_template(), file_name="inventory_template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")

if uploaded is None:
    df = demo_data()
    source_name, source_sheet, source_status = "Demonstration data", "demo_data", "Demonstration data"
    workbook_report = None
else:
    source_name = uploaded.name
    with st.status("Inspecting workbook and validating worksheets…", expanded=False) as upload_status:
        workbook_report = load_inventory_workbook(uploaded.getvalue())
        upload_status.update(label=f"Workbook inspected: {len(workbook_report.available_sheets)} worksheets found", state="complete")
    inventory_sheets = [sheet for sheet, item in workbook_report.sheet_reports.items() if item.semantic == "inventory" and sheet in workbook_report.frames]
    with st.sidebar:
        sheet_options = inventory_sheets or list(workbook_report.frames)
        if sheet_options:
            selected_sheet = st.selectbox("Primary inventory worksheet", sheet_options, index=sheet_options.index(workbook_report.preferred_sheet) if workbook_report.preferred_sheet in sheet_options else 0)
        else:
            selected_sheet = ""
    if selected_sheet and selected_sheet in workbook_report.frames:
        df = normalize_frame(workbook_report.frames[selected_sheet])
        source_sheet, source_status = selected_sheet, "Verified workbook data"
    else:
        df = pd.DataFrame(columns=["product", "variety", "classification", "size", "packaging_type", "units", "stored_lbs", "forecast_demand_lbs", "year", "week", "reporting_date"])
        source_sheet, source_status = None, "No verified inventory worksheet"

with st.sidebar:
    st.header("Filters")
    year_filter = st.multiselect("Year", options(df, "year"), default=options(df, "year"))
    week_filter = st.multiselect("Reporting week", options(df, "week"), default=options(df, "week"))
    variety_filter = st.multiselect("Variety", options(df, "variety"), default=options(df, "variety"))
    size_filter = st.multiselect("Size", options(df, "size"), default=options(df, "size"))
    class_filter = st.multiselect("Organic / conventional", options(df, "classification"), default=options(df, "classification"))
    product_filter = st.multiselect("Product", options(df, "product"), default=options(df, "product"))
    packaging_filter = st.multiselect("Packaging type", options(df, "packaging_type"), default=options(df, "packaging_type"))
    if st.button("Reset filters", width="stretch"):
        st.rerun()

filters = {
    "year": year_filter, "week": week_filter, "variety": variety_filter,
    "size": size_filter, "classification": class_filter, "product": product_filter,
    "packaging_type": packaging_filter,
}
filtered = apply_filters(df, filters)


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
period = source_period(df) if not df.empty else "Reporting period not supplied"
st.caption(f"Source: **{source_name}** · Worksheet: **{source_sheet or 'Unavailable'}** · Reporting period: **{period}** · {source_status}")

if workbook_report:
    render_quality_alerts(workbook_report.warnings)
    with st.expander("Workbook validation and worksheet details"):
        st.write(f"Available worksheets: {', '.join(workbook_report.available_sheets)}")
        st.dataframe(report_to_frame(workbook_report), width="stretch", hide_index=True)
else:
    st.info("Demo mode is active. Upload the Packout Analysis workbook to populate worksheet-backed timelines, harvest, sales, packout, and purchasing views.")

if not filtered.empty and "stored_lbs" in filtered:
    stored_value = pd.to_numeric(filtered["stored_lbs"], errors="coerce").sum(min_count=1)
    stored = float(stored_value) if pd.notna(stored_value) else None
else:
    stored = None
if not filtered.empty and "forecast_demand_lbs" in filtered:
    demand_value = pd.to_numeric(filtered["forecast_demand_lbs"], errors="coerce").sum(min_count=1)
    demand = float(demand_value) if pd.notna(demand_value) else None
else:
    demand = None
balance = stored - demand if stored is not None and demand is not None else None
k1, k2, k3 = st.columns(3)
k1.metric("Inventory in storage", f"{stored:,.0f} lbs" if stored is not None else "Unavailable")
k2.metric("Forecast demand", f"{demand:,.0f} lbs" if demand is not None else "Unavailable")
k3.metric("Net balance", f"{balance:+,.0f} lbs" if balance is not None else "Unavailable")
st.caption(f"Showing {len(filtered):,} records after filters · {filter_caption(filters)}")

overview, timeline, organic, breakdown, harvest_sales, purchasing, explorer = st.tabs([
    "Executive Overview", "Inventory Timeline", "Organic vs. Conventional", "Product Breakdown",
    "Harvest & Sales", "Purchasing & Loads", "Data Explorer & Quality",
])

with overview:
    st.subheader("Executive overview")
    source_caption(source_sheet, filtered if not filtered.empty else None, period)
    if filtered.empty:
        st.warning("No rows match the current filters. Reset or broaden the filters to continue.", icon="⚠️")
    elif "stored_lbs" in filtered:
        fig = inventory_comparison(filtered)
        st.plotly_chart(style_figure(fig), width="stretch", theme=None, alt="Stored inventory and forecast demand by product")
        st.download_button("Download filtered inventory summary (CSV)", data=filtered.to_csv(index=False).encode("utf-8"), file_name="inventory_summary.csv", mime="text/csv")

with timeline:
    st.subheader("Inventory timeline")
    source_caption(source_sheet, filtered if not filtered.empty else None, period)
    timeline_fig = inventory_timeline(filtered)
    if timeline_fig is None:
        st.info("A verified inventory timeline requires reporting year/week or reporting dates in the uploaded worksheet.")
    else:
        st.plotly_chart(style_figure(timeline_fig), width="stretch", theme=None, alt="Weekly inventory and forecast demand timeline")

with organic:
    st.subheader("Organic versus conventional")
    source_caption(source_sheet, filtered if not filtered.empty else None, period)
    if filtered.empty or "classification" not in filtered or not filtered["classification"].notna().any():
        st.info("No verified organic/conventional classification values are available for the current selection.")
    else:
        st.plotly_chart(style_figure(classification_comparison(filtered)), width="stretch", theme=None, alt="Stored inventory by organic and conventional classification")

with breakdown:
    st.subheader("Product breakdown")
    source_caption(source_sheet, filtered if not filtered.empty else None, period)
    left, right = st.columns(2)
    with left:
        if "variety" in filtered and filtered["variety"].notna().any():
            st.plotly_chart(style_figure(dimension_bar(filtered, "variety", "Variety")), width="stretch", theme=None, alt="Stored inventory by variety")
        else:
            st.info("No verified variety values are available.")
    with right:
        if "size" in filtered and filtered["size"].notna().any():
            st.plotly_chart(style_figure(dimension_bar(filtered, "size", "Size")), width="stretch", theme=None, alt="Stored inventory by size")
        else:
            st.info("No verified size values are available.")

def source_frame_for(semantic: str, measures: list[str]) -> tuple[str | None, pd.DataFrame | None]:
    if workbook_report:
        sheet, frame = frame_for(workbook_report, semantic)
        if frame is not None:
            return sheet, frame
    if source_sheet and source_sheet in (workbook_report.frames if workbook_report else {}):
        frame = workbook_report.frames[source_sheet]
        if any(measure in frame and frame[measure].notna().any() for measure in measures):
            return source_sheet, frame
    return None, None


with harvest_sales:
    st.subheader("Harvest & sales")
    harvest_sheet, harvest_frame = source_frame_for("harvest_sales", ["harvest_lbs", "sales_lbs", "packout_pct"])
    harvest_filtered = apply_filters(harvest_frame, filters) if harvest_frame is not None else None
    source_caption(harvest_sheet, harvest_filtered, source_period(harvest_filtered) if harvest_filtered is not None and not harvest_filtered.empty else "Reporting period not supplied")
    if harvest_filtered is None or harvest_filtered.empty:
        st.info("No verified harvest, sales, or packout worksheet is available for this workbook.")
    else:
        measure_fig = measure_timeline(harvest_filtered, ["harvest_lbs", "sales_lbs"])
        if measure_fig is not None:
            st.plotly_chart(style_figure(measure_fig), width="stretch", theme=None, alt="Harvest and sales plan trends")
        packout_fig = packout_chart(harvest_filtered)
        if packout_fig is not None:
            st.plotly_chart(style_figure(packout_fig), width="stretch", theme=None, alt="Packout values from the workbook")
        if measure_fig is None and packout_fig is None:
            st.info("The worksheet was recognized, but no usable harvest, sales, or packout values were supplied.")

with purchasing:
    st.subheader("Purchasing & loads")
    purchasing_sheet, purchasing_frame = source_frame_for("purchasing_loads", ["purchased_loads", "remaining_loads"])
    purchasing_filtered = apply_filters(purchasing_frame, filters) if purchasing_frame is not None else None
    source_caption(purchasing_sheet, purchasing_filtered, source_period(purchasing_filtered) if purchasing_filtered is not None and not purchasing_filtered.empty else "Reporting period not supplied")
    if purchasing_filtered is None or purchasing_filtered.empty:
        st.info("No verified purchasing or loads worksheet is available for this workbook.")
    else:
        loads_fig = load_comparison(purchasing_filtered)
        if loads_fig is not None:
            st.plotly_chart(style_figure(loads_fig), width="stretch", theme=None, alt="Purchased and remaining loads")
        else:
            st.info("The worksheet was recognized, but purchased or remaining load values were not available.")

with explorer:
    st.subheader("Data explorer & quality")
    if workbook_report:
        st.dataframe(report_to_frame(workbook_report), width="stretch", hide_index=True)
    search = st.text_input("Search verified records", placeholder="Product, variety, packaging, or source value")
    explorer_df = filtered[filtered.astype(str).apply(lambda row: row.str.contains(search, case=False, na=False).any(), axis=1)] if search else filtered
    if explorer_df.empty:
        st.info("No verified records match the current filters or search.")
    else:
        st.dataframe(explorer_df, width="stretch", hide_index=True, alt="Searchable verified source records")
        st.download_button("Download verified records (CSV)", data=explorer_df.to_csv(index=False).encode("utf-8"), file_name="verified_records.csv", mime="text/csv")

st.caption("The dashboard visualizes supplied workbook values. It does not calculate forecasts, recommend purchases, optimize profit, or make X-blocking decisions.")

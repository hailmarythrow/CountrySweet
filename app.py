"""Country Sweet Produce inventory visualization dashboard."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
import html
import logging
from pathlib import Path
from time import perf_counter, sleep
from zoneinfo import ZoneInfo
from zipfile import BadZipFile, ZipFile

import pandas as pd
import requests
import streamlit as st

from utils.charts import (
    classification_comparison,
    dimension_bar,
    inventory_mix_donut,
    inventory_mix_summary,
    inventory_current_vs_projected,
    inventory_periods,
    inventory_timeline,
    load_comparison,
    loads_timeline,
    measure_timeline,
    packout_chart,
)
from utils.excel_loader import REPORT_SCHEMA_VERSION, frame_for, load_inventory_workbook, normalize_frame, report_to_frame, timings_to_frame, workbook_content_id
from utils.theme import apply_theme
from utils.ui import render_chart, render_quality_summary, render_upload_error, render_upload_success, source_caption
from utils.validation import apply_filters, options, source_period

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "Country_Sweet_Produce_Logo_HD.png"
HEADER_IMAGE_PATH = Path(__file__).resolve().parent / "assets" / "backdrop.png"
LOGGER = logging.getLogger("country_sweet.dashboard")
LOGGER.setLevel(logging.INFO)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

st.set_page_config(
    page_title="Country Sweet | Inventory Dashboard",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)

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
# Manual refresh remains implemented for a future persistent OneDrive,
# SharePoint, or local connection, but is temporarily disabled in the UI.
ENABLE_WORKBOOK_REFRESH = False
# Keep parser diagnostics and timing calculations active, but hide the
# technical worksheet report from the normal Executive Overview interface.
SHOW_TECHNICAL_VALIDATION_DETAILS = False
# Keep workbook quality detection and reports active, but temporarily hide
# the end-user warning/expander until the team is ready to surface them again.
SHOW_DATA_QUALITY_UI = False
# Keep chart timing collection active, but hide the technical timing table
# from the everyday dashboard interface.
SHOW_PERFORMANCE_UI = False
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


@st.cache_data(ttl=3600, show_spinner=False)
def cached_workbook_report(_content: bytes, content_id: str, report_schema: str):
    """Cache parsed workbook datasets by content hash across Streamlit reruns."""
    return load_inventory_workbook(_content)


def _read_linked_workbook(path_text: str) -> tuple[Path, bytes, object]:
    """Read a local .xlsx safely, retrying transient Excel-save states."""
    cleaned = str(path_text or "").strip()
    if not cleaned:
        raise ValueError("Enter the path to a local .xlsx workbook first.")
    path = Path(cleaned).expanduser()
    if path.suffix.casefold() != ".xlsx":
        raise ValueError("The linked file must use the .xlsx extension.")
    if not path.exists():
        raise FileNotFoundError(f"The linked workbook was not found: {path}")

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            before = path.stat()
            if before.st_size <= 0:
                raise ValueError("The linked workbook is empty.")
            content = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError("The workbook changed while Excel was saving it.")
            with ZipFile(BytesIO(content)) as archive:
                bad_member = archive.testzip()
                if bad_member:
                    raise ValueError(f"The workbook archive is incomplete at {bad_member}.")
                if "[Content_Types].xml" not in archive.namelist():
                    raise ValueError("The file is not a valid .xlsx workbook.")
            return path, content, after
        except (BadZipFile, OSError, PermissionError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                sleep(0.5 * (attempt + 1))
    raise ValueError(f"The linked workbook could not be read safely: {last_error}") from last_error


def _timestamp_label(value: object) -> str:
    if not value:
        return "Not available"
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%b %d, %Y %I:%M:%S %p %Z")
    except (TypeError, ValueError):
        return str(value)


def _activate_workbook(
    report,
    *,
    source_type: str,
    name: str,
    content_id: str,
    content: bytes | None = None,
    path: Path | None = None,
    file_stat: object | None = None,
) -> None:
    """Store one validated report and its source metadata for later reruns."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    st.session_state.active_workbook_report = report
    st.session_state.active_workbook_name = name
    st.session_state.active_workbook_id = content_id
    st.session_state.active_workbook_source_type = source_type
    # Keep the active session aligned with parser changes. This prevents a
    # previously parsed snapshot from bypassing a newer classification schema.
    st.session_state.active_workbook_report_schema = REPORT_SCHEMA_VERSION
    if content is not None:
        st.session_state.active_workbook_content = content
    st.session_state.active_workbook_path = str(path) if path is not None else ""
    st.session_state.active_workbook_last_refresh = now
    st.session_state.active_workbook_last_saved = (
        datetime.fromtimestamp(file_stat.st_mtime, tz=ZoneInfo("UTC")).astimezone().isoformat(timespec="seconds")
        if file_stat is not None
        else ""
    )
    st.session_state.active_workbook_error = ""
    st.session_state.active_workbook_stale = False


def has_verified_inventory(report) -> bool:
    """Return whether a parsed workbook contains usable rows in a priority sheet."""
    if report is None:
        return False
    for semantic in ("inventory", "inventory_timeline"):
        sheet = report.semantic_sheets.get(semantic)
        frame = report.frames.get(sheet) if sheet else None
        if frame is not None and not frame.empty:
            return True
    return False


def verified_record_count(frame: pd.DataFrame) -> int:
    """Count rows with a product and at least one supplied inventory measure."""
    if frame is None or frame.empty or "product" not in frame:
        return 0
    measures = [column for column in ["stored_lbs", "forecast_demand_lbs", "planned_sales_lbs"] if column in frame]
    if not measures:
        return 0
    return int(frame.loc[frame["product"].notna() & frame[measures].notna().any(axis=1)].shape[0])


def dashboard_icon(name: str, *, size: int = 24) -> str:
    """Return one consistent inline outline icon for the presentation layer."""
    paths = {
        "warehouse": '<path d="M3 10.5 12 4l9 6.5v8.8a1.7 1.7 0 0 1-1.7 1.7H4.7A1.7 1.7 0 0 1 3 19.3z"/><path d="M7 21v-6h10v6M7 10h10M9 10v3m3-3v3m3-3v3"/>',
        "bars": '<path d="M4 20V10m5 10V4m5 16v-7m5 7V7"/><path d="M2 20h20"/>',
        "scale": '<path d="M12 4v16M7 20h10M5 7h14M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z"/>',
        "file": '<path d="M6 2.8h8l4 4v14.4H6z"/><path d="M14 2.8v4h4M9 12h6m-6 4h6"/>',
    }
    path = paths.get(name, paths["file"])
    return f'<svg class="cs-inline-icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'


def render_sidebar_navigation(page_names: list[str]) -> str:
    """Render compact native buttons in the expanded sidebar."""
    icons = {
        "Executive Overview": ":material/dashboard:",
        "Inventory Timeline": ":material/timeline:",
        "Organic vs. Conventional": ":material/compare_arrows:",
        "Product Breakdown": ":material/inventory_2:",
        "Harvest & Sales": ":material/warehouse:",
        "Purchasing & Loads": ":material/local_shipping:",
        "Data Explorer & Quality": ":material/search:",
        "Settings": ":material/settings:",
    }
    with st.sidebar:
        with st.container(key="sidebar_branding"):
            st.image(str(LOGO_PATH), width=135, output_format="PNG")
            st.markdown(
                "<div class='cs-sidebar-company'>Country Sweet Produce</div>"
                "<div class='cs-sidebar-tagline'>Sweeten Your Everyday</div>",
                unsafe_allow_html=True,
            )
        current = st.session_state.get("sidebar_page", page_names[0])
        valid_pages = [*page_names, "Settings"]
        if current not in valid_pages:
            current = page_names[0]
        with st.container(key="sidebar_navigation"):
            for page in page_names:
                if st.button(
                    f"{icons.get(page, ':material/circle:')}  {page}",
                    key=f"sidebar_nav_{page.lower().replace(' ', '_').replace('&', 'and').replace('.', '')}",
                    type="primary" if page == current else "secondary",
                    width="stretch",
                    on_click=lambda selected_page=page: st.session_state.update(sidebar_page=selected_page),
                ):
                    current = page
        with st.container(key="sidebar_settings"):
            st.divider()
            if st.button(
                f"{icons['Settings']}  Settings",
                key="sidebar_nav_settings",
                type="primary" if current == "Settings" else "secondary",
                width="stretch",
                on_click=lambda: st.session_state.update(sidebar_page="Settings"),
            ):
                current = "Settings"
    return current


def render_kpi_cards(stored: float | None, demand: float | None, balance: float | None, unavailable_label: str) -> None:
    """Render the three executive metrics with compact operational accents."""
    stored_label = f"{stored:,.0f} lbs" if stored is not None else unavailable_label
    demand_label = f"{demand:,.0f} lbs" if demand is not None else unavailable_label
    balance_label = f"{balance:+,.0f} lbs" if balance is not None else unavailable_label
    balance_support = "Surplus" if balance is not None and balance >= 0 else "Shortage" if balance is not None else "Awaiting verified values"
    cards = [
        ("storage", "warehouse", "Inventory in storage", stored_label, "Verified stored pounds"),
        ("demand", "bars", "Forecast demand", demand_label, "Workbook demand plan"),
        ("balance", "scale", "Net balance", balance_label, balance_support),
    ]
    markup = "<div class='cs-kpi-grid'>"
    for style, icon, label, value, support in cards:
        if style == "storage":
            markup += (
                "<div class='cs-kpi-card storage'>"
                f"<div class='cs-kpi-storage-content'><div class='cs-kpi-icon'>{dashboard_icon(icon, size=50)}</div>"
                "<div class='cs-kpi-storage-copy'>"
                f"<div class='cs-kpi-label'>{html.escape(label)}</div>"
                f"<div class='cs-kpi-value'>{html.escape(value)}</div>"
                f"<div class='cs-kpi-support'>{html.escape(support)}</div>"
                "</div></div></div>"
            )
        elif style == "demand":
            markup += (
                "<div class='cs-kpi-card demand'>"
                f"<div class='cs-kpi-demand-content'><div class='cs-kpi-icon'>{dashboard_icon(icon, size=50)}</div>"
                "<div class='cs-kpi-demand-copy'>"
                f"<div class='cs-kpi-label'>{html.escape(label)}</div>"
                f"<div class='cs-kpi-value'>{html.escape(value)}</div>"
                f"<div class='cs-kpi-support'>{html.escape(support)}</div>"
                "</div></div></div>"
            )
        elif style == "balance":
            markup += (
                "<div class='cs-kpi-card balance'>"
                f"<div class='cs-kpi-balance-content'><div class='cs-kpi-icon'>{dashboard_icon(icon, size=50)}</div>"
                "<div class='cs-kpi-balance-copy'>"
                f"<div class='cs-kpi-label'>{html.escape(label)}</div>"
                f"<div class='cs-kpi-value'>{html.escape(value)}</div>"
                f"<div class='cs-kpi-support'>{html.escape(support)}</div>"
                "</div></div></div>"
            )
        else:
            markup += (
                f"<div class='cs-kpi-card {style}'><div class='cs-kpi-icon'>{dashboard_icon(icon, size=50)}</div>"
                f"<div class='cs-kpi-label'>{html.escape(label)}</div>"
                f"<div class='cs-kpi-value'>{html.escape(value)}</div>"
                f"<div class='cs-kpi-support'>{html.escape(support)}</div></div>"
            )
    st.markdown(markup + "</div>", unsafe_allow_html=True)


def render_inventory_mix_legend(frame: pd.DataFrame) -> None:
    """Render the compact classification legend for the Inventory Mix card."""
    summary = inventory_mix_summary(frame)
    values = summary.set_index("classification")["stored_lbs"].to_dict() if not summary.empty else {}
    total = float(summary["stored_lbs"].sum()) if not summary.empty else 0.0
    colors = {"Organic": "organic", "Conventional": "conventional"}
    rows = []
    for classification in ("Organic", "Conventional"):
        value = values.get(classification)
        if value is None or total <= 0:
            percentage = "Unavailable"
        else:
            percentage = f"{float(value) / total:.0%}"
        rows.append(
            f"<div class='cs-mix-legend-item'><span class='cs-mix-dot {colors[classification]}'></span>"
            f"<div><div class='cs-mix-legend-label'>{classification}</div><div class='cs-mix-legend-value'>{percentage}</div></div></div>"
        )
    st.markdown("<div class='cs-mix-legend'>" + "".join(rows) + "</div>", unsafe_allow_html=True)


OPEN_METEO_TIMEOUT_SECONDS = 8
OPEN_METEO_CACHE_SECONDS = 900


def _open_meteo_get(endpoint: str, *, params: dict[str, object], operation: str) -> requests.Response:
    """Call Open-Meteo directly so stale process proxy settings cannot break weather."""
    started = perf_counter()
    session = requests.Session()
    # The dashboard calls a public HTTPS endpoint. Ignoring inherited proxy
    # variables avoids stale local proxy settings such as 127.0.0.1:9 while
    # leaving the rest of the application's HTTP behavior unchanged.
    session.trust_env = False
    try:
        response = session.get(endpoint, params=params, timeout=OPEN_METEO_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning(
            "weather.%s failed endpoint=%s elapsed_ms=%.1f error=%s",
            operation, endpoint, (perf_counter() - started) * 1000, exc,
        )
        raise
    finally:
        session.close()
    LOGGER.info(
        "weather.%s succeeded status=%s elapsed_ms=%.1f",
        operation, response.status_code, (perf_counter() - started) * 1000,
    )
    return response


@st.cache_data(ttl=OPEN_METEO_CACHE_SECONDS, show_spinner=False)
def _search_us_locations_cached(query: str) -> list[dict]:
    response = _open_meteo_get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": query,
            "count": 10,
            "language": "en",
            "format": "json",
            "countryCode": "US",
        },
        operation="geocode",
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo geocoding returned an unexpected response.")
    return payload.get("results") or []


def search_us_locations(query: str) -> tuple[list[dict], str | None]:
    query = query.strip()
    if not query:
        return [], None
    try:
        raw_results = _search_us_locations_cached(query)
    except (requests.RequestException, ValueError, TypeError) as exc:
        LOGGER.warning("weather.geocode_parse_failed query=%r error=%s", query, exc)
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


def weather_icon_svg(weather_code: int | None, is_day: bool = True, *, size: int = 42) -> str:
    """Return a scoped, data-driven filled icon for the current conditions."""
    code = int(weather_code) if weather_code is not None else -1
    if code == 0:
        icon_name = "sun" if is_day else "moon"
    elif code in {1, 2}:
        icon_name = "cloud_sun" if is_day else "cloud_moon"
    elif code in {3, 45, 48}:
        icon_name = "fog" if code in {45, 48} else "cloud"
    elif code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        icon_name = "rain"
    elif code in {71, 73, 75, 77, 85, 86}:
        icon_name = "snow"
    elif code in {95, 96, 99}:
        icon_name = "thunder"
    else:
        icon_name = "cloud"
    # The shapes are intentionally filled rather than stroked: this keeps the
    # weather mark legible at the compact 42px size and matches the dashboard's
    # solid icon language. Two-tone conditions use scoped classes so the sun,
    # cloud, precipitation, and lightning retain their natural colors.
    cloud = 'M6.5 19h10.9a5.1 5.1 0 0 0 .5-10.2A7.4 7.4 0 0 0 4.1 9.9 5 5 0 0 0 6.5 19Z'
    paths = {
        "sun": '<circle class="cs-weather-sun-fill" cx="12" cy="12" r="5.1"/><g class="cs-weather-sun-ray"><path d="M12 1.8v2.3M4.78 4.78 6.4 6.4M1.8 12h2.3M4.78 19.22 6.4 17.6M12 22.2v-2.3M19.22 19.22 17.6 17.6M22.2 12h-2.3M19.22 4.78 17.6 6.4"/></g>',
        "moon": '<path class="cs-weather-moon-fill" d="M20.8 14.2A8.8 8.8 0 0 1 9.8 3.2a8.5 8.5 0 1 0 11 11Z"/>',
        "cloud": f'<path class="cs-weather-cloud-fill" d="{cloud}"/>',
        "cloud_sun": f'<circle class="cs-weather-sun-fill" cx="8" cy="8" r="4.6"/><path class="cs-weather-cloud-fill" d="{cloud}"/>',
        "cloud_moon": f'<path class="cs-weather-moon-fill" d="M20.8 6.3A5.1 5.1 0 0 1 15 1.8a5.6 5.6 0 0 0 2 8.8 5.1 5.1 0 0 1 3.8-4.3Z"/><path class="cs-weather-cloud-fill" d="{cloud}"/>',
        "rain": f'<path class="cs-weather-cloud-fill" d="{cloud}"/><path class="cs-weather-rain-fill" d="M7.1 20.8a1.2 1.2 0 0 0 2.2.9l1.8-3.3a1.2 1.2 0 0 0-2.1-1.2Zm5.4 0a1.2 1.2 0 0 0 2.2.9l1.8-3.3a1.2 1.2 0 0 0-2.1-1.2Z"/>',
        "snow": f'<path class="cs-weather-cloud-fill" d="{cloud}"/><circle class="cs-weather-snow-fill" cx="8" cy="19" r="1.7"/><circle class="cs-weather-snow-fill" cx="14" cy="19" r="1.7"/><circle class="cs-weather-snow-fill" cx="20" cy="19" r="1.7"/>',
        "thunder": f'<path class="cs-weather-cloud-fill" d="{cloud}"/><path class="cs-weather-lightning-fill" d="m13.1 13.8-4.2 6.1h3.5l-.9 4.1 5.6-7.2h-3.5l2.1-3Z"/>',
        "fog": f'<path class="cs-weather-cloud-fill" d="{cloud}"/><rect class="cs-weather-fog-fill" x="4" y="19.5" width="16" height="1.8" rx=".9"/><rect class="cs-weather-fog-fill" x="6" y="22" width="12" height="1.5" rx=".75"/>',
    }
    return (
        f'<svg class="cs-weather-svg cs-weather-{icon_name}" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" aria-hidden="true">{paths[icon_name]}</svg>'
    )


@st.cache_data(ttl=OPEN_METEO_CACHE_SECONDS, show_spinner=False)
def _get_weather_cached(latitude: float, longitude: float, temperature_unit: str = "fahrenheit") -> dict:
    temperature_unit = "celsius" if temperature_unit == "celsius" else "fahrenheit"
    response = _open_meteo_get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,is_day",
            "temperature_unit": temperature_unit,
            "timezone": "auto",
        },
        operation="forecast",
    )
    payload = response.json()
    current = payload.get("current") if isinstance(payload, dict) else None
    if not isinstance(current, dict):
        raise ValueError("Open-Meteo forecast returned no current conditions.")
    try:
        temperature = float(current["temperature_2m"])
        weather_code = int(current["weather_code"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Open-Meteo forecast omitted temperature or weather code.") from exc
    try:
        is_day = bool(int(current.get("is_day", 1)))
    except (TypeError, ValueError):
        is_day = True
    condition, icon = WEATHER_CODES.get(weather_code, ("Current conditions", "🌡️"))
    return {
        "temperature": temperature,
        "temperature_unit": temperature_unit,
        "condition": condition,
        "icon": icon,
        "weather_code": weather_code,
        "is_day": is_day,
    }


def get_weather(latitude: float, longitude: float, temperature_unit: str = "fahrenheit") -> dict:
    """Return cached successful conditions; failed requests are retried later."""
    try:
        return _get_weather_cached(float(latitude), float(longitude), temperature_unit)
    except (requests.RequestException, ValueError, TypeError) as exc:
        LOGGER.warning("weather.forecast_parse_failed latitude=%s longitude=%s error=%s", latitude, longitude, exc)
        raise


def apply_weather_location(location: dict) -> None:
    st.session_state.weather_location = location
    st.session_state.weather_location_search = ""
    st.session_state.weather_location_popover = False
    st.rerun()


def apply_settings_weather_location(location: dict) -> None:
    """Persist a Settings-page weather choice and return to the compact view."""
    st.session_state.weather_location = location
    st.session_state.settings_weather_search = ""


def apply_weather_unit_preference() -> None:
    """Copy the Settings-page unit label into the value used by the header."""
    selected = st.session_state.get("settings_weather_unit", "Fahrenheit")
    st.session_state.weather_unit = "celsius" if selected == "Celsius" else "fahrenheit"


def render_top_header() -> object | None:
    """Render the compact header, weather card, and the single workbook uploader."""
    greeting, date, clock = get_welcome(st.session_state.timezone)
    if "weather_location" not in st.session_state:
        st.session_state.weather_location = DEFAULT_LOCATION.copy()
    if "weather_unit" not in st.session_state:
        st.session_state.weather_unit = "fahrenheit"
    selected_location = st.session_state.weather_location

    with st.container(key="hero_header"):
        header_col, right_col = st.columns([3.8, 1.8], gap="medium", vertical_alignment="top")
        with header_col:
            with st.container(key="header_copy"):
                st.html(
                    """
                    <div id="cs-live-clock-shell" style="height:34px; margin:0; padding:0; overflow:hidden;">
                        <div id="cs-live-clock" role="status" aria-live="polite" style="color:#B9C7D5; font-family:sans-serif; font-size:14px; font-weight:400; line-height:17.5px; white-space:nowrap;"></div>
                    </div>
                    <script>
                    (() => {
                        const target = document.getElementById("cs-live-clock");
                        if (!target) return;
                        const timeZone = "America/Los_Angeles";
                        const formatter = new Intl.DateTimeFormat("en-US", {
                            timeZone,
                            weekday: "long",
                            month: "long",
                            day: "2-digit",
                            year: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                            hour12: true,
                            timeZoneName: "short"
                        });
                        const updateClock = () => {
                            const parts = Object.fromEntries(
                                formatter.formatToParts(new Date())
                                    .filter(({ type }) => type !== "literal")
                                    .map(({ type, value }) => [type, value])
                            );
                            target.textContent = `${parts.weekday}, ${parts.month} ${parts.day}, ${parts.year} • ${parts.hour}:${parts.minute}:${parts.second} ${parts.dayPeriod} ${parts.timeZoneName}`;
                        };
                        updateClock();
                        const timer = window.setInterval(updateClock, 1000);
                        document.addEventListener("visibilitychange", updateClock);
                        window.addEventListener("beforeunload", () => {
                            window.clearInterval(timer);
                            document.removeEventListener("visibilitychange", updateClock);
                        }, { once: true });
                    })();
                    </script>
                    """,
                    unsafe_allow_javascript=True,
                )
                st.subheader(f"{greeting},")
                st.write("A clear view of stored inventory, demand coverage, and product mix.")
        with right_col:
            with st.container(key="header_right_stack"):
                try:
                    weather = get_weather(
                        selected_location["latitude"],
                        selected_location["longitude"],
                        st.session_state.weather_unit,
                    )
                    unit_label = "°C" if st.session_state.weather_unit == "celsius" else "°F"
                    weather_markup = (
                        "<div class='cs-weather-widget'>"
                        f"<div class='cs-weather-icon'>{weather_icon_svg(weather.get('weather_code'), weather.get('is_day', True))}</div>"
                        "<div class='cs-weather-info'>"
                        f"<div class='cs-weather-location'>{html.escape(location_label(selected_location))}</div>"
                        f"<div class='cs-weather-temperature'>{weather['temperature']:.0f} {unit_label}</div>"
                        f"<div class='cs-weather-condition'>{html.escape(weather['condition'])}</div>"
                        "</div></div>"
                    )
                    st.markdown(weather_markup, unsafe_allow_html=True)
                except Exception as exc:
                    LOGGER.warning("weather.widget_unavailable location=%s error=%s", location_label(selected_location), exc)
                    st.markdown(
                        "<div class='cs-weather-widget cs-weather-widget--unavailable'>"
                        f"<div class='cs-weather-icon'>{weather_icon_svg(None, True)}</div>"
                        "<div class='cs-weather-info'>"
                        f"<div class='cs-weather-location'>{html.escape(location_label(selected_location))}</div>"
                        "<div class='cs-weather-condition'>Weather temporarily unavailable</div>"
                        "</div></div>",
                        unsafe_allow_html=True,
                    )
                current_name = st.session_state.get("active_workbook_name")
                upload_state_class = "cs-upload-state--replace" if current_name else "cs-upload-state--choose"
                excel_upload_icon = (
                    "<svg class='cs-excel-upload-icon' width='58' height='64' viewBox='0 0 58 64' "
                    "aria-hidden='true'><path d='M8 2h29l13 13v47H8z' fill='#187C56'/>"
                    "<path d='M37 2v13h13' fill='#0F6848'/><path d='M37 2 50 15H37z' fill='#5AB68D'/>"
                    "<rect x='16' y='23' width='26' height='20' rx='1.5' fill='#F1FBF6'/>"
                    "<path d='M16 29.7h26M16 36.4h26M24.7 23v20M33.4 23v20' fill='none' stroke='#187C56' stroke-width='1.5'/>"
                    "<text x='29' y='56.5' text-anchor='middle' fill='#F1FBF6' font-family='Arial, sans-serif' font-size='7' font-weight='700' letter-spacing='.5'>XLSX</text></svg>"
                )
                with st.container(key="upload_panel"):
                    st.markdown(
                        "<div class='cs-upload-intro'>"
                        f"<div class='cs-upload-icon-wrap'>{excel_upload_icon}</div>"
                        "<div class='cs-upload-copy'>"
                        "<div class='cs-upload-heading'>Upload Inventory Workbook</div>"
                        "<div class='cs-upload-subtitle'>Drag and drop your Excel file here, or click to browse.</div>"
                        "<div class='cs-upload-support'>Supports .xlsx files (200MB per file)</div>"
                        "</div></div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"<span class='cs-upload-state {upload_state_class}' aria-hidden='true'></span>", unsafe_allow_html=True)
                    uploaded = st.file_uploader(
                        "Choose Excel File",
                        type=["xlsx"],
                        key="workbook_uploader",
                        help="A browser upload is a validated snapshot. Replace it here when you have a newer workbook version.",
                        label_visibility="collapsed",
                    )
                    if current_name:
                        st.caption(f"Current workbook: **{current_name}**")
                        st.caption(f"Last successful refresh: {_timestamp_label(st.session_state.get('active_workbook_last_refresh'))}")
                    return uploaded
    return None


if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True
else:
    st.session_state.dark_mode = True
if "timezone" not in st.session_state:
    st.session_state.timezone = "America/Los_Angeles"

apply_theme(True, HEADER_IMAGE_PATH)
previous_report = st.session_state.get("active_workbook_report")
previous_name = st.session_state.get("active_workbook_name")
previous_content = st.session_state.get("active_workbook_content")
workbook_report = None
load_error = None
uploaded = None
refresh_clicked = False
uploaded = render_top_header()

def parse_and_activate(
    content: bytes,
    *,
    name: str,
    source_type: str,
    path: Path | None = None,
    file_stat: object | None = None,
    force_refresh: bool = False,
):
    content_id = workbook_content_id(content)
    if force_refresh:
        # Explicit refresh means "reprocess the latest saved source";
        # normal reruns still use the content hash to avoid unnecessary work.
        cached_workbook_report.clear()
    parse_started = perf_counter()
    report = cached_workbook_report(content, content_id, REPORT_SCHEMA_VERSION)
    LOGGER.info(
        "excel.parse_complete filename=%s content_id=%s elapsed_ms=%.1f parser_total_ms=%.1f",
        name,
        content_id[:12],
        (perf_counter() - parse_started) * 1000,
        report.timings.get("total_ms", 0.0),
    )
    if not has_verified_inventory(report):
        raise ValueError("No verified inventory rows were found in Current Week Model or Forecasted Inventory Levels.")
    _activate_workbook(report, source_type=source_type, name=name, content_id=content_id, content=content, path=path, file_stat=file_stat)
    return report


if refresh_clicked and st.session_state.get("active_workbook_path"):
    refresh_path = st.session_state.get("active_workbook_path")
    try:
        with st.status("Refreshing workbook", expanded=False) as refresh_status:
            refresh_status.update(label="File received")
            LOGGER.info("excel.stage File received path=%s", refresh_path)
            refresh_status.update(label="Reading workbook")
            LOGGER.info("excel.stage Reading workbook path=%s", refresh_path)
            refresh_status.update(label="Validating data")
            refresh_path_obj, refresh_content, refresh_stat = _read_linked_workbook(refresh_path)
            refreshed_report = parse_and_activate(
                refresh_content,
                name=refresh_path_obj.name,
                source_type="local_link",
                path=refresh_path_obj,
                file_stat=refresh_stat,
                force_refresh=True,
            )
            refresh_status.update(label="Updating visualizations")
            LOGGER.info("excel.stage Updating visualizations path=%s", refresh_path)
            workbook_report = refreshed_report
            refresh_status.update(label="Complete", state="complete")
        st.success(f"Workbook refreshed at {_timestamp_label(st.session_state.get('active_workbook_last_refresh'))}.")
    except Exception as exc:
        LOGGER.exception("excel.refresh_failed path=%s", refresh_path)
        load_error = str(exc)
        st.session_state.active_workbook_error = load_error
        st.session_state.active_workbook_stale = True
        workbook_report = previous_report

if uploaded is not None and not refresh_clicked:
    upload_received_started = perf_counter()
    uploaded_bytes = uploaded.getvalue()
    LOGGER.info(
        "excel.file_received filename=%s bytes=%d copy_elapsed_ms=%.1f",
        uploaded.name,
        len(uploaded_bytes),
        (perf_counter() - upload_received_started) * 1000,
    )
    upload_id = workbook_content_id(uploaded_bytes)
    active_id = st.session_state.get("active_workbook_id")
    active_source = st.session_state.get("active_workbook_source_type")
    active_schema = st.session_state.get("active_workbook_report_schema")
    if (
        active_id == upload_id
        and active_source == "upload"
        and active_schema == REPORT_SCHEMA_VERSION
        and previous_report is not None
    ):
        workbook_report = previous_report
        # A replacement can be byte-for-byte identical while arriving under
        # a new filename. Reuse the verified parse, but still update the
        # snapshot metadata so the UI reflects the workbook the user chose.
        if uploaded.name != st.session_state.get("active_workbook_name"):
            _activate_workbook(
                previous_report,
                source_type="upload",
                name=uploaded.name,
                content_id=upload_id,
                content=uploaded_bytes,
            )
    else:
        upload_started = perf_counter()
        with st.status("File received", expanded=False) as upload_status:
            LOGGER.info("excel.stage File received filename=%s", uploaded.name)
            upload_status.update(label="Reading workbook")
            LOGGER.info("excel.stage Reading workbook filename=%s", uploaded.name)
            try:
                upload_status.update(label="Validating data")
                LOGGER.info("excel.stage Validating data filename=%s", uploaded.name)
                workbook_report = parse_and_activate(uploaded_bytes, name=uploaded.name, source_type="upload")
                LOGGER.info(
                    "excel.data_normalized filename=%s records=%d worksheets=%d validation_elapsed_ms=%.1f",
                    uploaded.name,
                    sum(len(frame) for frame in workbook_report.frames.values()),
                    len(workbook_report.available_sheets),
                    workbook_report.timings.get("validation_ms", 0.0),
                )
                upload_status.update(label="Updating visualizations")
                LOGGER.info("excel.stage Updating visualizations filename=%s", uploaded.name)
                LOGGER.info("excel.upload filename=%s bytes=%d elapsed_ms=%.1f content_id=%s", uploaded.name, len(uploaded_bytes), (perf_counter() - upload_started) * 1000, upload_id[:12])
                upload_status.update(label="Complete", state="complete")
                LOGGER.info("excel.stage Complete filename=%s total_elapsed_ms=%.1f", uploaded.name, (perf_counter() - upload_started) * 1000)
                # Render the uploader again with its post-upload label and
                # current workbook metadata immediately after activation.
                st.rerun()
            except Exception as exc:
                LOGGER.exception("excel.upload_failed filename=%s", uploaded.name)
                load_error = str(exc)
                # Keep the last verified workbook visible, but label it as
                # stale so a failed replacement is never mistaken for the
                # newly selected file.
                st.session_state.active_workbook_error = load_error
                if previous_report is not None:
                    st.session_state.active_workbook_stale = True
                upload_status.update(label="Upload failed — see details below", state="error")
    if workbook_report is None and previous_report is not None:
        workbook_report = previous_report
elif workbook_report is None and previous_report is not None:
    workbook_report = previous_report

# Keep upload state immediately visible above the worksheet selector. Demo mode
# intentionally has no success banner, and a failed replacement takes
# precedence over the previous workbook's success state.
active_source = st.session_state.get("active_workbook_source_type", "")
if load_error:
    render_upload_error(
        load_error,
        filename=getattr(uploaded, "name", None),
        retained_previous=previous_report is not None,
    )
elif workbook_report is not None and active_source in {"upload", "local_link"}:
    primary_sheet = workbook_report.preferred_sheet or workbook_report.semantic_sheets.get("inventory")
    primary_frame = workbook_report.frames.get(primary_sheet) if primary_sheet else None
    loaded_records = verified_record_count(primary_frame) if primary_frame is not None else 0
    render_upload_success(
        st.session_state.get("active_workbook_name") or previous_name or "Loaded workbook",
        loaded_records,
        source_type=active_source,
    )

if workbook_report is None:
    df = demo_data()
    source_name, source_sheet, source_status = "Demonstration data", "demo_data", "Demonstration data"
else:
    source_name = st.session_state.get("active_workbook_name") or previous_name or "Loaded workbook"
    inventory_sheets = [
        sheet for sheet, item in workbook_report.sheet_reports.items()
        if item.semantic == "inventory" and sheet in workbook_report.frames
    ]
    sheet_options = inventory_sheets or list(workbook_report.frames)
    if sheet_options:
        selected_sheet = st.selectbox(
            "Primary inventory worksheet",
            sheet_options,
            index=sheet_options.index(workbook_report.preferred_sheet) if workbook_report.preferred_sheet in sheet_options else 0,
        )
    else:
        selected_sheet = ""
    if selected_sheet and selected_sheet in workbook_report.frames:
        df = normalize_frame(workbook_report.frames[selected_sheet])
        source_sheet, source_status = selected_sheet, "Verified workbook data"
    else:
        df = pd.DataFrame(columns=["product", "variety", "classification", "size", "packaging_type", "units", "stored_lbs", "forecast_demand_lbs", "year", "week", "reporting_date"])
        source_sheet, source_status = None, "No verified inventory worksheet"

active_source = st.session_state.get("active_workbook_source_type", "")
if workbook_report is not None:
    if active_source == "upload":
        source_status = "Uploaded snapshot · verified data"
        if st.session_state.get("active_workbook_stale"):
            source_status = "Uploaded snapshot · showing last successful update"
    elif active_source == "local_link":
        source_status = "Local workbook · verified data"
        if st.session_state.get("active_workbook_stale"):
            source_status = "Local workbook · showing last successful update"
timeline_filter_frame = None
if workbook_report is not None:
    _, timeline_filter_frame = frame_for(workbook_report, "inventory_timeline")
week_option_frame = timeline_filter_frame if timeline_filter_frame is not None else df

year_frames = [
    frame[["year"]]
    for frame in (df, week_option_frame)
    if frame is not None and "year" in frame
]
year_source = pd.concat(year_frames, ignore_index=True) if year_frames else pd.DataFrame(columns=["year"])
year_options = options(year_source, "year")
week_options = options(week_option_frame, "week")
filter_signature = f"{getattr(workbook_report, 'content_id', 'demo')}::{source_sheet or 'demo'}"
filter_keys = {
    "year": "filter_year", "week": "filter_week", "variety": "filter_variety",
    "size": "filter_size", "classification": "filter_classification",
    "product": "filter_product", "packaging_type": "filter_packaging_type",
}
if st.session_state.get("filter_signature") != filter_signature:
    for key in filter_keys.values():
        st.session_state[key] = []
    st.session_state.filter_signature = filter_signature
for key in filter_keys.values():
    st.session_state.setdefault(key, [])

filters = {
    "year": st.session_state[filter_keys["year"]],
    "week": st.session_state[filter_keys["week"]],
    "variety": st.session_state[filter_keys["variety"]],
    "size": st.session_state[filter_keys["size"]],
    "classification": st.session_state[filter_keys["classification"]],
    "product": st.session_state[filter_keys["product"]],
    "packaging_type": st.session_state[filter_keys["packaging_type"]],
}
filtered = apply_filters(df, filters)
record_stage_counts = {
    "Workbook worksheets recognized": len(workbook_report.available_sheets) if workbook_report else 0,
    "Priority worksheet records parsed": sum(
        report.row_count for name, report in workbook_report.sheet_reports.items()
        if workbook_report and name in {"Current Week Model", "Forecasted Inventory Levels"}
    ) if workbook_report else 0,
    "Records normalized": len(df),
    "Records with verified measures": verified_record_count(df),
    "Records after filters": len(filtered),
    "Records sent to overview visualizations": len(filtered),
}
if not df.empty and filtered.empty:
    st.warning("No records match the selected filters. Blank filters mean All; use Reset filters to restore the complete verified dataset.", icon="⚠️")

period = source_period(df) if not df.empty else "Reporting period not supplied"

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
no_matching_records = bool(df is not None and not df.empty and filtered.empty)
missing_metric_label = "No matching records" if no_matching_records else ("Not available from selected worksheet" if workbook_report else "Unavailable")
render_kpi_cards(stored, demand, balance, missing_metric_label)

if workbook_report:
    if SHOW_DATA_QUALITY_UI:
        render_quality_summary(workbook_report)
    if SHOW_TECHNICAL_VALIDATION_DETAILS:
        with st.expander("Workbook validation and worksheet details"):
            st.dataframe(
                pd.DataFrame([{"Stage": stage, "Records": count} for stage, count in record_stage_counts.items()]),
                width="stretch", hide_index=True,
            )
            st.write(f"Available worksheets: {', '.join(workbook_report.available_sheets)}")
            st.dataframe(report_to_frame(workbook_report), width="stretch", hide_index=True)
            st.caption(f"Deferred worksheets: {len(workbook_report.deferred_sheets)}. They are not opened or converted until explicitly requested.")
            st.dataframe(timings_to_frame(workbook_report), width="stretch", hide_index=True)
if workbook_report is not None and source_sheet == workbook_report.semantic_sheets.get("inventory") and not filtered.empty:
    model_week = ", ".join(options(filtered, "week")) or "selected week"
    contribution = pd.to_numeric(filtered.get("contribution_margin"), errors="coerce").sum(min_count=1) if "contribution_margin" in filtered else None
    upcoming = pd.to_numeric(filtered.get("upcoming_demand_lbs"), errors="coerce").sum(min_count=1) if "upcoming_demand_lbs" in filtered else None
    st.caption(f"Current Week Model supplied totals · Week {model_week}")
    cm_col, upcoming_col = st.columns(2)
    with cm_col:
        with st.container(key="contribution_metric"):
            st.metric("Contribution margin", f"${contribution:,.0f}" if pd.notna(contribution) else "Not available from selected worksheet")
    with upcoming_col:
        with st.container(key="upcoming_planned_metric"):
            st.metric("Upcoming planned pounds", f"{upcoming:,.0f} lbs" if pd.notna(upcoming) else "Not available from selected worksheet")

with st.container(key="filters_panel", border=True):
    st.markdown("#### Filters")
    filter_row_one = st.columns(4, gap="small")
    with filter_row_one[0]:
        st.multiselect("Year", year_options, key=filter_keys["year"], placeholder="All years")
    with filter_row_one[1]:
        st.multiselect("Reporting week", week_options, key=filter_keys["week"], placeholder="All weeks")
    with filter_row_one[2]:
        st.multiselect("Variety", options(df, "variety"), key=filter_keys["variety"], placeholder="All varieties")
    with filter_row_one[3]:
        st.multiselect("Organic / conventional", options(df, "classification"), key=filter_keys["classification"], placeholder="All classifications")
    filter_row_two = st.columns(4, gap="small")
    with filter_row_two[0]:
        st.multiselect("Size", options(df, "size"), key=filter_keys["size"], placeholder="All sizes")
    with filter_row_two[1]:
        st.multiselect("Product", options(df, "product"), key=filter_keys["product"], placeholder="All products")
    with filter_row_two[2]:
        st.multiselect("Packaging type", options(df, "packaging_type"), key=filter_keys["packaging_type"], placeholder="All packaging")
    with filter_row_two[3]:
        def reset_filters() -> None:
            for key in filter_keys.values():
                st.session_state[key] = []

        st.button("Reset filters", width="stretch", on_click=reset_filters)

visualization_started = perf_counter()
harvest_view_available = bool(workbook_report and frame_for(workbook_report, "harvest_sales")[1] is not None and not frame_for(workbook_report, "harvest_sales")[1].empty)
purchasing_view_available = bool(workbook_report and frame_for(workbook_report, "purchasing_loads")[1] is not None and not frame_for(workbook_report, "purchasing_loads")[1].empty)
navigation_pages = ["Executive Overview", "Inventory Timeline", "Organic vs. Conventional", "Product Breakdown"]
if harvest_view_available:
    navigation_pages.append("Harvest & Sales")
if purchasing_view_available:
    navigation_pages.append("Purchasing & Loads")
navigation_pages.append("Data Explorer & Quality")
active_page = render_sidebar_navigation(navigation_pages)


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

if active_page == "Settings":
    st.subheader("Settings")
    st.write("Manage your dashboard preferences and application settings.")
    st.markdown("#### Weather Preferences")
    st.caption("Choose the location and temperature units shown in the dashboard header.")

    current_location = st.session_state.get("weather_location", DEFAULT_LOCATION.copy())
    st.caption(f"Current location: **{location_label(current_location)}**")
    weather_search = st.text_input(
        "Search U.S. city or state",
        placeholder="Bakersfield, California",
        key="settings_weather_search",
    )
    if weather_search.strip():
        search_results, search_error = search_us_locations(weather_search)
        if search_error:
            st.caption(search_error)
        elif search_results:
            result_labels = [location_label(location) for location in search_results]
            selected_result = st.selectbox(
                "Matching locations",
                result_labels,
                key="settings_weather_result",
            )
            selected_location = search_results[result_labels.index(selected_result)]
            st.button(
                "Use selected location",
                key="settings_weather_apply",
                on_click=apply_settings_weather_location,
                args=(selected_location,),
            )
        else:
            st.caption(f"No U.S. cities found for “{weather_search.strip()}”.")

    if "settings_weather_unit" not in st.session_state:
        st.session_state.settings_weather_unit = (
            "Celsius" if st.session_state.get("weather_unit") == "celsius" else "Fahrenheit"
        )
    st.radio(
        "Temperature units",
        ["Fahrenheit", "Celsius"],
        horizontal=True,
        key="settings_weather_unit",
        on_change=apply_weather_unit_preference,
    )
    st.caption("The header updates after you apply a location or change units.")
    st.caption("Additional customization options will be introduced in a future update.")

if active_page == "Executive Overview":
    st.subheader("Executive overview")
    if workbook_report is None:
        st.markdown(
            "<div class='cs-demo-mode'>"
            "<div class='cs-demo-mode-icon' aria-hidden='true'>i</div>"
            "<div class='cs-demo-mode-copy'>"
            "<div class='cs-demo-mode-title'>Demo Mode</div>"
            "<div class='cs-demo-mode-message'>You're viewing sample inventory data. Upload your Excel workbook to see your company's actual data.</div>"
            "</div></div>",
            unsafe_allow_html=True,
        )
    if filtered.empty:
        st.warning("No rows match the current filters. Reset or broaden the filters to continue.", icon="⚠️")
    elif "stored_lbs" in filtered:
        overview_chart_col, mix_col = st.columns([2, 1], gap="medium", vertical_alignment="top")
        with overview_chart_col:
            with st.container(key="inventory_panel", border=True):
                st.markdown("#### Current vs. Projected Inventory")
                timeline_sheet, timeline_frame = frame_for(workbook_report, "inventory_timeline") if workbook_report else (None, None)
                comparison_frame = apply_filters(timeline_frame, filters) if timeline_frame is not None else None
                actual_periods = inventory_periods(comparison_frame, "Actual") if comparison_frame is not None else pd.DataFrame()
                forecast_periods = inventory_periods(comparison_frame, "Forecast") if comparison_frame is not None else pd.DataFrame()
                if timeline_sheet is None or comparison_frame is None or actual_periods.empty or forecast_periods.empty:
                    st.info("Upload a verified Forecasted Inventory Levels worksheet with ACTUAL and FORECAST periods to compare inventory balances.")
                else:
                    latest_actual = actual_periods.iloc[-1]
                    later_forecasts = forecast_periods[forecast_periods["_sort_key"] > latest_actual["_sort_key"]]
                    default_forecast = later_forecasts.iloc[0] if not later_forecasts.empty else forecast_periods.iloc[0]
                    forecast_labels = forecast_periods["label"].tolist()
                    forecast_widget_key = f"inventory_forecast_week_{getattr(workbook_report, 'content_id', 'workbook')[:12]}"
                    selected_forecast_label = st.selectbox(
                        "Forecast Week",
                        forecast_labels,
                        index=forecast_labels.index(default_forecast["label"]),
                        key=forecast_widget_key,
                    )
                    selected_forecast = forecast_periods.loc[forecast_periods["label"] == selected_forecast_label].iloc[0]
                    st.caption(
                        f"Source worksheet: {timeline_sheet} · Actual {latest_actual['label']} · Forecast {selected_forecast['label']}"
                    )
                    group_by = "Individual Product" if len(filters.get("variety", [])) == 1 else "Variety"
                    render_chart(
                        lambda: inventory_current_vs_projected(comparison_frame, selected_forecast["key"], group_by),
                        "executive_overview",
                        "Current and projected inventory by product or variety",
                    )
        with mix_col:
            with st.container(key="mix_panel", border=True):
                st.markdown("#### Inventory Mix")
                st.markdown("<div class='cs-mix-subtitle'>Share of inventory in storage by classification.</div>", unsafe_allow_html=True)
                mix_chart_col, mix_legend_col = st.columns([3, 2], gap="small", vertical_alignment="center")
                with mix_chart_col:
                    render_chart(lambda: inventory_mix_donut(filtered), "inventory_mix", "Inventory mix")
                with mix_legend_col:
                    render_inventory_mix_legend(filtered)
        st.download_button("Export Inventory Data", data=filtered.to_csv(index=False).encode("utf-8"), file_name="inventory_summary.csv", mime="text/csv")

if active_page == "Inventory Timeline":
    st.subheader("Inventory timeline")
    timeline_sheet, timeline_frame = source_frame_for("inventory_timeline", ["stored_lbs"])
    timeline_filtered = apply_filters(timeline_frame, filters) if timeline_frame is not None else None
    source_caption(timeline_sheet, timeline_filtered, source_period(timeline_filtered) if timeline_filtered is not None and not timeline_filtered.empty else "Reporting period not supplied")
    timeline_fig = render_chart(lambda: inventory_timeline(timeline_filtered) if timeline_filtered is not None else None, "inventory_timeline", "Weekly inventory and forecast demand timeline")
    if timeline_fig is None:
        st.info("A verified inventory timeline requires the Forecasted Inventory Levels worksheet with reporting dates or weeks.")

if active_page == "Organic vs. Conventional":
    st.subheader("Organic versus conventional")
    source_caption(source_sheet, filtered, period)
    if filtered.empty or "classification" not in filtered or not filtered["classification"].notna().any():
        st.info("No verified organic/conventional classification values are available for the current selection.")
    else:
        render_chart(lambda: classification_comparison(filtered), "classification_comparison", "Stored inventory by organic and conventional classification")

if active_page == "Product Breakdown":
    st.subheader("Product breakdown")
    source_caption(source_sheet, filtered, period)
    left, right = st.columns(2)
    with left:
        if "variety" in filtered and filtered["variety"].notna().any():
            render_chart(lambda: dimension_bar(filtered, "variety", "Variety"), "variety_breakdown", "Stored inventory by variety")
        else:
            st.info("No verified variety values are available.")
    with right:
        if "size" in filtered and filtered["size"].notna().any():
            render_chart(lambda: dimension_bar(filtered, "size", "Size"), "size_breakdown", "Stored inventory by size")
        else:
            st.info("No verified size values are available.")

if harvest_view_available:
    if active_page == "Harvest & Sales":
        st.subheader("Harvest & sales")
        harvest_sheet, harvest_frame = source_frame_for("harvest_sales", ["harvest_lbs", "planned_sales_lbs", "packout_pct"])
        if harvest_frame is not None:
            # These sources do not share a verified calendar: harvest has week
            # numbers without a crop year, while Sales by Week has explicit
            # 2026–2027 years. Filter each series independently and never
            # connect unrelated periods on one continuous line.
            harvest_series = harvest_frame[harvest_frame["harvest_lbs"].notna()].copy()
            sales_series = harvest_frame[harvest_frame["planned_sales_lbs"].notna()].copy()
            packout_series = harvest_frame[harvest_frame["packout_pct"].notna()].copy()
            dimension_filters = {key: values for key, values in filters.items() if key not in {"year", "week"}}
            harvest_filtered = apply_filters(harvest_series, dimension_filters)
            sales_filtered = apply_filters(sales_series, filters)
            packout_filtered = apply_filters(packout_series, dimension_filters)
        else:
            harvest_filtered = sales_filtered = packout_filtered = None
        source_caption(harvest_sheet, harvest_frame, source_period(harvest_frame) if harvest_frame is not None and not harvest_frame.empty else "Reporting period not supplied")
        st.caption("Harvest and sales are shown in separate charts because only Sales by Week includes a verified crop year. Values remain in the workbook's supplied pounds; no additional packout adjustment is applied.")
        render_chart(lambda: measure_timeline(harvest_filtered, ["harvest_lbs"]) if harvest_filtered is not None else None, "harvest_timeline", "Forecast harvest pounds by source week")
        render_chart(lambda: measure_timeline(sales_filtered, ["planned_sales_lbs"]) if sales_filtered is not None else None, "sales_timeline", "Planned sales pounds by crop year and week")
        render_chart(lambda: packout_chart(packout_filtered) if packout_filtered is not None else None, "packout", "Monthly packout percentages from the workbook")
        if packout_filtered is not None and not packout_filtered.empty and "source_cell" in packout_filtered:
            with st.expander("Packout source points"):
                st.dataframe(
                    packout_filtered[["product", "period_label", "packout_pct", "source_cell", "source_period"]],
                    width="stretch", hide_index=True,
                )

if purchasing_view_available:
    if active_page == "Purchasing & Loads":
        st.subheader("Purchasing & loads")
        purchasing_sheet, purchasing_frame = source_frame_for("purchasing_loads", ["total_loads_needed", "purchased_loads", "remaining_loads", "loads_per_week"])
        purchasing_filtered = apply_filters(purchasing_frame, filters) if purchasing_frame is not None else None
        source_caption(purchasing_sheet, purchasing_filtered, source_period(purchasing_filtered) if purchasing_filtered is not None and not purchasing_filtered.empty else "Reporting period not supplied")
        if purchasing_filtered is not None and not purchasing_filtered.empty:
            def source_sum(column: str) -> tuple[float | None, int, int]:
                values = pd.to_numeric(purchasing_filtered[column], errors="coerce") if column in purchasing_filtered else pd.Series(dtype="float64")
                valid = values.dropna()
                total = valid.sum(min_count=1)
                return (float(total) if pd.notna(total) else None, int(valid.size), int((valid < 0).sum()))

            total_needed, needed_rows, needed_negative = source_sum("total_loads_needed")
            purchased, purchased_rows, purchased_negative = source_sum("purchased_loads")
            remaining, remaining_rows, remaining_negative = source_sum("remaining_loads")
            metric_columns = st.columns(3)
            needed_label = "Total loads needed (review)" if needed_negative else "Total loads needed (source)"
            remaining_label = "Loads remaining (review)" if remaining_negative else "Loads remaining (source)"
            metric_columns[0].metric(needed_label, f"{total_needed:,.2f}" if total_needed is not None else "Unavailable")
            metric_columns[1].metric("Loads purchased (source)", f"{purchased:,.2f}" if purchased is not None else "Unavailable")
            metric_columns[2].metric(remaining_label, f"{remaining:,.2f}" if remaining is not None else "Unavailable")
            if any(value > 0 for value in [needed_negative, purchased_negative, remaining_negative]):
                st.caption("⚠️ Review required: the POs worksheet contains negative load values. They are displayed exactly as supplied and were not converted to zero or absolute values.")
            st.caption(
                f"Separate valid-cell totals: needed {needed_rows} rows, purchased {purchased_rows} rows, remaining {remaining_rows} rows. These populations do not fully reconcile because some purchased cells have invalid or missing needed/remaining values. POs formula errors remain listed in the quality report."
            )
            po_rows = purchasing_filtered[purchasing_filtered.get("source_cells", pd.Series(index=purchasing_filtered.index)).notna()].copy() if "source_cells" in purchasing_filtered else pd.DataFrame()
            if not po_rows.empty:
                with st.expander("PO source reconciliation"):
                    st.dataframe(
                        po_rows[["product", "total_loads_needed", "purchased_loads", "remaining_loads", "source_cells", "source_period"]],
                        width="stretch", hide_index=True,
                    )
            render_chart(lambda: load_comparison(purchasing_filtered), "purchasing_loads", "Purchased and remaining loads from POs")
            st.caption("Loads per Week is plotted as an overall weekly trend. Product and variety filters above still apply; blank cells are omitted and source zeros remain zeros. The source does not provide a crop year.")
            render_chart(lambda: loads_timeline(purchasing_filtered), "weekly_loads", "Overall weekly loads from Loads per Week")

if active_page == "Data Explorer & Quality":
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

LOGGER.info(
    "dashboard.visualizations_updated workbook=%s elapsed_ms=%.1f charts=%d",
    source_name,
    (perf_counter() - visualization_started) * 1000,
    len(st.session_state.get("chart_timings", {})),
)

if SHOW_PERFORMANCE_UI and st.session_state.get("chart_timings"):
    with st.expander("Dashboard render timings"):
        st.dataframe(pd.DataFrame([{"Chart": label, "Elapsed ms": round(value, 1)} for label, value in st.session_state["chart_timings"].items()]), width="stretch", hide_index=True)

"""Country Sweet Produce inventory visualization dashboard."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
import html
import logging
import os
from pathlib import Path
from time import perf_counter, sleep, time as epoch_time
from typing import Any
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
from utils.validation import apply_filters, options, source_period, standardized_dimension_options
from utils.local_workbook_link import LocalWorkbookLink, choose_workbook, load_link, save_link
from utils.dashboard_snapshot import (
    DEFAULT_NAMESPACE as DEFAULT_DEV_SNAPSHOT_NAMESPACE,
    DashboardSnapshotError,
    SnapshotSource,
    active_snapshot_version,
    publish_dashboard_snapshot,
    read_snapshot_refresh_metadata,
    record_snapshot_refresh,
    restore_dashboard_snapshot,
)
from utils.firebase_auth import (
    FirebaseAuthConfig,
    FirebaseAuthError,
    FirebaseEmployee,
    approve_new_account,
    cancel_invitation,
    clear_auth_session,
    invitation_digest,
    invite_teammate,
    list_invitations,
    list_team_members,
    remember_employee,
    normalize_person_name,
    revoke_user,
    render_firebase_auth_component,
    require_permission,
    session_employee,
    token_digest,
    validate_invitation,
    verify_id_token,
)
from utils.firebase_admin_client import get_firebase_app, runtime_setting
from utils.x_blocking import (
    build_comparison,
    build_organic_outlook,
    comparison_periods,
    organic_horizon_options,
    pricing_assessment,
    reference_price_comparison,
    reconciliation_report,
    source_inventory,
)

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "Country_Sweet_Produce_Logo_HD.png"
SIDEBAR_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "bakosweet.png"
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
def _env_flag(name: str, default: bool = False) -> bool:
    return str(runtime_setting(name, str(default))).strip().casefold() in {"1", "true", "yes", "on"}


# Development-only local OneDrive-sync demonstration. This is deliberately
# opt-in and does not activate Graph or Country Sweet employee authentication.
ENABLE_LOCAL_WORKBOOK_DEMO = _env_flag("BAKO_LOCAL_WORKBOOK_DEMO", False)
# Development-only persistence is opt-in and never uses the production active
# workbook metadata pointer. It stores dashboard-ready frames only.
ENABLE_DEV_FIRESTORE_SNAPSHOTS = _env_flag("BAKO_DEV_FIRESTORE_SNAPSHOTS", False)
DEV_SNAPSHOT_NAMESPACE = str(
    runtime_setting("BAKO_DEV_FIRESTORE_SNAPSHOT_NAMESPACE", DEFAULT_DEV_SNAPSHOT_NAMESPACE)
).strip() or DEFAULT_DEV_SNAPSHOT_NAMESPACE
FIREBASE_AUTH_CONFIG = FirebaseAuthConfig.from_environment()
FIREBASE_AUTH_REQUIRED = _env_flag("BAKO_FIREBASE_AUTH_REQUIRED", False)
# This panel is a separate local-development aid for verifying an existing
# Firebase account. It never grants dashboard access and is off by default.
FIREBASE_AUTH_DEV_PANEL = _env_flag("BAKO_FIREBASE_AUTH_DEV_PANEL", False)
# Keep parser diagnostics and timing calculations active, but hide the
# technical worksheet report from the normal Executive Overview interface.
SHOW_TECHNICAL_VALIDATION_DETAILS = False
# Keep workbook quality detection and reports active, but temporarily hide
# the end-user warning/expander until the team is ready to surface them again.
SHOW_DATA_QUALITY_UI = False
# Keep chart timing collection active, but hide the technical timing table
# from the everyday dashboard interface.
SHOW_PERFORMANCE_UI = False
# Keep worksheet validation reports available internally, but hide the
# technical diagnostics dataframe from the user-facing Inventory Explorer.
SHOW_DATA_EXPLORER_DIAGNOSTICS = False
# Purchasing and Loads charts are temporarily hidden while the business
# meaning and validity of the POs and Loads per Week worksheets are confirmed.
# The chart functions and parsed source data remain available for restoration.
SHOW_PURCHASING_LOAD_CHARTS = False
# Keep the Purchasing & Loads parsers and page implementation available, but
# temporarily remove the page from the employee-facing navigation.
SHOW_PURCHASING_LOADS = False
X_BLOCKING_SCENARIO_QUANTITY_KEY = "x_blocking_scenario_quantity"
X_BLOCKING_SCENARIO_SLIDER_KEY = "x_blocking_scenario_slider"
X_BLOCKING_SCENARIO_EXACT_KEY = "x_blocking_scenario_exact"
LOGGER.info(
    "snapshot.config app_path=%s enabled=%s namespace=%s firebase_project=%s",
    Path(__file__).resolve(),
    ENABLE_DEV_FIRESTORE_SNAPSHOTS,
    DEV_SNAPSHOT_NAMESPACE,
    runtime_setting("FIREBASE_PROJECT_ID", "bako-sweet"),
)
LOGGER.info(
    "auth.config required=%s enabled=%s email_verification=%s",
    FIREBASE_AUTH_REQUIRED,
    FIREBASE_AUTH_CONFIG.enabled,
    FIREBASE_AUTH_CONFIG.require_email_verification,
)
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


def _sync_x_blocking_from_slider() -> None:
    """Use the slider's 100-lb position as the canonical scenario quantity."""
    st.session_state[X_BLOCKING_SCENARIO_QUANTITY_KEY] = int(
        st.session_state.get(X_BLOCKING_SCENARIO_SLIDER_KEY, 0)
    )


def _sync_x_blocking_from_exact_input() -> None:
    """Preserve an exact whole-pound scenario quantity entered by the user."""
    st.session_state[X_BLOCKING_SCENARIO_QUANTITY_KEY] = int(
        st.session_state.get(X_BLOCKING_SCENARIO_EXACT_KEY, 0)
    )


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


def _explorer_text(value: object) -> str:
    """Return a safe, display-ready value for Inventory Explorer suggestions."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def inventory_explorer_suggestions(frame: pd.DataFrame, query: str, limit: int = 6) -> list[dict[str, object]]:
    """Build workbook-backed autocomplete suggestions from an already filtered frame.

    This operates only on the in-memory records already used by the Explorer;
    it never reparses the workbook. Classification-wide suggestions are kept
    separate from product-level suggestions so the selected scope is explicit.
    """
    search = str(query or "").strip().casefold()
    if not search or frame is None or frame.empty:
        return []

    candidates: list[dict[str, object]] = []
    classifications = sorted(
        {
            _explorer_text(value)
            for value in frame.get("classification", pd.Series(dtype="object")).dropna().tolist()
            if _explorer_text(value)
        },
        key=str.casefold,
    )
    for classification in classifications:
        if search not in classification.casefold():
            continue
        candidates.append(
            {
                "label": f"{classification} — All products",
                "kind": "classification",
                "classification": classification,
                "score": 0 if classification.casefold().startswith(search) else 8,
            }
        )

    product_groups: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for _, row in frame.iterrows():
        product = _explorer_text(row.get("product"))
        variety = _explorer_text(row.get("variety"))
        classification = _explorer_text(row.get("classification"))
        size = _explorer_text(row.get("size"))
        searchable = " ".join(value for value in (product, variety, classification, size) if value)
        if not searchable or search not in searchable.casefold():
            continue
        key = (product.casefold(), variety.casefold(), classification.casefold(), size.casefold())
        product_groups.setdefault(
            key,
            {
                "product": product,
                "variety": variety,
                "classification": classification,
                "size": size,
            },
        )

    labels_seen = {str(item["label"]) for item in candidates}
    for item in product_groups.values():
        classification = str(item["classification"])
        variety = str(item["variety"])
        size = str(item["size"])
        product = str(item["product"])
        readable_parts = [part for part in (classification, variety) if part]
        if size:
            readable_parts.append(size)
        label = " · ".join(readable_parts) if readable_parts else product
        if label in labels_seen:
            label = f"{label} · {product}" if product else label
        labels_seen.add(label)
        searchable_values = [product, variety, classification, size]
        starts_with = any(value.casefold().startswith(search) for value in searchable_values if value)
        candidates.append(
            {
                "label": label,
                "kind": "product",
                "product": product,
                "variety": variety,
                "classification": classification,
                "size": size,
                "score": 2 if starts_with else 12,
            }
        )

    candidates.sort(key=lambda item: (int(item.get("score", 99)), str(item["label"]).casefold()))
    return candidates[: max(1, int(limit))]


def filter_inventory_explorer_suggestion(frame: pd.DataFrame, suggestion: dict[str, object]) -> pd.DataFrame:
    """Apply one selected autocomplete scope without changing global filters."""
    result = frame.copy()
    if result.empty:
        return result
    if suggestion.get("kind") == "classification":
        value = str(suggestion.get("classification", "")).casefold()
        return result[result["classification"].map(_explorer_text).str.casefold() == value]
    for column in ("product", "variety", "classification", "size"):
        expected = _explorer_text(suggestion.get(column))
        if expected and column in result:
            result = result[result[column].map(_explorer_text).str.casefold() == expected.casefold()]
    return result


def clear_inventory_explorer_selection() -> None:
    """Clear a prior suggestion when the search query changes."""
    st.session_state["inventory_explorer_suggestion"] = None


_INVENTORY_EXPLORER_AUTOCOMPLETE_HTML = """
<div class="cs-explorer-autocomplete">
  <label for="cs-explorer-search">Search verified records</label>
  <input id="cs-explorer-search" type="search" autocomplete="off"
         placeholder="Product, variety, classification, or size" />
  <div id="cs-explorer-suggestions" role="listbox" aria-label="Matching products"></div>
</div>
"""

_INVENTORY_EXPLORER_AUTOCOMPLETE_CSS = """
.cs-explorer-autocomplete { width: 100%; font-family: inherit; }
.cs-explorer-autocomplete label { display: block; color: var(--cs-text, #F5F7FA); font-size: .875rem; margin: 0 0 .35rem; }
.cs-explorer-autocomplete input { width: 100%; box-sizing: border-box; color: var(--cs-text, #F5F7FA); background: var(--cs-input, #1A3044); border: 1px solid var(--cs-border, #2C4558); border-radius: .45rem; padding: .55rem .7rem; font: inherit; outline: none; }
.cs-explorer-autocomplete input:focus { border-color: var(--cs-accent, #F47C35); box-shadow: 0 0 0 1px var(--cs-accent, #F47C35); }
#cs-explorer-suggestions { display: grid; gap: .25rem; margin-top: .35rem; }
#cs-explorer-suggestions button { width: 100%; display: flex; align-items: center; gap: .55rem; text-align: left; color: var(--cs-text, #F5F7FA); background: var(--cs-card, #13283A); border: 1px solid var(--cs-border, #2C4558); border-radius: .4rem; padding: .45rem .6rem; font: inherit; cursor: pointer; }
#cs-explorer-suggestions button:hover, #cs-explorer-suggestions button[aria-selected="true"] { color: var(--cs-heading, #F9FAFB); background: var(--cs-hover, #20384B); border-color: var(--cs-accent, #F47C35); }
#cs-explorer-suggestions button .kind { color: var(--cs-accent, #F47C35); font-size: .72rem; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; flex: 0 0 auto; }
#cs-explorer-suggestions .no-matches { color: var(--cs-muted, #BAC9D7); font-size: .82rem; padding: .35rem 0; }
#cs-explorer-suggestions[hidden] { display: none !important; }
"""

_INVENTORY_EXPLORER_AUTOCOMPLETE_JS = """
export default function(component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const input = parentElement.querySelector('#cs-explorer-search');
  const list = parentElement.querySelector('#cs-explorer-suggestions');
  if (!input || !list) return;

  const suggestions = Array.isArray(data?.suggestions) ? data.suggestions : [];
  let activeIndex = -1;
  let isOpen = Boolean(data?.open && String(data?.query ?? '').trim());
  input.value = data?.query ?? '';

  const close = (persist = true) => {
    isOpen = false;
    activeIndex = -1;
    list.replaceChildren();
    list.hidden = true;
    if (persist) setStateValue('open', false);
  };

  const choose = (item) => {
    input.value = item.label ?? '';
    close(false);
    setStateValue('open', false);
    setStateValue('query', data?.query ?? '');
    setTriggerValue('selected', item.id);
  };

  const render = () => {
    list.replaceChildren();
    list.hidden = !isOpen;
    if (!isOpen) return;
    if (data?.no_matches) {
      const empty = document.createElement('div');
      empty.className = 'no-matches';
      empty.textContent = 'No matching products';
      list.appendChild(empty);
      return;
    }
    suggestions.forEach((item, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(index === activeIndex));
      const kind = document.createElement('span');
      kind.className = 'kind';
      kind.textContent = item.kind === 'classification' ? 'All products' : 'Product';
      const label = document.createElement('span');
      label.textContent = item.label ?? '';
      button.append(kind, label);
      button.onclick = () => choose(item);
      list.appendChild(button);
    });
  };

  input.oninput = () => {
    isOpen = Boolean(input.value.trim());
    setStateValue('open', isOpen);
    setStateValue('query', input.value);
  };
  input.onfocus = () => {
    if (input.value.trim()) {
      isOpen = true;
      setStateValue('open', true);
      render();
    }
  };
  input.onblur = () => {
    window.setTimeout(() => {
      if (!parentElement.contains(document.activeElement)) close();
    }, 0);
  };
  list.onmousedown = (event) => {
    if (event.target.closest('button')) event.preventDefault();
  };
  input.onkeydown = (event) => {
    if (!suggestions.length) {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close();
      }
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeIndex = Math.min(activeIndex + 1, suggestions.length - 1);
      render();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      render();
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault();
      choose(suggestions[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };
  render();

  const handleDocumentPointerDown = (event) => {
    if (!parentElement.contains(event.target)) close();
  };
  document.addEventListener('pointerdown', handleDocumentPointerDown);
  return () => document.removeEventListener('pointerdown', handleDocumentPointerDown);
}
"""

try:
    _inventory_explorer_autocomplete = st.components.v2.component(
        "inventory_explorer_autocomplete",
        html=_INVENTORY_EXPLORER_AUTOCOMPLETE_HTML,
        css=_INVENTORY_EXPLORER_AUTOCOMPLETE_CSS,
        js=_INVENTORY_EXPLORER_AUTOCOMPLETE_JS,
    )
except AttributeError:
    _inventory_explorer_autocomplete = None


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


def _refresh_first_name(employee: FirebaseEmployee | None) -> str | None:
    """Return a trusted profile first name without inferring from email."""
    if employee is None:
        return None
    if employee.first_name:
        return employee.first_name
    display_name = str(employee.display_name or "").strip()
    if not display_name or display_name.casefold() == employee.email.casefold():
        return None
    first_name = display_name.split()[0].strip(" ,.;:()[]{}")
    return first_name or None


def _pacific_timestamp_label(value: object) -> str:
    if not value:
        return "Not available"
    try:
        return datetime.fromisoformat(str(value)).astimezone(ZoneInfo("America/Los_Angeles")).strftime(
            "%b %d, %Y %I:%M:%S %p %Z"
        )
    except (TypeError, ValueError):
        return str(value)


def _render_snapshot_publication_identity() -> None:
    """Show persisted publisher metadata without changing it on reads."""
    if not ENABLE_DEV_FIRESTORE_SNAPSHOTS:
        return
    publisher_name = st.session_state.get("dev_snapshot_publisher_name")
    published_at = st.session_state.get("dev_snapshot_published_at")
    if publisher_name and published_at:
        st.caption(f"Last refreshed by: **{publisher_name}**")
        st.caption(f"Last refreshed: {_pacific_timestamp_label(published_at)}")


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
    # A newly validated upload/local refresh starts a fresh status cycle; a
    # previous Firebase reload message must not linger on the replacement.
    st.session_state.workbook_refresh_status = ""
    st.session_state.workbook_refresh_error = ""
    st.session_state.pop("workbook_refresh_just_completed", None)
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


def _excel_upload_icon_markup() -> str:
    return (
        "<svg class='cs-excel-upload-icon' width='58' height='64' viewBox='0 0 58 64' "
        "aria-hidden='true'><path d='M8 2h29l13 13v47H8z' fill='#187C56'/>"
        "<path d='M37 2v13h13' fill='#0F6848'/><path d='M37 2 50 15H37z' fill='#5AB68D'/>"
        "<rect x='16' y='23' width='26' height='20' rx='1.5' fill='#F1FBF6'/>"
        "<path d='M16 29.7h26M16 36.4h26M24.7 23v20M33.4 23v20' fill='none' stroke='#187C56' stroke-width='1.5'/>"
        "<text x='29' y='56.5' text-anchor='middle' fill='#F1FBF6' font-family='Arial, sans-serif' font-size='7' font-weight='700' letter-spacing='.5'>XLSX</text></svg>"
    )


def _render_workbook_ready_card() -> None:
    """Render the compact ready state for an active validated workbook."""
    source_type = st.session_state.get("active_workbook_source_type")
    restored = source_type == "firestore_snapshot"
    source_label = "Restored from Firebase" if restored else "Loaded and validated"
    with st.container(key="workbook_ready_card"):
        with st.expander(f"✅ Workbook Ready  ·  {source_label}", expanded=True):
            filename = st.session_state.get("active_workbook_name") or "Workbook filename unavailable"
            refreshed_at = st.session_state.get("active_workbook_last_refresh")
            refreshed_by = str(st.session_state.get("active_workbook_last_refreshed_by") or "").strip()
            saved_at = st.session_state.get("active_workbook_last_saved")
            st.markdown(
                "<div class='cs-upload-feedback cs-upload-feedback--success'>"
                "<div class='cs-upload-feedback-icon'>✓</div>"
                "<div class='cs-upload-feedback-copy'>"
                "<div class='cs-upload-feedback-title'>Workbook Loaded Successfully</div>"
                "<div class='cs-upload-feedback-message'>Inventory data is ready for analysis.</div>"
                f"<div class='cs-upload-feedback-file'>{html.escape(str(filename))}</div>"
                "</div></div>",
                unsafe_allow_html=True,
            )
            refresh_identity_markup = (
                f"<div class='cs-workbook-refresh-time'>Last refreshed by {html.escape(refreshed_by)}</div>"
                if refreshed_by.casefold() not in {"", "team member"}
                else "<div class='cs-workbook-refresh-time'>Last refreshed</div>"
            )
            st.markdown(
                refresh_identity_markup
                + f"<div class='cs-workbook-refresh-time'>{html.escape(_timestamp_label(refreshed_at))}</div>"
                f"<div class='cs-workbook-save-time'>Last saved: "
                f"{html.escape(_timestamp_label(saved_at))}</div>",
                unsafe_allow_html=True,
            )
            refresh_status = st.session_state.get("workbook_refresh_status")
            refresh_just_completed = bool(st.session_state.pop("workbook_refresh_just_completed", False))
            if refresh_just_completed and refresh_status not in {"success", "error"}:
                # Preserve the visible confirmation even if another safe
                # restore path cleared the transient status during rerun.
                refresh_status = "success"
            if refresh_status == "refreshing":
                st.markdown(
                    "<div class='cs-workbook-refresh-feedback cs-workbook-refresh-feedback--loading'>Refreshing...</div>",
                    unsafe_allow_html=True,
                )
            elif refresh_status == "success":
                st.markdown(
                    "<div class='cs-workbook-refresh-feedback cs-workbook-refresh-feedback--success'>Refresh successful.</div>",
                    unsafe_allow_html=True,
                )
            elif refresh_status == "error":
                st.markdown(
                    "<div class='cs-workbook-refresh-feedback cs-workbook-refresh-feedback--error'>"
                    "Refresh failed. The previous validated workbook remains active.</div>",
                    unsafe_allow_html=True,
                )
            action_columns = st.columns(2 if restored else 1, gap="small")
            if restored:
                with action_columns[0]:
                    if st.button("Refresh Workbook", key="reload_saved_dashboard_data", width="stretch"):
                        st.session_state.reload_saved_data_requested = True
                        st.session_state.workbook_refresh_status = "refreshing"
                        st.rerun()
            replace_column = action_columns[-1]
            with replace_column:
                if st.button("Replace Workbook", key="replace_active_workbook", width="stretch"):
                    st.session_state.workbook_replace_open = True
                    st.rerun()
        # Keep this interaction entirely in the browser.  It changes only the
        # native expander's open state, so scrolling never reruns Streamlit or
        # touches workbook/Firebase state.
        st.html(
            """
            <script>
            (() => {
                const card = document.querySelector("div.st-key-workbook_ready_card");
                const details = card && card.querySelector("details");
                if (!card || !details) return;

                const cleanupKey = "__csWorkbookReadyScrollCleanup";
                if (typeof window[cleanupKey] === "function") window[cleanupKey]();

                const scrollTarget = document.querySelector("main.stMain") || window;
                const animationStart = 0;
                const animationEnd = 260;
                const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                let framePending = false;
                const detailsContent = details.querySelector('[data-testid="stExpanderDetails"]');
                if (!detailsContent) return;

                const getScrollPosition = () => scrollTarget === window
                    ? (window.scrollY || document.documentElement.scrollTop || 0)
                    : scrollTarget.scrollTop;
                // A refresh confirmation is inserted by a Streamlit rerun. On
                // that first frame, reveal the current natural height so the
                // new message cannot leave the action row clipped. Subsequent
                // scroll events immediately resume the normal interpolation.
                let revealAfterRefresh = Boolean(card.querySelector(".cs-workbook-refresh-feedback"));

                // Keep the native expander open so its header remains stable;
                // the details region itself is scroll-linked below.
                details.open = true;
                detailsContent.style.overflow = "hidden";
                detailsContent.style.willChange = "max-height, opacity";
                const measureExpandedHeight = () => {
                    const previousMaxHeight = detailsContent.style.maxHeight;
                    detailsContent.style.maxHeight = "none";
                    const naturalHeight = detailsContent.scrollHeight;
                    detailsContent.style.maxHeight = previousMaxHeight;
                    return naturalHeight;
                };
                let expandedHeight = measureExpandedHeight();

                const syncToScrollPosition = () => {
                    const scrollTop = getScrollPosition();
                    const rawProgress = 1 - ((scrollTop - animationStart) / (animationEnd - animationStart));
                    const progress = revealAfterRefresh
                        ? 1
                        : reducedMotion
                        ? (scrollTop <= animationStart ? 1 : 0)
                        : Math.max(0, Math.min(1, rawProgress));
                    revealAfterRefresh = false;
                    const eased = progress * progress * (3 - (2 * progress));
                    details.open = true;
                    detailsContent.style.maxHeight = `${Math.round(expandedHeight * eased)}px`;
                    detailsContent.style.opacity = `${eased}`;
                    detailsContent.style.pointerEvents = eased < 0.05 ? "none" : "";
                    detailsContent.setAttribute("aria-hidden", eased < 0.05 ? "true" : "false");
                    card.dataset.csWorkbookReadyState = progress <= 0 ? "collapsed" : progress >= 1 ? "expanded" : "transitioning";
                };

                const onResize = () => {
                    expandedHeight = measureExpandedHeight();
                    syncToScrollPosition();
                };
                let observerFramePending = false;
                const resizeObserver = typeof ResizeObserver === "function"
                    ? new ResizeObserver(() => {
                        if (observerFramePending) return;
                        observerFramePending = true;
                        window.requestAnimationFrame(() => {
                            observerFramePending = false;
                            revealAfterRefresh = true;
                            onResize();
                        });
                    })
                    : null;
                if (resizeObserver) {
                    // The inner block changes size when Streamlit inserts or
                    // removes status content; observing the constrained
                    // wrapper alone would not see those changes.
                    if (detailsContent.firstElementChild) {
                        resizeObserver.observe(detailsContent.firstElementChild);
                    }
                }

                const onScroll = () => {
                    if (framePending) return;
                    framePending = true;
                    window.requestAnimationFrame(() => {
                        framePending = false;
                        syncToScrollPosition();
                    });
                };

                scrollTarget.addEventListener("scroll", onScroll, { passive: true });
                window.addEventListener("resize", onResize, { passive: true });
                window[cleanupKey] = () => {
                    scrollTarget.removeEventListener("scroll", onScroll);
                    window.removeEventListener("resize", onResize);
                    if (resizeObserver) resizeObserver.disconnect();
                    if (window[cleanupKey]) delete window[cleanupKey];
                };
                syncToScrollPosition();
            })();
            </script>
            """,
            unsafe_allow_javascript=True,
        )


def _render_workbook_upload_panel(slot: object) -> tuple[object | None, object]:
    """Render the ready card or the existing native upload workflow.

    The keyed outer container stays mounted while ``content_slot`` can be
    replaced after restoration or validation in the same Streamlit run. This
    keeps the upload widget stable without forcing a rerun after file upload.
    """
    current_name = st.session_state.get("active_workbook_name")
    current_source = st.session_state.get("active_workbook_source_type")
    replace_open = bool(st.session_state.get("workbook_replace_open", False))
    with slot.container():
        with st.container(key="upload_panel"):
            content_slot = st.empty()
            active_source = current_source if current_source in {"upload", "local_link", "firestore_snapshot"} else None
            has_active_workbook = (
                bool(current_name)
                and active_source is not None
                and st.session_state.get("active_workbook_report") is not None
                and not st.session_state.get("active_workbook_stale", False)
            )
            if has_active_workbook and not replace_open:
                with content_slot.container():
                    _render_workbook_ready_card()
                return None, content_slot
            with content_slot.container():
                upload_state_class = "cs-upload-state--replace" if current_name else "cs-upload-state--choose"
                heading = "Replace Inventory Workbook" if current_name else "Upload Inventory Workbook"
                excel_upload_icon = _excel_upload_icon_markup()
                st.markdown(
                    "<div class='cs-upload-intro'>"
                    f"<div class='cs-upload-icon-wrap'>{excel_upload_icon}</div>"
                    "<div class='cs-upload-copy'>"
                    f"<div class='cs-upload-heading'>{heading}</div>"
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
                    disabled=(
                        FIREBASE_AUTH_CONFIG.enabled
                        and not (
                            session_employee(st.session_state)
                            and session_employee(st.session_state).can("publish_workbook")
                        )
                    ),
                )
                if current_name:
                    st.caption("The current validated workbook remains active until the replacement succeeds.")
                if ENABLE_LOCAL_WORKBOOK_DEMO:
                    linked = load_link()
                    if linked:
                        st.caption(f"Local synced workbook: **{linked.filename}**")
                    picker_col, refresh_col = st.columns(2, gap="small")
                    with picker_col:
                        if st.button("Select synced workbook", key="select_local_workbook"):
                            try:
                                selected_path = choose_workbook()
                                if selected_path is not None:
                                    save_link(
                                        LocalWorkbookLink(
                                            path=str(selected_path),
                                            filename=selected_path.name,
                                            selected_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                                        )
                                    )
                                    st.session_state.active_workbook_path = str(selected_path)
                                    st.session_state.local_link_pending = True
                                    st.session_state.local_link_picker_error = ""
                            except Exception as exc:
                                st.session_state.local_link_picker_error = str(exc)
                    with refresh_col:
                        if st.button("Refresh synced workbook", key="refresh_local_workbook"):
                            st.session_state.local_refresh_requested = True
                    picker_error = st.session_state.get("local_link_picker_error")
                    if picker_error:
                        st.warning(picker_error)
            return uploaded, content_slot


def dashboard_icon(name: str, *, size: int = 24) -> str:
    """Return one consistent inline outline icon for the presentation layer."""
    paths = {
        "warehouse": '<path d="M3 10.5 12 4l9 6.5v8.8a1.7 1.7 0 0 1-1.7 1.7H4.7A1.7 1.7 0 0 1 3 19.3z"/><path d="M7 21v-6h10v6M7 10h10M9 10v3m3-3v3m3-3v3"/>',
        "bars": '<path d="M4 20V10m5 10V4m5 16v-7m5 7V7"/><path d="M2 20h20"/>',
        "scale": '<path d="M12 4v16M7 20h10M5 7h14M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z"/>',
        "file": '<path d="M6 2.8h8l4 4v14.4H6z"/><path d="M14 2.8v4h4M9 12h6m-6 4h6"/>',
        "filter": '<path d="M3 5h18l-7 8v5l-4 2v-7z"/>',
        "calendar-days": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>',
        "calendar-range": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M8 15h2M14 15h2M8 19h2M14 19h2"/>',
        "sprout": '<path d="M7 20h10M12 20v-9"/><path d="M12 11c-1.8 0-3.5-.7-4.8-2S5 6 5 4c2 0 3.8.7 5.2 2S12 9 12 11Z"/><path d="M12 11c1.8 0 3.5-.7 4.8-2S19 6 19 4c-2 0-3.8.7-5.2 2S12 9 12 11Z"/>',
        "layers-2": '<path d="m12.83 2.18 7.5 4.23a1 1 0 0 1 0 1.74l-7.5 4.23a1.75 1.75 0 0 1-1.66 0l-7.5-4.23a1 1 0 0 1 0-1.74l7.5-4.23a1.75 1.75 0 0 1 1.66 0Z"/><path d="m5 12 7 4 7-4M5 17l7 4 7-4"/>',
        "scaling": '<path d="M21 3 3 21M3 7V3h4M17 3h4v4M21 17v4h-4M7 21H3v-4"/>',
    }
    path = paths.get(name, paths["file"])
    return f'<svg class="cs-inline-icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'


def render_sidebar_navigation(page_names: list[str], *, firebase_employee: FirebaseEmployee | None = None) -> str:
    """Render compact native buttons in the expanded sidebar."""
    icons = {
        "Executive Overview": ":material/dashboard:",
        "Inventory Timeline": ":material/timeline:",
        "Organic vs. Conventional": ":material/compare_arrows:",
        "Product Breakdown": ":material/inventory_2:",
        "X-Blocking Analysis": ":material/compare:",
        "Harvest & Sales": ":material/warehouse:",
        "Purchasing & Loads": ":material/local_shipping:",
        "Data Explorer & Quality": ":material/search:",
        "Settings": ":material/settings:",
    }
    with st.sidebar:
        with st.container(key="sidebar_branding"):
            st.image(str(SIDEBAR_LOGO_PATH), width=135, output_format="PNG")
            st.markdown(
                "<div class='cs-sidebar-company'>Bako Sweet</div>"
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
            if firebase_employee is not None:
                with st.expander(":material/account_circle: My Account · :green[●]", expanded=False):
                    if firebase_employee.display_name and firebase_employee.display_name != firebase_employee.email:
                        st.caption(firebase_employee.display_name)
                    st.text(firebase_employee.email)
                    st.caption(f"Role: {firebase_employee.role.title()}")
                    st.caption("✓ Email verified")
                    if st.button("Sign out", key="firebase_sign_out", width="stretch"):
                        clear_auth_session(st.session_state)
                        st.session_state.firebase_force_signout = True
                        st.rerun()
    return current


def render_kpi_cards(stored: float | None, demand: float | None, balance: float | None, unavailable_label: str) -> None:
    """Render the three executive metrics with compact operational accents."""
    stored_label = f"{stored:,.0f} lbs" if stored is not None else unavailable_label
    demand_label = f"{demand:,.0f} lbs" if demand is not None else unavailable_label
    balance_label = f"{balance:+,.0f} lbs" if balance is not None else unavailable_label
    balance_support = "Starting inventory − planned sales" if balance is not None else "Awaiting verified values"
    cards = [
        ("storage", "warehouse", "Starting Inventory", stored_label, "Beginning-of-week inventory"),
        ("demand", "bars", "This Week's Planned Sales", demand_label, "Current week's planned sales"),
        ("balance", "scale", "Inventory Less Planned Sales", balance_label, balance_support),
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


OPEN_METEO_TIMEOUT_SECONDS = 4
OPEN_METEO_ATTEMPTS = 2
OPEN_METEO_CACHE_SECONDS = 900
WEATHER_STALE_SECONDS = 21600


def _open_meteo_get(endpoint: str, *, params: dict[str, object], operation: str) -> requests.Response:
    """Call Open-Meteo directly so stale process proxy settings cannot break weather."""
    started = perf_counter()
    last_error: requests.RequestException | None = None
    for attempt in range(1, OPEN_METEO_ATTEMPTS + 1):
        session = requests.Session()
        # The dashboard calls a public HTTPS endpoint. Ignoring inherited
        # proxy variables avoids stale local proxy settings such as 127.0.0.1:9
        # while leaving the rest of the application's HTTP behavior unchanged.
        session.trust_env = False
        try:
            response = session.get(endpoint, params=params, timeout=OPEN_METEO_TIMEOUT_SECONDS)
            response.raise_for_status()
            LOGGER.info(
                "weather.%s succeeded status=%s attempt=%s elapsed_ms=%.1f",
                operation, response.status_code, attempt, (perf_counter() - started) * 1000,
            )
            return response
        except requests.RequestException as exc:
            last_error = exc
            LOGGER.warning(
                "weather.%s failed endpoint=%s attempt=%s elapsed_ms=%.1f error=%s",
                operation, endpoint, attempt, (perf_counter() - started) * 1000, exc,
            )
            if attempt < OPEN_METEO_ATTEMPTS:
                sleep(0.25)
        finally:
            session.close()
    assert last_error is not None
    raise last_error


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
    """Return live conditions, or a recent successful result when the provider is unavailable."""
    temperature_unit = "celsius" if temperature_unit == "celsius" else "fahrenheit"
    cache_key = (round(float(latitude), 4), round(float(longitude), 4))
    try:
        weather = dict(_get_weather_cached(float(latitude), float(longitude), temperature_unit))
        weather["is_cached"] = False
        st.session_state.setdefault("weather_last_success", {})[cache_key] = {
            "weather": dict(weather),
            "saved_at": epoch_time(),
        }
        return weather
    except (requests.RequestException, ValueError, TypeError) as exc:
        LOGGER.warning("weather.forecast_parse_failed latitude=%s longitude=%s error=%s", latitude, longitude, exc)
        recent = st.session_state.get("weather_last_success", {}).get(cache_key)
        if isinstance(recent, dict):
            age = max(0.0, epoch_time() - float(recent.get("saved_at", 0.0)))
            if age <= WEATHER_STALE_SECONDS and isinstance(recent.get("weather"), dict):
                stale = dict(recent["weather"])
                source_unit = stale.get("temperature_unit", "fahrenheit")
                if source_unit != temperature_unit:
                    source_temperature = float(stale["temperature"])
                    stale["temperature"] = (
                        (source_temperature - 32.0) * 5.0 / 9.0
                        if temperature_unit == "celsius"
                        else source_temperature * 9.0 / 5.0 + 32.0
                    )
                    stale["temperature_unit"] = temperature_unit
                stale["is_cached"] = True
                stale["cache_age_seconds"] = age
                LOGGER.info(
                    "weather.forecast_using_cached latitude=%s longitude=%s age_seconds=%.0f",
                    latitude, longitude, age,
                )
                return stale
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


def render_top_header() -> tuple[object | None, object]:
    """Render the compact header, weather card, and workbook status/uploader."""
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
                    condition_label = html.escape(str(weather["condition"]))
                    if weather.get("is_cached"):
                        condition_label = f"Cached · {condition_label}"
                    weather_markup = (
                        "<div class='cs-weather-widget'>"
                        f"<div class='cs-weather-icon'>{weather_icon_svg(weather.get('weather_code'), weather.get('is_day', True))}</div>"
                        "<div class='cs-weather-info'>"
                        f"<div class='cs-weather-location'>{html.escape(location_label(selected_location))}</div>"
                        f"<div class='cs-weather-temperature'>{weather['temperature']:.0f} {unit_label}</div>"
                        f"<div class='cs-weather-condition'>{condition_label}</div>"
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
                upload_panel_slot = st.empty()
                uploaded, upload_content_slot = _render_workbook_upload_panel(upload_panel_slot)
    return uploaded, upload_content_slot


if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True
else:
    st.session_state.dark_mode = True
if "timezone" not in st.session_state:
    st.session_state.timezone = "America/Los_Angeles"

apply_theme(True, HEADER_IMAGE_PATH)

# Deployment mode is fail-closed.  Streamlit Cloud does not execute the
# Windows launcher, so this gate verifies the values supplied through Secrets
# before any workbook, snapshot, or dashboard content is rendered.
if ENABLE_DEV_FIRESTORE_SNAPSHOTS and not FIREBASE_AUTH_REQUIRED:
    st.error(
        "Shared Firebase snapshot mode requires mandatory Firebase Authentication. "
        "Set BAKO_FIREBASE_AUTH_REQUIRED=true before exposing the dashboard."
    )
    st.stop()
if FIREBASE_AUTH_REQUIRED:
    if not FIREBASE_AUTH_CONFIG.enabled:
        st.error(
            "Firebase Authentication is required for this deployment, but it is not enabled. "
            "Configure BAKO_FIREBASE_AUTH_ENABLED=true in Streamlit Secrets."
        )
        st.stop()
    if not FIREBASE_AUTH_CONFIG.web_ready:
        st.error(
            "Firebase Authentication is required, but the Firebase Web App configuration is incomplete. "
            "Configure the Web API key, auth domain, App ID, and project ID in Streamlit Secrets."
        )
        st.stop()
    try:
        get_firebase_app()
    except Exception as exc:
        # Keep provider details and credential material out of the UI/logs.
        LOGGER.warning("auth.required_admin_init_failed error_type=%s", type(exc).__name__)
        st.error(
            "Firebase server authentication is unavailable. The deployment administrator must "
            "configure a valid Firebase Admin credential for project bako-sweet."
        )
        st.stop()

# Optional verification-only panel. It intentionally runs while mandatory
# authentication is disabled, so an existing Firebase user can request the
# official verification email without assigning a role or locking the local
# dashboard behind a login screen.
if FIREBASE_AUTH_DEV_PANEL and not FIREBASE_AUTH_CONFIG.enabled:
    if not FIREBASE_AUTH_CONFIG.web_ready:
        st.sidebar.warning(
            "Firebase verification panel is enabled, but the local Web App configuration "
            "is incomplete. Set BAKO_FIREBASE_WEB_API_KEY, BAKO_FIREBASE_AUTH_DOMAIN, "
            "and BAKO_FIREBASE_WEB_APP_ID."
        )
    else:
        with st.sidebar.expander("Firebase account verification (development)", expanded=True):
            st.caption(
                "Verification only. Dashboard login remains disabled; no role or administrator "
                "claim is assigned here."
            )
            render_firebase_auth_component(FIREBASE_AUTH_CONFIG, verification_only=True)

# Firebase authentication is deliberately opt-in. When enabled, this gate is
# evaluated before any workbook, snapshot, or dashboard data is rendered.
firebase_employee: FirebaseEmployee | None = None
auth_panel_slot = None
if FIREBASE_AUTH_CONFIG.enabled:
    if not FIREBASE_AUTH_CONFIG.web_ready:
        st.error(
            "Firebase Authentication is enabled, but its web configuration is incomplete. "
            "Ask the project administrator to configure the Firebase web app settings."
        )
        st.stop()
    # Resolve sign-out before reading the cached verified identity. The
    # sidebar sets this flag while the auth component is not rendered, so the
    # server session must be cleared before deciding which branch to render.
    force_signout = bool(st.session_state.pop("firebase_force_signout", False))
    if force_signout:
        clear_auth_session(st.session_state)
        st.session_state.pop("firebase_auth_error", None)

    # Do not render the full auth component once a verified session exists.
    # Removing it from the Streamlit tree prevents the login form from
    # occupying space or reappearing during ordinary reruns/tab navigation.
    firebase_employee = session_employee(st.session_state)
    auth_event: dict[str, Any] = {}
    if firebase_employee is None:
        signup_approval = st.session_state.get("firebase_signup_approval")
        signup_approval_active = isinstance(signup_approval, dict)
        signup_profile = st.session_state.get("firebase_signup_profile")
        # Render the keyed auth surface directly so the auth-scoped theme can
        # remove the 1rem gap Streamlit places before the surface's first child.
        auth_panel_slot = st.container(
            key="firebase_auth_page",
            width="stretch",
            height="content",
            horizontal_alignment="center",
        )
        with auth_panel_slot:
            auth_event = render_firebase_auth_component(
                FIREBASE_AUTH_CONFIG,
                signup_approved=signup_approval_active,
                signup_profile=signup_profile if isinstance(signup_profile, dict) else None,
                signup_error=str(st.session_state.get("firebase_signup_error", "")),
                force_signout=force_signout,
            )
    signup_request = auth_event.get("signup_request")
    if isinstance(signup_request, dict):
        request_email = str(signup_request.get("email", "")).strip().casefold()
        request_code = str(signup_request.get("invite_code", "")).strip()
        try:
            request_first_name = normalize_person_name(
                str(signup_request.get("first_name", "")),
                field_name="First name",
            )
            request_last_name = normalize_person_name(
                str(signup_request.get("last_name", "")),
                field_name="Last name",
            )
        except FirebaseAuthError as exc:
            st.session_state.firebase_signup_error = str(exc)
            st.rerun()
        request_digest = invitation_digest(request_code)
        prior_request = st.session_state.get("firebase_last_signup_request")
        approval_matches = (
            isinstance(signup_approval, dict)
            and signup_approval.get("email") == request_email
            and signup_approval.get("invite_digest") == request_digest
        )
        if not approval_matches and prior_request != request_digest:
            st.session_state.pop("firebase_signup_error", None)
            try:
                invitation = validate_invitation(request_email, request_code)
                if invitation is None:
                    raise FirebaseAuthError("That invitation is invalid, expired, or already used.")
                st.session_state.firebase_signup_approval = {
                    "email": request_email,
                    "invite_digest": request_digest,
                }
                st.session_state.firebase_signup_profile = {
                    "first_name": request_first_name,
                    "last_name": request_last_name,
                }
                st.session_state.firebase_last_signup_request = request_digest
                st.rerun()
            except FirebaseAuthError as exc:
                st.session_state.firebase_last_signup_request = request_digest
                st.session_state.firebase_signup_error = str(exc)
                st.rerun()
    signup_result = auth_event.get("signup_result")
    if isinstance(signup_result, dict):
        signup_token = str(signup_result.get("id_token", "")).strip()
        signup_code = str(signup_result.get("invite_code", "")).strip()
        result_digest = token_digest(signup_token) if signup_token else ""
        if result_digest and result_digest == st.session_state.get("firebase_last_signup_result"):
            signup_result = None
        else:
            if result_digest:
                st.session_state.firebase_last_signup_result = result_digest
    if isinstance(signup_result, dict):
        approval = st.session_state.get("firebase_signup_approval")
        profile = st.session_state.get("firebase_signup_profile")
        if not signup_token or not signup_code or not isinstance(approval, dict) or not isinstance(profile, dict) or invitation_digest(signup_code) != approval.get("invite_digest"):
            st.session_state.pop("firebase_signup_approval", None)
            st.session_state.firebase_signup_error = "The invitation approval expired. Start registration again with a valid invitation."
            st.rerun()
        try:
            approve_new_account(
                signup_token,
                signup_code,
                first_name=str(profile.get("first_name", "")),
                last_name=str(profile.get("last_name", "")),
                config=FIREBASE_AUTH_CONFIG,
            )
            st.session_state.pop("firebase_signup_approval", None)
            st.session_state.pop("firebase_signup_profile", None)
            st.session_state.pop("firebase_signup_error", None)
            st.info("Account created. Verify your Firebase email, then sign in.")
            st.stop()
        except FirebaseAuthError as exc:
            # Keep the same approval/profile in session so a profile or role
            # update failure can be retried for the existing Firebase UID
            # without creating another account.
            st.session_state.pop("firebase_last_signup_result", None)
            st.session_state.firebase_signup_error = str(exc)
            st.rerun()
    if auth_event.get("logout"):
        clear_auth_session(st.session_state)
        st.session_state.pop("firebase_auth_error", None)
        # Remove the authenticated dashboard tree before the next render.
        # The following run will show only the full-screen sign-in surface.
        st.rerun()
    token = auth_event.get("id_token")
    if token:
        auth_started = perf_counter()
        LOGGER.info("auth.id_token_received stage=server_verification")
        try:
            firebase_employee = verify_id_token(str(token), config=FIREBASE_AUTH_CONFIG)
            remember_employee(st.session_state, firebase_employee)
            st.session_state.pop("firebase_auth_error", None)
            LOGGER.info(
                "auth.token_verified role=%s elapsed_ms=%.1f",
                firebase_employee.role,
                (perf_counter() - auth_started) * 1000,
            )
            # The auth component was rendered earlier in this run while the
            # identity was still unknown. Start a clean rerun now so the
            # authentication surface is removed before any sidebar or
            # protected dashboard elements are rendered.
            if auth_panel_slot is not None:
                auth_panel_slot.empty()
            st.rerun()
        except FirebaseAuthError as exc:
            clear_auth_session(st.session_state)
            st.session_state.firebase_auth_error = str(exc)
            LOGGER.warning(
                "auth.token_verification_failed category=%s elapsed_ms=%.1f",
                type(exc).__name__,
                (perf_counter() - auth_started) * 1000,
            )
            # The component was displaying its restoring overlay while this
            # event was processed. Re-render it with a durable, safe error so
            # a failed verification cannot leave the browser dimmed forever.
            if auth_panel_slot is not None:
                auth_panel_slot.empty()
            st.rerun()
    if firebase_employee is None:
        firebase_employee = session_employee(st.session_state)
    if firebase_employee is None:
        st.stop()
    require_permission(firebase_employee, "view_dashboard")
    if auth_panel_slot is not None:
        # The component was rendered before its ID-token event was processed.
        # Clear that same slot so the login form cannot remain above the hero
        # during the authenticated run.
        auth_panel_slot.empty()

if ENABLE_LOCAL_WORKBOOK_DEMO:
    saved_local_link = load_link()
    if saved_local_link:
        # Keep the reference in session state, but do not trust it as loaded
        # data until the file is read and validated in this process.
        st.session_state.active_workbook_path = saved_local_link.path
previous_report = st.session_state.get("active_workbook_report")
previous_name = st.session_state.get("active_workbook_name")
previous_content = st.session_state.get("active_workbook_content")
workbook_report = None
load_error = None
uploaded = None
refresh_clicked = bool(st.session_state.pop("local_refresh_requested", False)) if ENABLE_LOCAL_WORKBOOK_DEMO else False
reload_saved_data_requested = bool(st.session_state.pop("reload_saved_data_requested", False))

def _activate_restored_snapshot(restored_snapshot, *, refreshed_at: str | None = None) -> None:
    """Make a verified Firestore snapshot the active dashboard dataset."""
    completed_at = refreshed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    st.session_state.active_workbook_report = restored_snapshot.report
    st.session_state.active_workbook_name = restored_snapshot.source.filename
    st.session_state.active_workbook_id = restored_snapshot.source.source_checksum
    st.session_state.active_workbook_source_type = "firestore_snapshot"
    st.session_state.active_workbook_report_schema = REPORT_SCHEMA_VERSION
    st.session_state.active_workbook_path = ""
    st.session_state.active_workbook_content = None
    st.session_state.active_workbook_last_refresh = completed_at
    st.session_state.active_workbook_last_saved = restored_snapshot.published_at.isoformat()
    st.session_state.active_workbook_stale = False
    st.session_state.dev_snapshot_id = restored_snapshot.snapshot_id
    st.session_state.dev_snapshot_published_at = restored_snapshot.published_at.isoformat()
    st.session_state.dev_snapshot_restore_error = ""
    st.session_state.dev_snapshot_publish_error = ""
    st.session_state.dev_snapshot_saved_notice = ""
    st.session_state.dev_snapshot_publisher_name = restored_snapshot.source.publisher_display_name or ""
    st.session_state.workbook_replace_open = False


def _apply_shared_refresh_metadata(snapshot_id: str) -> None:
    """Load the authenticated user's shared refresh identity for the active snapshot."""
    st.session_state.active_workbook_last_refreshed_by = ""
    if not ENABLE_DEV_FIRESTORE_SNAPSHOTS:
        return
    try:
        metadata = read_snapshot_refresh_metadata(
            snapshot_id=snapshot_id,
            namespace=DEV_SNAPSHOT_NAMESPACE,
        )
    except Exception:
        LOGGER.exception("dashboard_snapshot.refresh_metadata_read_failed snapshot_id=%s", snapshot_id)
        return
    if not metadata:
        return
    st.session_state.active_workbook_last_refreshed_by = str(metadata.get("first_name") or "")
    refreshed_at = metadata.get("refreshed_at")
    if isinstance(refreshed_at, datetime):
        st.session_state.active_workbook_last_refresh = refreshed_at.isoformat()
    elif isinstance(refreshed_at, str) and refreshed_at.strip():
        st.session_state.active_workbook_last_refresh = refreshed_at.strip()

# Restore the saved snapshot before rendering the hero upload component.  This
# lets the first render choose exactly one state (ready card or native uploader)
# instead of briefly mounting the uploader and replacing it later in the run.
if ENABLE_DEV_FIRESTORE_SNAPSHOTS and not refresh_clicked and not reload_saved_data_requested and previous_report is None:
    snapshot_restore_started = perf_counter()
    try:
        restored_snapshot = restore_dashboard_snapshot(namespace=DEV_SNAPSHOT_NAMESPACE)
        if restored_snapshot is not None:
            workbook_report = restored_snapshot.report
            _activate_restored_snapshot(restored_snapshot)
            _apply_shared_refresh_metadata(restored_snapshot.snapshot_id)
            if st.session_state.get("workbook_refresh_status") not in {"refreshing", "success", "error"}:
                st.session_state.workbook_refresh_status = ""
                st.session_state.workbook_refresh_error = ""
            LOGGER.info(
                "dashboard_snapshot.restore_success namespace=%s snapshot_id=%s frames=%d rows=%d elapsed_ms=%.1f",
                DEV_SNAPSHOT_NAMESPACE,
                restored_snapshot.snapshot_id,
                int(restored_snapshot.stats.get("frame_count", 0)),
                int(restored_snapshot.stats.get("row_count", 0)),
                (perf_counter() - snapshot_restore_started) * 1000,
            )
    except Exception as exc:
        LOGGER.exception(
            "dashboard_snapshot_restore_failed elapsed_ms=%.1f",
            (perf_counter() - snapshot_restore_started) * 1000,
        )
        st.session_state.dev_snapshot_restore_error = str(exc)

upload_panel_started_ready = bool(
    st.session_state.get("active_workbook_name")
    and st.session_state.get("active_workbook_source_type")
    and st.session_state.get("active_workbook_report") is not None
    and not st.session_state.get("workbook_replace_open", False)
)
uploaded, upload_content_slot = render_top_header()

# A requested saved-data refresh is intentionally performed after the hero has
# rendered once, so the user sees the compact "Refreshing..." state before the
# existing Firebase restore call completes. The next rerun then shows the
# success/error result and the corresponding timestamp.
if ENABLE_DEV_FIRESTORE_SNAPSHOTS and reload_saved_data_requested and not refresh_clicked:
    snapshot_restore_started = perf_counter()
    try:
        restored_snapshot = restore_dashboard_snapshot(namespace=DEV_SNAPSHOT_NAMESPACE)
        if restored_snapshot is None:
            raise ValueError("No saved dashboard snapshot is available.")
        _activate_restored_snapshot(
            restored_snapshot,
            refreshed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if firebase_employee is not None:
            try:
                refresh_metadata = record_snapshot_refresh(
                    snapshot_id=restored_snapshot.snapshot_id,
                    workbook_id=restored_snapshot.source.workbook_id,
                    user_uid=firebase_employee.uid,
                    first_name=_refresh_first_name(firebase_employee) or "Team member",
                    display_name=firebase_employee.display_name,
                    namespace=DEV_SNAPSHOT_NAMESPACE,
                )
                st.session_state.active_workbook_last_refreshed_by = str(
                    refresh_metadata.get("first_name") or ""
                )
                server_refreshed_at = refresh_metadata.get("refreshed_at")
                if isinstance(server_refreshed_at, datetime):
                    st.session_state.active_workbook_last_refresh = server_refreshed_at.isoformat()
            except Exception:
                # The workbook remains safely restored, but do not claim a
                # new shared identity when the metadata write was rejected.
                LOGGER.exception(
                    "dashboard_snapshot.refresh_metadata_write_failed snapshot_id=%s",
                    restored_snapshot.snapshot_id,
                )
                _apply_shared_refresh_metadata(restored_snapshot.snapshot_id)
        else:
            _apply_shared_refresh_metadata(restored_snapshot.snapshot_id)
        st.session_state.workbook_refresh_status = "success"
        st.session_state.workbook_refresh_error = ""
        st.session_state.workbook_refresh_just_completed = True
        LOGGER.info(
            "dashboard_snapshot.refresh_success namespace=%s snapshot_id=%s elapsed_ms=%.1f",
            DEV_SNAPSHOT_NAMESPACE,
            restored_snapshot.snapshot_id,
            (perf_counter() - snapshot_restore_started) * 1000,
        )
    except Exception as exc:
        st.session_state.workbook_refresh_status = "error"
        st.session_state.workbook_refresh_error = str(exc)
        st.session_state.dev_snapshot_restore_error = str(exc)
        LOGGER.exception(
            "dashboard_snapshot.refresh_failed namespace=%s elapsed_ms=%.1f",
            DEV_SNAPSHOT_NAMESPACE,
            (perf_counter() - snapshot_restore_started) * 1000,
        )
    st.rerun()

def parse_and_activate(
    content: bytes,
    *,
    name: str,
    source_type: str,
    path: Path | None = None,
    file_stat: object | None = None,
    force_refresh: bool = False,
):
    publisher = firebase_employee
    if FIREBASE_AUTH_CONFIG.enabled:
        require_permission(publisher, "publish_workbook")
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
    # A validated replacement returns the compact ready state. Failed
    # replacements leave this flag set so the native uploader remains open.
    st.session_state.workbook_replace_open = False
    if ENABLE_DEV_FIRESTORE_SNAPSHOTS:
        snapshot_started = perf_counter()
        try:
            LOGGER.info(
                "dashboard_snapshot.publish_attempt namespace=%s source_type=%s content_id=%s",
                DEV_SNAPSHOT_NAMESPACE,
                source_type,
                content_id[:12],
            )
            prior_snapshot_version = active_snapshot_version(namespace=DEV_SNAPSHOT_NAMESPACE)
            published = publish_dashboard_snapshot(
                report,
                SnapshotSource(
                    workbook_id=f"{DEV_SNAPSHOT_NAMESPACE}:{content_id[:24]}",
                    filename=name,
                    source_version=content_id,
                    source_checksum=content_id,
                    publisher_uid=publisher.uid if publisher is not None else None,
                    publisher_display_name=publisher.display_name if publisher is not None else None,
                ),
                namespace=DEV_SNAPSHOT_NAMESPACE,
                expected_active_version=prior_snapshot_version,
            )
            st.session_state.dev_snapshot_id = published.snapshot_id
            st.session_state.dev_snapshot_published_at = published.published_at.isoformat()
            # For Firebase-backed uploads, Last saved is the server-side
            # publication time—not the time the user clicked Refresh.
            st.session_state.active_workbook_last_saved = published.published_at.isoformat()
            st.session_state.dev_snapshot_publish_error = ""
            st.session_state.dev_snapshot_restore_error = ""
            st.session_state.dev_snapshot_saved_notice = published.published_at.isoformat()
            st.session_state.dev_snapshot_publisher_name = published.source.publisher_display_name or ""
            LOGGER.info(
                "dashboard_snapshot.publish_success namespace=%s snapshot_id=%s frames=%d rows=%d elapsed_ms=%.1f",
                DEV_SNAPSHOT_NAMESPACE,
                published.snapshot_id,
                int(published.stats.get("frame_count", 0)),
                int(published.stats.get("row_count", 0)),
                (perf_counter() - snapshot_started) * 1000,
            )
        except Exception as exc:
            # A Firebase publication failure must not discard a locally
            # validated workbook that is already safe to display.
            LOGGER.exception(
                "dashboard_snapshot_publish_failed filename=%s elapsed_ms=%.1f",
                name,
                (perf_counter() - snapshot_started) * 1000,
            )
            st.session_state.dev_snapshot_publish_error = str(exc)
            st.session_state.dev_snapshot_saved_notice = ""
    return report


if ENABLE_LOCAL_WORKBOOK_DEMO and st.session_state.get("active_workbook_path"):
    local_path = st.session_state.get("active_workbook_path")
    local_path_pending = bool(st.session_state.pop("local_link_pending", False))
    local_path_loaded = st.session_state.get("local_link_loaded_path")
    should_load_saved = previous_report is None and local_path_loaded != local_path
    if local_path_pending or should_load_saved:
        try:
            linked_path, linked_content, linked_stat = _read_linked_workbook(local_path)
            workbook_report = parse_and_activate(
                linked_content,
                name=linked_path.name,
                source_type="local_link",
                path=linked_path,
                file_stat=linked_stat,
                force_refresh=True,
            )
            st.session_state.local_link_loaded_path = str(linked_path)
            st.session_state.local_link_picker_error = ""
        except Exception as exc:
            # Keep any prior verified report active; the normal error banner
            # explains that the synchronized file is unavailable or invalid.
            load_error = str(exc)
            LOGGER.exception("excel.local_link_load_failed path=%s", local_path)
            st.session_state.active_workbook_error = load_error
            if previous_report is not None:
                st.session_state.active_workbook_stale = True
                workbook_report = previous_report


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
        st.session_state.workbook_replace_open = False
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
                # The current run already has the verified report and session
                # metadata. Do not force a second rerun while Streamlit is
                # still finalizing the file-uploader event; that extra rerun
                # can interrupt the uploader WebSocket and surface a false
                # "Connection lost" state even when parsing succeeded.
            except Exception as exc:
                LOGGER.exception("excel.upload_failed filename=%s", uploaded.name)
                load_error = str(exc)
                # Keep the last verified workbook visible, but label it as
                # stale so a failed replacement is never mistaken for the
                # newly selected file.
                st.session_state.active_workbook_error = load_error
                st.session_state.dev_snapshot_saved_notice = ""
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
# The initial upload form is rendered before restoration/upload processing so
# replace its content with the ready card once a validated source is known.
if (
    workbook_report is not None
    and active_source in {"upload", "local_link", "firestore_snapshot"}
    and not load_error
    and not upload_panel_started_ready
):
    upload_content_slot.empty()
    with upload_content_slot.container():
        _render_workbook_ready_card()
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
    if ENABLE_DEV_FIRESTORE_SNAPSHOTS and st.session_state.get("dev_snapshot_saved_notice"):
        st.success("Dashboard data successfully saved to Firebase.")
        _render_snapshot_publication_identity()
if ENABLE_DEV_FIRESTORE_SNAPSHOTS:
    restore_error = st.session_state.get("dev_snapshot_restore_error")
    publish_error = st.session_state.get("dev_snapshot_publish_error")
    if restore_error:
        st.warning(f"The saved development snapshot could not be restored: {restore_error}")
    if publish_error:
        st.warning(f"The workbook loaded locally, but the development snapshot was not published: {publish_error}")

# Sidebar navigation callbacks update this value before the rerun begins. Use
# it to suppress analysis-only controls while Settings is being rendered,
# without changing the selected worksheet or any calculations.
settings_page_active = st.session_state.get("sidebar_page", "Executive Overview") == "Settings"
data_preparation_started = perf_counter()

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
        preferred_sheet = workbook_report.preferred_sheet if workbook_report.preferred_sheet in sheet_options else sheet_options[0]
        selected_sheet = st.session_state.get("primary_inventory_worksheet", preferred_sheet)
        if selected_sheet not in sheet_options:
            selected_sheet = preferred_sheet
        st.session_state.primary_inventory_worksheet = selected_sheet
        if not settings_page_active:
            selected_sheet = st.selectbox(
                "Primary inventory worksheet",
                sheet_options,
                index=sheet_options.index(selected_sheet),
                key="primary_inventory_worksheet",
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
    elif active_source == "firestore_snapshot":
        source_status = "Firebase development snapshot · verified data"
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
filter_signature = (
    f"{active_source or 'demo'}::{getattr(workbook_report, 'content_id', 'demo')}::"
    f"{source_sheet or 'demo'}"
)
filter_keys = {
    "year": "filter_year", "week": "filter_week", "variety": "filter_variety",
    "size": "filter_size", "classification": "filter_classification",
    "product": "filter_product", "packaging_type": "filter_packaging_type",
}
if st.session_state.get("filter_signature") != filter_signature:
    for key in filter_keys.values():
        st.session_state[key] = []
    st.session_state["filter_classification_segment"] = "All"
    st.session_state.filter_signature = filter_signature
for key in filter_keys.values():
    st.session_state.setdefault(key, [])

# Streamlit can retain widget state while a session reconnects or while a
# restored snapshot replaces an uploaded workbook.  Keep valid selections,
# but discard values that are no longer present in the active dataset.  An
# empty selection is the canonical representation of "All" for every
# multiselect dimension.
filter_value_options = {
    "year": set(year_options),
    "week": set(week_options),
    "variety": set(options(df, "variety")),
    "size": set(options(df, "size")),
    "product": set(),
}
for dimension, key in filter_keys.items():
    if dimension not in filter_value_options:
        continue
    selected_values = st.session_state.get(key, []) or []
    valid_values = filter_value_options[dimension]
    st.session_state[key] = [value for value in selected_values if value in valid_values]


def sync_classification_filter() -> None:
    choice = st.session_state.get("filter_classification_segment", "All")
    st.session_state[filter_keys["classification"]] = [] if choice == "All" else [choice]


if "filter_classification_segment" not in st.session_state:
    existing_classification = st.session_state[filter_keys["classification"]]
    st.session_state["filter_classification_segment"] = (
        existing_classification[0] if len(existing_classification) == 1 else "All"
    )
classification_choice = st.session_state.get("filter_classification_segment", "All")
if classification_choice not in {"All", "Organic", "Conventional"}:
    classification_choice = "All"
    st.session_state["filter_classification_segment"] = classification_choice
st.session_state[filter_keys["classification"]] = (
    [] if classification_choice == "All" else [classification_choice]
)

# Packaging is only useful as a visible filter when the loaded data contains
# more than one valid option. Keep a hidden/stale selection from affecting
# results when the control is not rendered.
packaging_options = options(df, "packaging_type")
show_packaging_filter = len(packaging_options) >= 2
if not show_packaging_filter:
    st.session_state[filter_keys["packaging_type"]] = []

# Product remains part of the canonical filter schema for search and explorer
# behavior, but the standalone Product dropdown is intentionally removed from
# the main Filters panel. Clear any value left by an earlier app version so it
# cannot act as an invisible filter.
st.session_state[filter_keys["product"]] = []

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
LOGGER.info(
    "dashboard.data_preparation source=%s rows=%d filtered_rows=%d elapsed_ms=%.1f",
    active_source or "demo",
    len(df),
    len(filtered),
    (perf_counter() - data_preparation_started) * 1000,
)

# Resolve the selected page before rendering shared page content so Settings
# can omit the KPI row during the same Streamlit run as its navigation click.
harvest_view_available = bool(workbook_report and frame_for(workbook_report, "harvest_sales")[1] is not None and not frame_for(workbook_report, "harvest_sales")[1].empty)
purchasing_view_available = bool(
    SHOW_PURCHASING_LOADS
    and workbook_report
    and frame_for(workbook_report, "purchasing_loads")[1] is not None
    and not frame_for(workbook_report, "purchasing_loads")[1].empty
)
navigation_pages = ["Executive Overview", "Inventory Timeline", "Product Breakdown", "X-Blocking Analysis"]
if harvest_view_available:
    navigation_pages.append("Harvest & Sales")
if purchasing_view_available:
    navigation_pages.append("Purchasing & Loads")
navigation_pages.append("Data Explorer & Quality")
active_page = render_sidebar_navigation(navigation_pages, firebase_employee=firebase_employee)

# This warning belongs only to pages that actually render the shared
# inventory-filter dataset. X-Blocking and Settings use independent layouts
# and must not inherit a contradictory warning from the primary worksheet.
FILTER_WARNING_PAGES = {
    "Executive Overview",
    "Inventory Timeline",
    "Organic vs. Conventional",
    "Product Breakdown",
    "Harvest & Sales",
    "Purchasing & Loads",
    "Data Explorer & Quality",
}
if active_page in FILTER_WARNING_PAGES and not df.empty and filtered.empty:
    st.warning("No records match the selected filters. Blank filters mean All; use Reset filters to restore the complete verified dataset.", icon="⚠️")

# Demo Mode belongs directly beneath the hero/header so first-time users see
# that the dashboard is using sample data before the KPI and filter sections.
# Keep the existing conditional and markup unchanged; real workbook data hides
# this notice automatically because ``workbook_report`` is populated.
if active_page == "Executive Overview" and workbook_report is None:
    st.markdown(
        "<div class='cs-demo-mode'>"
        "<div class='cs-demo-mode-icon' aria-hidden='true'>i</div>"
        "<div class='cs-demo-mode-copy'>"
        "<div class='cs-demo-mode-title'>Demo Mode</div>"
        "<div class='cs-demo-mode-message'>You're viewing sample inventory data. Upload your Excel workbook to see your company's actual data.</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

# The KPI row is shared by the dashboard pages, but Settings should start
# directly with its own controls. Keep the shared renderer and calculations
# intact while skipping only its visible output on the Settings page.
if active_page not in {"Settings", "X-Blocking Analysis"}:
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
if not settings_page_active and workbook_report is not None and source_sheet == workbook_report.semantic_sheets.get("inventory") and not filtered.empty:
    model_week = ", ".join(options(filtered, "week")) or "selected week"
    contribution = pd.to_numeric(filtered.get("contribution_margin"), errors="coerce").sum(min_count=1) if "contribution_margin" in filtered else None
    upcoming = pd.to_numeric(filtered.get("upcoming_demand_lbs"), errors="coerce").sum(min_count=1) if "upcoming_demand_lbs" in filtered else None
    st.caption(f"Current Week Model supplied totals · Week {model_week}")
    cm_col, upcoming_col = st.columns(2)
    with cm_col:
        with st.container(key="contribution_metric"):
            st.metric("Weekly Contribution Margin", f"${contribution:,.0f}" if pd.notna(contribution) else "Not available from selected worksheet")
    with upcoming_col:
        with st.container(key="upcoming_planned_metric"):
            st.metric("Next 4 Weeks' Planned Sales", f"{upcoming:,.0f} lbs" if pd.notna(upcoming) else "Not available from selected worksheet")

if active_page not in {"Settings", "X-Blocking Analysis"}:
    with st.container(key="filters_panel", border=True):
        filter_header_left, filter_header_right = st.columns([4, 1], gap="small", vertical_alignment="center")
        with filter_header_left:
            st.markdown(
                f"<div class='cs-filter-heading'><span class='cs-filter-heading-icon'>{dashboard_icon('filter', size=32)}</span>"
                "<strong class='cs-filter-heading-title'>Filters</strong></div>",
                unsafe_allow_html=True,
            )
        with filter_header_right:
            def reset_filters() -> None:
                for key in filter_keys.values():
                    st.session_state[key] = []
                st.session_state["filter_classification_segment"] = "All"

            st.button(
                "Reset filters",
                icon=":material/refresh:",
                width="stretch",
                on_click=reset_filters,
                key="reset_filters_button",
            )

        filter_row = st.columns([0.9, 1.15, 1.05, 1.8, 0.9], gap="small", vertical_alignment="bottom")
        with filter_row[0]:
            with st.container(key="filter_tile_year"):
                st.markdown(f"<div class='cs-filter-control-label'>{dashboard_icon('calendar-days', size=26)}<strong>Year</strong></div>", unsafe_allow_html=True)
                st.multiselect("Year", year_options, key=filter_keys["year"], placeholder="All years", label_visibility="collapsed")
        with filter_row[1]:
            with st.container(key="filter_tile_week"):
                st.markdown(f"<div class='cs-filter-control-label'>{dashboard_icon('calendar-range', size=26)}<strong>Reporting week</strong></div>", unsafe_allow_html=True)
                st.multiselect("Reporting week", week_options, key=filter_keys["week"], placeholder="All weeks", label_visibility="collapsed")
        with filter_row[2]:
            with st.container(key="filter_tile_variety"):
                st.markdown(f"<div class='cs-filter-control-label'>{dashboard_icon('sprout', size=26)}<strong>Variety</strong></div>", unsafe_allow_html=True)
                st.multiselect("Variety", options(df, "variety"), key=filter_keys["variety"], placeholder="All varieties", label_visibility="collapsed")
        with filter_row[3]:
            with st.container(key="filter_tile_classification"):
                st.markdown(f"<div class='cs-filter-control-label'>{dashboard_icon('layers-2', size=26)}<strong>Organic / conventional</strong></div>", unsafe_allow_html=True)
                st.segmented_control(
                    "Organic / conventional",
                    ["All", "Organic", "Conventional"],
                    selection_mode="single",
                    key="filter_classification_segment",
                    on_change=sync_classification_filter,
                    label_visibility="collapsed",
                    width="stretch",
                )
        with filter_row[4]:
            with st.container(key="filter_tile_size"):
                st.markdown(f"<div class='cs-filter-control-label'>{dashboard_icon('scaling', size=26)}<strong>Size</strong></div>", unsafe_allow_html=True)
                st.multiselect("Size", options(df, "size"), key=filter_keys["size"], placeholder="All sizes", label_visibility="collapsed")

visualization_started = perf_counter()


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


def render_team_management(admin: FirebaseEmployee | None) -> None:
    """Render admin-only invitation and access controls inside Settings."""
    if admin is None or not admin.can("manage_users"):
        return

    st.markdown("#### Team Management")
    st.caption("Invite approved teammates and manage their dashboard access.")
    with st.container(border=True):
        invite_columns = st.columns([2.4, 1.2, 1.1], vertical_alignment="bottom")
        with invite_columns[0]:
            invite_email = st.text_input(
                "Teammate email",
                placeholder="teammate@company.com",
                key="team_invite_email",
            )
        with invite_columns[1]:
            invite_role = st.selectbox(
                "Role",
                ["Viewer", "Publisher"],
                key="team_invite_role",
            )
        with invite_columns[2]:
            create_invite = st.button("Create Invitation", key="team_create_invitation", type="primary", width="stretch")
        if create_invite:
            normalized_email = invite_email.strip().casefold()
            if "@" not in normalized_email:
                st.session_state.team_management_error = "Enter a valid teammate email address."
            else:
                try:
                    code = invite_teammate(
                        normalized_email,
                        display_name=None,
                        role=invite_role.casefold(),
                        admin=admin,
                    )
                    st.session_state.team_invitation_notice = {
                        "email": normalized_email,
                        "role": invite_role,
                        "code": code,
                    }
                    st.session_state.team_management_error = ""
                except FirebaseAuthError as exc:
                    st.session_state.team_management_error = str(exc)
                except Exception:
                    LOGGER.exception("team_management.invitation_create_failed")
                    st.session_state.team_management_error = "The invitation could not be created. Check Firebase connectivity and try again."

        team_error = st.session_state.get("team_management_error")
        if team_error:
            st.error(team_error)
        invitation_notice = st.session_state.get("team_invitation_notice")
        if isinstance(invitation_notice, dict) and invitation_notice.get("code"):
            st.success(
                f"Invitation created with {invitation_notice.get('role', 'Viewer')} access."
            )
            st.text(str(invitation_notice.get("email") or "Email unavailable"))
            st.caption("No email was sent automatically. Deliver this one-time code through an approved private channel, then hide it.")
            st.code(str(invitation_notice["code"]), language="text")
            if st.button("Hide invitation code", key="team_hide_invitation_code"):
                st.session_state.pop("team_invitation_notice", None)
                st.rerun()

        st.markdown("##### Pending invitations")
        try:
            invitations = list_invitations(admin=admin)
        except FirebaseAuthError as exc:
            invitations = []
            st.warning(str(exc))
        except Exception:
            LOGGER.exception("team_management.invitation_list_failed")
            invitations = []
            st.warning("Invitation status is temporarily unavailable.")
        if not invitations:
            st.caption("No invitations have been created yet.")
        else:
            for invitation in invitations[:20]:
                row = st.columns([2.4, .9, 1.25, 1.05], vertical_alignment="center")
                with row[0]:
                    st.text(invitation.get("email") or "Email unavailable")
                with row[1]:
                    st.caption(str(invitation.get("role") or "Unknown").title())
                with row[2]:
                    status = str(invitation.get("status") or "unknown").title()
                    st.caption(status)
                    if invitation.get("expires_at"):
                        st.caption(f"Until {_pacific_timestamp_label(invitation['expires_at'])}")
                with row[3]:
                    if invitation.get("status") == "pending":
                        if st.button("Revoke", key=f"team_cancel_invite_{invitation['id']}"):
                            try:
                                cancel_invitation(invitation["id"], admin=admin)
                                st.session_state.team_management_error = ""
                                st.rerun()
                            except FirebaseAuthError as exc:
                                st.session_state.team_management_error = str(exc)
                            except Exception:
                                LOGGER.exception("team_management.invitation_cancel_failed")
                                st.session_state.team_management_error = "The invitation could not be revoked. Try again."
            if len(invitations) > 20:
                st.caption(f"Showing the 20 most recent of {len(invitations)} invitations.")

        st.markdown("##### Authorized team members")
        try:
            members = list_team_members(admin=admin)
        except FirebaseAuthError as exc:
            members = []
            st.warning(str(exc))
        except Exception:
            LOGGER.exception("team_management.member_list_failed")
            members = []
            st.warning("Authorized team members are temporarily unavailable.")
        if not members:
            st.caption("No approved team members were found.")
        else:
            for member in members:
                row = st.columns([2.2, .9, 1.15, 1.05], vertical_alignment="center")
                with row[0]:
                    st.text(member.get("email") or "Email unavailable")
                    if member.get("display_name") and member.get("display_name") != member.get("email"):
                        st.caption(str(member["display_name"]))
                with row[1]:
                    st.caption(str(member.get("role") or "Unknown").title())
                with row[2]:
                    if member.get("disabled"):
                        st.caption("Revoked")
                    elif member.get("email_verified"):
                        st.caption("Verified")
                    else:
                        st.caption("Unverified")
                with row[3]:
                    if member.get("uid") != admin.uid and not member.get("disabled"):
                        if st.button("Revoke", key=f"team_revoke_user_{member['uid']}"):
                            try:
                                revoke_user(str(member["uid"]), admin=admin)
                                st.session_state.team_management_error = ""
                                st.rerun()
                            except FirebaseAuthError as exc:
                                st.session_state.team_management_error = str(exc)
                            except Exception:
                                LOGGER.exception("team_management.member_revoke_failed")
                                st.session_state.team_management_error = "Access could not be revoked. Try again."

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
    render_team_management(firebase_employee)

if active_page == "X-Blocking Analysis":
    st.subheader("X-Blocking Analysis")
    st.write(
        "Compare workbook-supplied organic and conventional inventory with the matching planned-demand period. "
        "This view does not determine whether any organic pounds are eligible for conversion."
    )
    if workbook_report is None:
        st.info("Upload and successfully validate a workbook to inspect X-blocking-related inventory and demand.")
    else:
        timeline_sheet, timeline_frame = frame_for(workbook_report, "inventory_timeline")
        harvest_sheet, harvest_frame = frame_for(workbook_report, "harvest_sales")
        sales_sheet, sales_frame = frame_for(workbook_report, "sales_source")
        if timeline_frame is None or timeline_frame.empty:
            st.warning("Forecasted Inventory Levels is not available as a verified source, so no inventory comparison can be shown.")
        else:
            periods = comparison_periods(timeline_frame)
            if periods.empty:
                st.warning("No ACTUAL or FORECAST periods were detected in Forecasted Inventory Levels.")
            else:
                st.markdown("#### Comparison filters")
                period_labels = periods["selection_label"].tolist()
                actual_indices = [index for index, state in enumerate(periods["state"].tolist()) if state == "Actual"]
                default_period_index = actual_indices[-1] if actual_indices else 0
                selected_period_label = st.selectbox(
                    "Reporting week",
                    period_labels,
                    index=default_period_index,
                    key="x_blocking_reporting_week",
                )
                selected_period = periods.loc[periods["selection_label"] == selected_period_label].iloc[0]
                # X-Blocking options are anchored to the verified inventory
                # timeline.  Harvest/sales may contain products that have no
                # matching inventory balance (for example process-only rows),
                # so using those frames to populate controls would offer
                # selections that cannot produce an inventory comparison.
                x_option_frame = timeline_frame
                if "classification" in timeline_frame:
                    organic_options = timeline_frame[
                        timeline_frame["classification"].astype("string").str.casefold().eq("organic")
                    ]
                    if not organic_options.empty:
                        x_option_frame = organic_options
                variety_values, variety_source_values = standardized_dimension_options(
                    (x_option_frame,), "variety"
                )
                size_values, size_source_values = standardized_dimension_options(
                    (x_option_frame,), "size"
                )
                with st.container(key="x_blocking_filter_controls"):
                    x_filter_left, x_filter_right = st.columns(2, gap="small")
                    with x_filter_left:
                        with st.container(key="x_filter_tile_variety"):
                            st.markdown(
                                f"<div class='cs-filter-control-label'>{dashboard_icon('sprout', size=26)}<strong>Organic variety</strong></div>",
                                unsafe_allow_html=True,
                            )
                            selected_variety_labels = st.multiselect(
                                "Organic variety",
                                variety_values,
                                key="x_blocking_variety",
                                placeholder="All varieties",
                                label_visibility="collapsed",
                            )
                    with x_filter_right:
                        with st.container(key="x_filter_tile_size"):
                            st.markdown(
                                f"<div class='cs-filter-control-label'>{dashboard_icon('scaling', size=26)}<strong>Size</strong></div>",
                                unsafe_allow_html=True,
                            )
                            selected_size_labels = st.multiselect(
                                "Size",
                                size_values,
                                key="x_blocking_size",
                                placeholder="All sizes",
                                label_visibility="collapsed",
                            )
                selected_varieties = [
                    raw_value
                    for display_label in selected_variety_labels
                    for raw_value in variety_source_values.get(display_label, [display_label])
                ]
                selected_sizes = [
                    raw_value
                    for display_label in selected_size_labels
                    for raw_value in size_source_values.get(display_label, [display_label])
                ]
                horizon_values = organic_horizon_options(timeline_frame, selected_period)
                selected_horizon = st.selectbox(
                    "Planning horizon",
                    horizon_values or ["1 week"],
                    key="x_blocking_horizon",
                )
                comparison, comparison_meta = build_comparison(
                    timeline_frame,
                    sales_frame,
                    selected_period,
                    selected_varieties,
                    selected_sizes,
                )
                if comparison.empty:
                    st.info("No verified inventory rows match the selected reporting week, variety, and size filters.")
                else:
                    inventory_measure = f"{selected_period['state']} inventory"
                    card_data = {}
                    for classification, label in (("Organic", "Organic"), ("Conventional", "Conventional")):
                        selected_class = comparison[
                            comparison["classification"].astype("string").str.casefold() == classification.casefold()
                        ]
                        card_data[f"{label} inventory"] = selected_class["inventory_lbs"].sum(min_count=1) if not selected_class.empty else pd.NA
                        card_data[f"{label} demand"] = selected_class["demand_lbs"].sum(min_count=1) if not selected_class.empty else pd.NA

                    def display_lbs(value: object) -> str:
                        if value is None or pd.isna(value):
                            return "—"
                        return f"{float(value):,.0f} lbs"

                    summary_cards = [
                        (f"Organic {selected_period['state']} inventory", card_data["Organic inventory"]),
                        (f"Organic planned demand · Week {int(selected_period['week'])}", card_data["Organic demand"]),
                        (f"Conventional {selected_period['state']} inventory", card_data["Conventional inventory"]),
                        (f"Conventional planned demand · Week {int(selected_period['week'])}", card_data["Conventional demand"]),
                    ]
                    summary_columns = st.columns(4, gap="small")
                    for column, (label, value) in zip(summary_columns, summary_cards):
                        with column:
                            st.metric(label, display_lbs(value))

                    organic_inventory = card_data["Organic inventory"]
                    organic_demand = card_data["Organic demand"]
                    if pd.notna(organic_inventory) and pd.notna(organic_demand):
                        # Keep this derived value available for future X-Blocking
                        # scenarios, but do not expose it in the current UI.
                        organic_above_demand = float(organic_inventory) - float(organic_demand)
                    st.markdown("#### Organic vs. Conventional inventory")
                    classification_frame = comparison[
                        comparison["classification"].astype("string").str.casefold().isin(
                            {"organic", "conventional"}
                        )
                    ][["classification", "inventory_lbs"]].rename(
                        columns={"inventory_lbs": "stored_lbs"}
                    )
                    if classification_frame.empty:
                        st.info("No verified organic/conventional inventory is available for this selection.")
                    else:
                        render_chart(
                            lambda: classification_comparison(classification_frame),
                            "x_blocking_classification_comparison",
                            "Organic and conventional inventory comparison",
                        )
                    detail_columns = [
                        column for column in ["product", "variety", "size", "classification", "inventory_lbs", "demand_lbs"]
                        if column in comparison
                    ]
                    detail_table = comparison[detail_columns].rename(
                        columns={
                            "product": "Product",
                            "variety": "Variety",
                            "size": "Size",
                            "classification": "Classification",
                            "inventory_lbs": f"{inventory_measure.title()} (lbs)",
                            "demand_lbs": "Planned demand (lbs)",
                        }
                    ).copy()
                    for numeric_column in [column for column in detail_table.columns if column.endswith("(lbs)")]:
                        detail_table[numeric_column] = detail_table[numeric_column].map(display_lbs)
                    st.dataframe(detail_table, width="stretch", hide_index=True)
                    # Keep the workbook reconciliation calculation active for
                    # internal diagnostics, but do not expose its developer
                    # table in the everyday X-Blocking interface.
                    _reconciliation = reconciliation_report(
                        timeline_frame,
                        sales_frame,
                        selected_period,
                        selected_varieties,
                        selected_sizes,
                    )
                    if comparison["classification"].astype("string").str.casefold().eq("unclassified").any():
                        st.warning("Some rows do not have a verified organic/conventional classification and are not included in the classification columns.")
                    st.markdown("#### Organic Inventory Outlook")
                    outlook = build_organic_outlook(
                        timeline_frame,
                        harvest_frame,
                        sales_frame,
                        selected_period,
                        selected_horizon,
                        selected_varieties,
                        selected_sizes,
                    )
                    outlook_periods = outlook["future_periods"]
                    if outlook_periods.empty:
                        st.info("No compatible future forecast periods are available for the selected organic product.")
                    else:
                        outlook_cards = [
                            ("Starting organic inventory", outlook["starting_lbs"]),
                            ("Planned incoming harvest", outlook["harvest_lbs"]),
                            ("Planned organic sales", outlook["sales_lbs"]),
                            ("Projected ending organic inventory", outlook["ending_lbs"]),
                        ]
                        outlook_columns = st.columns(4, gap="small")
                        for column, (label, value) in zip(outlook_columns, outlook_cards):
                            with column:
                                st.metric(label, display_lbs(value))
                        if outlook["reconciled"]:
                            st.success("The displayed workbook components reconcile to the projected ending balance for this selection.")
                        # Keep non-reconciled workbook diagnostics in ``outlook``
                        # for internal use, but do not expose the warning in this
                        # compact X-Blocking view.
                    st.markdown("#### Potential opportunity cost")
                    financial_status = pricing_assessment(workbook_report)
                    price_comparison = reference_price_comparison(
                        workbook_report,
                        selected_varieties,
                        selected_sizes,
                    )
                    if price_comparison.get("paired"):
                        price_columns = st.columns(3, gap="small")
                        with price_columns[0]:
                            st.metric("Organic reference price", f"${price_comparison['organic_price']:,.4f} / lb")
                        with price_columns[1]:
                            st.metric("Conventional reference price", f"${price_comparison['conventional_price']:,.4f} / lb")
                        with price_columns[2]:
                            st.metric(
                                "Conventional − organic price difference",
                                f"${price_comparison['gross_sales_difference_per_lb']:,.4f} / lb",
                            )
                        verified_organic_inventory = (
                            int(max(0.0, float(organic_inventory)))
                            if organic_inventory is not None and pd.notna(organic_inventory)
                            else 0
                        )
                        previous_quantity = int(
                            st.session_state.get(X_BLOCKING_SCENARIO_QUANTITY_KEY, 0) or 0
                        )
                        quantity_was_reduced = previous_quantity > verified_organic_inventory
                        scenario_quantity = min(
                            max(previous_quantity, 0),
                            verified_organic_inventory,
                        )
                        slider_maximum = (verified_organic_inventory // 100) * 100
                        slider_position = min(
                            slider_maximum,
                            int(round(scenario_quantity / 100.0) * 100),
                        )
                        st.session_state[X_BLOCKING_SCENARIO_QUANTITY_KEY] = scenario_quantity
                        st.session_state[X_BLOCKING_SCENARIO_SLIDER_KEY] = slider_position
                        st.session_state[X_BLOCKING_SCENARIO_EXACT_KEY] = scenario_quantity
                        if quantity_was_reduced:
                            st.info(
                                f"The previous scenario amount exceeded the verified organic inventory ceiling; "
                                f"it was reduced to {scenario_quantity:,} lbs."
                            )
                        st.slider(
                            "Organic pounds considered for X-blocking",
                            min_value=0,
                            max_value=slider_maximum,
                            step=100,
                            key=X_BLOCKING_SCENARIO_SLIDER_KEY,
                            on_change=_sync_x_blocking_from_slider,
                        )
                        st.number_input(
                            "Exact organic pounds",
                            min_value=0,
                            max_value=verified_organic_inventory,
                            step=1,
                            format="%d",
                            key=X_BLOCKING_SCENARIO_EXACT_KEY,
                            on_change=_sync_x_blocking_from_exact_input,
                        )
                        scenario_pounds = int(
                            st.session_state.get(X_BLOCKING_SCENARIO_QUANTITY_KEY, 0) or 0
                        )
                        hypothetical_difference = (
                            0.0
                            if scenario_pounds == 0
                            else scenario_pounds * float(price_comparison["gross_sales_difference_per_lb"])
                        )
                        st.metric("Hypothetical gross sales value difference", f"${hypothetical_difference:,.2f}")
                        st.caption("Scenario estimate only. Confirm pricing, eligibility, and sales assumptions before use.")
                    else:
                        message = str(price_comparison.get("message") or financial_status.get("detail") or financial_status["status"])
                        st.warning(message)

if active_page == "Executive Overview":
    st.subheader("Executive overview")
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
            needed_label = "Total loads needed (review)" if needed_negative else "Total loads needed (source)"
            remaining_label = "Loads remaining (review)" if remaining_negative else "Loads remaining (source)"
            purchasing_metrics_valid = not any(value > 0 for value in [needed_negative, purchased_negative, remaining_negative])
            if purchasing_metrics_valid:
                metric_columns = st.columns(3)
                metric_columns[0].metric(needed_label, f"{total_needed:,.2f}" if total_needed is not None else "Unavailable")
                metric_columns[1].metric("Loads purchased (source)", f"{purchased:,.2f}" if purchased is not None else "Unavailable")
                metric_columns[2].metric(remaining_label, f"{remaining:,.2f}" if remaining is not None else "Unavailable")
            else:
                st.caption("Purchasing summary metrics are hidden because the POs source contains negative load values requiring review.")
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
            if SHOW_PURCHASING_LOAD_CHARTS:
                render_chart(lambda: load_comparison(purchasing_filtered), "purchasing_loads", "Purchased and remaining loads from POs")
                st.caption("Loads per Week is plotted as an overall weekly trend. Product and variety filters above still apply; blank cells are omitted and source zeros remain zeros. The source does not provide a crop year.")
                render_chart(lambda: loads_timeline(purchasing_filtered), "weekly_loads", "Overall weekly loads from Loads per Week")

if active_page == "Data Explorer & Quality":
    st.subheader("Data explorer & quality")
    if workbook_report and SHOW_DATA_EXPLORER_DIAGNOSTICS:
        st.dataframe(report_to_frame(workbook_report), width="stretch", hide_index=True)
    component_result = None
    if _inventory_explorer_autocomplete is not None:
        previous_search = st.session_state.get("inventory_explorer_last_query")
        component_state = st.session_state.get("inventory_explorer_autocomplete", {})
        if not isinstance(component_state, dict):
            component_state = {}
        search = str(component_state.get("query") or "")
        suggestions_open = bool(component_state.get("open") and search.strip())
        suggestions = inventory_explorer_suggestions(filtered, search)
        suggestion_payload = [dict(item, id=str(index)) for index, item in enumerate(suggestions)]
        component_result = _inventory_explorer_autocomplete(
            data={
                "query": search,
                "suggestions": suggestion_payload,
                "no_matches": bool(search.strip() and not suggestions),
                "open": suggestions_open,
            },
            default={"query": previous_search or "", "open": False},
            key="inventory_explorer_autocomplete",
            on_query_change=lambda: None,
            on_open_change=lambda: None,
        )
    else:
        search = st.text_input(
            "Search verified records",
            placeholder="Product, variety, classification, or size",
            key="inventory_explorer_search",
            on_change=clear_inventory_explorer_selection,
        )

    suggestions = inventory_explorer_suggestions(filtered, search)
    suggestion_payload = [dict(item, id=str(index)) for index, item in enumerate(suggestions)]
    suggestion_lookup = {str(item["id"]): item for item in suggestion_payload}
    selected_id = None
    if component_result is not None:
        selected_id = component_result.get("selected")
        if selected_id is not None:
            st.session_state["inventory_explorer_selected_id"] = str(selected_id)
        if previous_search != search:
            st.session_state["inventory_explorer_selected_id"] = None
        st.session_state["inventory_explorer_last_query"] = search

    selected_id = st.session_state.get("inventory_explorer_selected_id") if selected_id is None else str(selected_id)
    text_filtered = filtered[filtered.astype(str).apply(lambda row: row.str.contains(search, case=False, na=False).any(), axis=1)] if search else filtered
    if selected_id and selected_id in suggestion_lookup:
        explorer_df = filter_inventory_explorer_suggestion(filtered, suggestion_lookup[selected_id])
    else:
        explorer_df = text_filtered
    if explorer_df.empty:
        st.info("No matching products" if search.strip() else "No verified records match the current filters.")
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

"""Local-only linked workbook support for the CSUB development demo.

This module stores only a user-selected Windows path and file metadata in the
user's local application-data directory. It never writes workbook bytes,
tokens, or shared Firebase metadata, and it is intentionally separate from
the Microsoft Graph and Country Sweet authentication flows.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_NAME = "csub_demo_workbook.json"


@dataclass(frozen=True)
class LocalWorkbookLink:
    """A persisted reference to a local OneDrive-synchronized workbook."""

    path: str
    filename: str
    selected_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "filename": self.filename,
            "selected_at": self.selected_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "LocalWorkbookLink":
        path = str(value.get("path", "")).strip()
        if not path:
            raise ValueError("The saved local workbook reference has no path.")
        return cls(
            path=path,
            filename=str(value.get("filename") or Path(path).name),
            selected_at=str(value.get("selected_at") or ""),
        )


def config_path() -> Path:
    """Return a local config path outside the repository by default."""
    configured = os.getenv("BAKO_CSUB_LOCAL_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return root / "BakoSweet" / DEFAULT_CONFIG_NAME


def load_link(path: Path | None = None) -> LocalWorkbookLink | None:
    """Read the local path reference, returning ``None`` if not configured."""
    target = path or config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return LocalWorkbookLink.from_dict(payload)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_link(link: LocalWorkbookLink, path: Path | None = None) -> Path:
    """Persist only the selected path and non-sensitive selection metadata."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(link.to_dict(), indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def forget_link(path: Path | None = None) -> None:
    """Remove the local reference without touching the workbook itself."""
    try:
        (path or config_path()).unlink()
    except FileNotFoundError:
        pass


def choose_workbook() -> Path | None:
    """Open a native Windows file picker when a local desktop is available."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(
            title="Select the synchronized CSUB demonstration workbook",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")],
        )
        root.destroy()
        return Path(selected) if selected else None
    except Exception as exc:  # pragma: no cover - depends on desktop availability
        raise RuntimeError(
            "The Windows file picker is unavailable in this Streamlit process. "
            "Set BAKO_CSUB_LOCAL_CONFIG to a local config file or select the workbook path through a development-only fallback."
        ) from exc

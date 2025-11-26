"""
steam_nonsteam_art_apply.py

Minimal PyQt6 utility to apply Steam CDN artwork from a real AppID to a non-Steam
shortcut by using the shortcut's actual Steam-assigned appid stored in shortcuts.vdf.

Requirements:
    pip install PyQt6 requests vdf
"""

import os
import sys
import json
import shutil
import zlib
from pathlib import Path

import requests
import vdf
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QLabel, QLineEdit, QPushButton, QMessageBox
)
from PyQt6.QtCore import Qt

# -------------------- Helpers & Config --------------------

USER_HOME = Path.home()
STEAM_USERDATA_BASE = USER_HOME / "Library" / "Application Support" / "Steam" / "userdata"

HEADERS = {"User-Agent": "steam-art-tool/1.0"}
REQUEST_TIMEOUT = 15


def find_steam_userdata():
    base = STEAM_USERDATA_BASE
    if not base.exists():
        return None, None
    # choose the first numeric folder (works for single-account users; if multiple, pick first)
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and entry.name.isdigit():
            return entry.name, entry
    return None, None


def ensure_grid_dir(userdata_path: Path):
    gp = userdata_path / "config" / "grid"
    gp.mkdir(parents=True, exist_ok=True)
    return gp


def download_to_file(url: str, out_path: Path) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
    except Exception:
        return False
    if r.status_code != 200:
        return False
    try:
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception:
        return False


def normalize_appid_field(raw):
    """
    shortcuts.vdf 'appid' may come as:
      - Python int (signed) -> convert to unsigned
      - bytes -> little-endian 4 bytes -> convert to unsigned int
    Return decimal string of unsigned 32-bit value (same as Steam filenames).
    """
    if raw is None:
        return None
    # bytes case
    if isinstance(raw, (bytes, bytearray)):
        # some cheap checks: if length 4, intval from little-endian
        try:
            intval = int.from_bytes(raw, "little", signed=False)
            return str(intval & 0xFFFFFFFF)
        except Exception:
            pass
    # int case (possibly negative)
    try:
        intval = int(raw)
        return str(int(intval & 0xFFFFFFFF))
    except Exception:
        try:
            return str(int(str(raw)))
        except Exception:
            return None


def read_shortcuts(shortcuts_path: Path):
    """
    Returns list of entries: dicts with keys:
      - appid (string unsigned decimal)
      - AppName (str)
      - Exe (str)
      - entry_raw (original dict)
    """
    entries = []
    if not shortcuts_path.exists():
        return entries

    with open(shortcuts_path, "rb") as f:
        data = vdf.binary_load(f)

    shortcuts = data.get("shortcuts") or data.get(b"shortcuts") or {}
    for k, ent in shortcuts.items():
        # read appid (could be int or bytes)
        raw_appid = ent.get("appid") or ent.get(b"appid")
        appid_s = normalize_appid_field(raw_appid)
        # read name/exe robustly (bytes or str)
        raw_name = ent.get("AppName") or ent.get(b"AppName") or b""
        raw_exe = ent.get("Exe") or ent.get(b"Exe") or b""
        name = raw_name.decode("utf-8", "ignore") if isinstance(raw_name, (bytes, bytearray)) else str(raw_name)
        exe  = raw_exe.decode("utf-8", "ignore") if isinstance(raw_exe, (bytes, bytearray)) else str(raw_exe)
        if not appid_s:
            # fallback: compute crc style (not preferred), but we'll skip entries missing appid
            continue
        entries.append({
            "appid": appid_s,
            "AppName": name,
            "Exe": exe,
            "raw": ent
        })
    return entries


def copy_or_write_json_for_target(grid_dir: Path, source_appid: str, target_appid: str) -> str:
    """
    If grid/<source_appid>.json exists, copy to grid/<target_appid>.json.
    Otherwise write a reasonable default JSON for logo positioning.
    Returns status string.
    """
    src = grid_dir / f"{source_appid}.json"
    dst = grid_dir / f"{target_appid}.json"
    try:
        if src.exists():
            shutil.copy2(src, dst)
            return "JSON copied"
        # write sensible default
        default_json = {
            "nVersion": 1,
            "logoPosition": {
                "pinnedPosition": "CenterCenter",
                "nWidthPct": 70.0,
                "nHeightPct": 95.0
            }
        }
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(default_json, f, indent=2)
        return "JSON written (default)"
    except Exception:
        return "JSON failed"


# -------------------- GUI App --------------------

class SteamNonSteamArtApply(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Apply Steam Artwork to Non-Steam Shortcuts")
        self.resize(700, 480)

        # autodetect userdata
        steamid, userdata_path = find_steam_userdata()
        if not steamid:
            QMessageBox.critical(self, "Steam userdata not found",
                                 "Couldn't find Steam userdata folder at:\n"
                                 f"{STEAM_USERDATA_BASE}\n\nMake sure Steam has run at least once.")
            sys.exit(1)

        self.steamid = steamid
        self.userdata_path = userdata_path
        self.shortcuts_path = self.userdata_path / "config" / "shortcuts.vdf"
        self.grid_dir = ensure_grid_dir(self.userdata_path)

        # layout
        v = QVBoxLayout()

        header = QLabel(f"Steam userdata: {self.userdata_path}   (SteamID: {self.steamid})")
        #header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(header)

        self.list = QListWidget()
        v.addWidget(QLabel("Non-Steam shortcuts (select one):"))
        v.addWidget(self.list, stretch=1)

        # source AppID input
        h = QHBoxLayout()
        self.appid_input = QLineEdit()
        self.appid_input.setPlaceholderText("Enter source Steam AppID (e.g. 271590)")
        h.addWidget(QLabel("Source AppID:"))
        h.addWidget(self.appid_input, stretch=1)

        self.apply_btn = QPushButton("Download and Apply to selected")
        self.apply_btn.clicked.connect(self.on_apply)
        h.addWidget(self.apply_btn)

        v.addLayout(h)

        # status
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.setLayout(v)

        # load shortcuts
        self.shortcuts = []  # list of dicts from read_shortcuts
        self.reload_shortcuts()

    def reload_shortcuts(self):
        self.list.clear()
        self.shortcuts = read_shortcuts(self.shortcuts_path)
        for ent in self.shortcuts:
            name = ent["AppName"]
            exe = ent["Exe"]
            aid = ent["appid"]
            self.list.addItem(f"{name} — {exe}   [ID: {aid}]")
        self.status.setText(f"Loaded {len(self.shortcuts)} shortcuts from {self.shortcuts_path}")

    def on_apply(self):
        row = self.list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select an entry", "Please select a non-Steam shortcut from the list.")
            return
        source_appid = self.appid_input.text().strip()
        if not source_appid.isdigit():
            QMessageBox.warning(self, "Invalid AppID", "Please enter a valid numeric Steam AppID to copy from.")
            return

        target_entry = self.shortcuts[row]
        target_id = target_entry["appid"]

        self.status.setText(f"Downloading artwork for AppID {source_appid} → applying to non-Steam ID {target_id} ...")

        # CDN urls
        cover_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{source_appid}/library_600x900_2x.jpg"
        wide_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{source_appid}/library_411x184.jpg"
        hero_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{source_appid}/library_hero.jpg"
        logo_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{source_appid}/logo.png"

        # destination paths
        dest_cover = self.grid_dir / f"{target_id}p.jpg"
        dest_wide  = self.grid_dir / f"{target_id}.jpg"
        dest_hero  = self.grid_dir / f"{target_id}_hero.jpg"
        dest_logo  = self.grid_dir / f"{target_id}_logo.png"

        results = []
        results.append("Cover ✓" if download_to_file(cover_url, dest_cover) else "Cover ✗")
        results.append("Wide ✓"  if download_to_file(wide_url, dest_wide) else "Wide ✗")
        results.append("Hero ✓"  if download_to_file(hero_url, dest_hero) else "Hero ✗")
        results.append("Logo ✓"  if download_to_file(logo_url, dest_logo) else "Logo ✗")

        # handle JSON metadata (copy or write default)
        json_result = copy_or_write_json_for_target(self.grid_dir, source_appid, target_id)
        results.append(json_result)

        self.status.setText("  |  ".join(results) + f"\nSaved to: {self.grid_dir}")

        # remind user: restart Steam
        self.status.setText(self.status.text() + "\nTip: Quit & relaunch Steam to pick up artwork (or clear Steam cache).")


# -------------------- Run --------------------

def main():
    app = QApplication(sys.argv)
    win = SteamNonSteamArtApply()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

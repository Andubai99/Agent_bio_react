from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API_TXT = ROOT / "API.txt"


def get_api_key(env_name: str, *, marker: str = "", api_txt: Path = LOCAL_API_TXT) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    found = _read_key_from_api_txt(marker, api_txt)
    if found:
        return found
    return ""


def _read_key_from_api_txt(marker: str, api_txt: Path) -> Optional[str]:
    if not api_txt.exists():
        return None
    marker_lower = marker.lower()
    for line in api_txt.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if marker and marker_lower not in stripped.lower():
            continue
        if stripped.startswith("apikey="):
            value = stripped[len("apikey=") :]
            if "#" in value:
                value = value.split("#", 1)[0]
            return value.strip()
    return None

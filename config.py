"""Application configuration: enabled flags + per-lane ban/pick priority lists.

Persisted to config.json next to the executable. All access goes through a
threading.Lock because the GUI (main thread) writes while the LCU websocket
handlers (background thread) read.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field, asdict

LANES = ("top", "jungle", "middle", "bottom", "utility")

LANE_LABELS = {
    "top": "Top",
    "jungle": "Jungle",
    "middle": "Middle",
    "bottom": "Bottom (ADC)",
    "utility": "Support",
}


def _base_dir() -> str:
    """Directory to store config.json / champions.json.

    Uses the folder containing the executable when frozen by PyInstaller,
    otherwise the folder containing this source file.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_base_dir(), "config.json")


@dataclass
class AppConfig:
    auto_accept: bool = False
    auto_ban: bool = False
    auto_pick: bool = False
    bans: dict[str, list[int]] = field(default_factory=lambda: {lane: [] for lane in LANES})
    picks: dict[str, list[int]] = field(default_factory=lambda: {lane: [] for lane in LANES})

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def snapshot(self) -> "AppConfig":
        """Return a lock-free deep copy safe to read from any thread."""
        with self._lock:
            return AppConfig(
                auto_accept=self.auto_accept,
                auto_ban=self.auto_ban,
                auto_pick=self.auto_pick,
                bans={lane: list(ids) for lane, ids in self.bans.items()},
                picks={lane: list(ids) for lane, ids in self.picks.items()},
            )

    def update(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)
        self.save()

    def save(self) -> None:
        with self._lock:
            data = {
                "auto_accept": self.auto_accept,
                "auto_ban": self.auto_ban,
                "auto_pick": self.auto_pick,
                "bans": self.bans,
                "picks": self.picks,
            }
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, CONFIG_PATH)  # atomic on the same filesystem


def _normalize_lane_map(raw) -> dict[str, list[int]]:
    """Coerce a loaded lane->ids map into a complete, well-typed dict."""
    result = {lane: [] for lane in LANES}
    if isinstance(raw, dict):
        for lane in LANES:
            ids = raw.get(lane, [])
            if isinstance(ids, list):
                result[lane] = [int(x) for x in ids if isinstance(x, (int, float, str)) and str(x).lstrip("-").isdigit()]
    return result


def load() -> AppConfig:
    """Load config.json, tolerating a missing or partially-corrupt file."""
    if not os.path.exists(CONFIG_PATH):
        return AppConfig()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return AppConfig()
    return AppConfig(
        auto_accept=bool(data.get("auto_accept", False)),
        auto_ban=bool(data.get("auto_ban", False)),
        auto_pick=bool(data.get("auto_pick", False)),
        bans=_normalize_lane_map(data.get("bans")),
        picks=_normalize_lane_map(data.get("picks")),
    )

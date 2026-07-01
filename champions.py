"""Champion id <-> name mapping, sourced from Riot's Data Dragon.

The GUI shows champion names; the LCU API speaks numeric champion ids. This
module fetches the mapping once, caches it to champions.json, and exposes fast
lookups in both directions. If the network is unavailable it falls back to the
cache (and returns an empty map only on a truly cold start with no internet).
"""
from __future__ import annotations

import json
import os

import requests

from config import _base_dir

CACHE_PATH = os.path.join(_base_dir(), "champions.json")
VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
CHAMPION_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
_TIMEOUT = 10


class Champions:
    """Bidirectional champion id/name lookup."""

    def __init__(self, id_to_name: dict[int, str]):
        self.id_to_name = dict(id_to_name)
        self.name_to_id = {name: cid for cid, name in id_to_name.items()}

    def names(self) -> list[str]:
        """Champion names sorted alphabetically, for GUI dropdowns."""
        return sorted(self.name_to_id)

    def id_for(self, name: str) -> int | None:
        return self.name_to_id.get(name)

    def name_for(self, cid: int) -> str:
        return self.id_to_name.get(cid, str(cid))


def _fetch_from_network() -> dict[int, str]:
    versions = requests.get(VERSIONS_URL, timeout=_TIMEOUT).json()
    version = versions[0]
    data = requests.get(CHAMPION_URL.format(version=version), timeout=_TIMEOUT).json()
    return {int(entry["key"]): entry["name"] for entry in data["data"].values()}


def _load_cache() -> dict[int, str] | None:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return {int(k): v for k, v in raw.items()}
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _save_cache(mapping: dict[int, str]) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in mapping.items()}, fh, indent=2)
    except OSError:
        pass


def load_champions(refresh: bool = False) -> Champions:
    """Return champion data, preferring the network but falling back to cache.

    On success the fresh data is cached. `refresh=True` forces a network fetch.
    """
    if not refresh:
        cached = _load_cache()
        if cached:
            return Champions(cached)
    try:
        mapping = _fetch_from_network()
        _save_cache(mapping)
        return Champions(mapping)
    except (requests.RequestException, ValueError, KeyError):
        cached = _load_cache()
        return Champions(cached or {})

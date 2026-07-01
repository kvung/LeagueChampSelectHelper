"""LCU integration: connects to the running League client and drives the
auto-accept / auto-ban / auto-pick automation off websocket events. Also
publishes a snapshot of live game state (summoner, gameflow phase, lobby role
picks, champ-select phase, region) via `info_cb` for the GUI to render.

`lcu-driver` handles lockfile discovery, auth, HTTPS/WSS, and reconnection. We
register handlers for:

  * connection ready/close       -> report status + refresh info
  * ready-check UPDATE           -> auto-accept
  * champ-select session         -> decide_action() + update phase display
  * gameflow session             -> update Game Status
  * lobby                        -> update Selected Roles

`connector.start()` blocks on its own asyncio loop, so main.py runs an
LcuController in a daemon thread.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Callable

from lcu_driver import Connector

from champ_select import decide_action

BANNABLE_URL = "/lol-champ-select/v1/bannable-champion-ids"
PICKABLE_URL = "/lol-champ-select/v1/pickable-champion-ids"

DASH = "—"

_GAMEFLOW_LABELS = {
    "None":              "Not in lobby",
    "Lobby":             "In Lobby",
    "Matchmaking":       "In Queue",
    "ReadyCheck":        "Ready Check",
    "ChampSelect":       "In Champ Select",
    "GameStart":         "Game Starting",
    "InProgress":        "In Game",
    "Reconnect":         "Reconnecting",
    "WaitingForStats":   "Post-Game (stats)",
    "PreEndOfGame":      "Post-Game",
    "EndOfGame":         "Post-Game",
    "TerminatedInError": "Client Error",
}

_QUEUE_NAMES = {
    0:    "Custom",
    400:  "Normal Draft",
    420:  "Ranked Solo/Duo",
    430:  "Normal Blind",
    440:  "Ranked Flex",
    450:  "ARAM",
    490:  "Quickplay",
    700:  "Clash",
    720:  "Clash (ARAM)",
    830:  "Co-op vs AI (Intro)",
    840:  "Co-op vs AI (Beginner)",
    850:  "Co-op vs AI (Intermediate)",
    900:  "ARURF",
    1020: "One for All",
    1300: "Nexus Blitz",
    1400: "Ultimate Spellbook",
    1700: "Arena",
    1710: "Arena",
    1900: "Pick URF",
}

_POSITION_LABELS = {
    "TOP":        "Top",
    "JUNGLE":     "Jungle",
    "MIDDLE":     "Mid",
    "BOTTOM":     "Bot",
    "UTILITY":    "Support",
    "FILL":       "Fill",
    "UNSELECTED": DASH,
    "":           DASH,
}

_CS_PHASE_LABELS = {
    "PLANNING":     "Planning",
    "BAN_PICK":     "Ban / Pick",
    "FINALIZATION": "Finalization",
    "GAME_STARTING": "Game Starting",
}


@dataclass
class GameInfo:
    """Snapshot of the client-side view the GUI cares about."""
    summoner: str = DASH
    game_status: str = "Waiting for client…"
    selected_roles: str = DASH
    champ_select_phase: str = DASH
    region: str = DASH


class LcuController:
    """Owns the lcu-driver Connector and the automation handlers.

    Args:
        config: the shared AppConfig (read via .snapshot() on each event).
        status_cb: called with a short status string ("Connected", etc.);
            the GUI marshals this onto the Tk thread.
        info_cb: called with a fresh GameInfo whenever any tracked field
            changes; also on connection ready/close.
    """

    def __init__(self, config, status_cb: Callable[[str], None],
                 info_cb: Callable[["GameInfo"], None]):
        self.config = config
        self.status_cb = status_cb
        self.info_cb = info_cb
        self._submitted: set[int] = set()
        self._info = GameInfo()
        self.connector: Connector | None = None

    def _build_connector(self) -> Connector:
        # Constructed lazily on the thread that will run the loop: `Connector`
        # calls `asyncio.get_event_loop()` in its __init__, which on Python 3.12+
        # requires a loop already set on the current thread.
        connector = Connector()
        connector.ready(self._on_ready)
        connector.close(self._on_close)
        connector.ws.register(
            "/lol-matchmaking/v1/ready-check", event_types=("CREATE", "UPDATE")
        )(self._on_ready_check)
        connector.ws.register(
            "/lol-champ-select/v1/session", event_types=("CREATE", "UPDATE", "DELETE")
        )(self._on_champ_select)
        connector.ws.register(
            "/lol-gameflow/v1/session", event_types=("CREATE", "UPDATE", "DELETE")
        )(self._on_gameflow_session)
        connector.ws.register(
            "/lol-lobby/v2/lobby", event_types=("CREATE", "UPDATE", "DELETE")
        )(self._on_lobby)
        return connector

    async def _on_ready(self, connection):
        self.status_cb("Connected to League client")
        await self._refresh_summoner(connection)
        await self._refresh_region(connection)
        await self._refresh_gameflow(connection)
        await self._refresh_lobby(connection)
        self._emit_info()

    async def _on_close(self, connection):
        self.status_cb("League client closed — waiting…")
        self._info = GameInfo()
        self._submitted.clear()
        self._emit_info()

    async def _refresh_summoner(self, connection) -> None:
        data = await self._get_json(connection, "/lol-summoner/v1/current-summoner")
        if not isinstance(data, dict):
            return
        game_name = data.get("gameName") or data.get("displayName") or ""
        tag_line = data.get("tagLine") or ""
        if game_name and tag_line:
            self._info.summoner = f"{game_name}#{tag_line}"
        elif game_name:
            self._info.summoner = game_name

    async def _refresh_region(self, connection) -> None:
        data = await self._get_json(connection, "/riotclient/region-locale")
        if isinstance(data, dict):
            region = data.get("region")
            if isinstance(region, str) and region:
                self._info.region = region
                return
        data = await self._get_json(
            connection, "/lol-platform-config/v1/namespaces/LoginDataPacket/platformId"
        )
        if isinstance(data, str) and data:
            self._info.region = data.rstrip("0123456789") or data

    async def _refresh_gameflow(self, connection) -> None:
        self._apply_gameflow(await self._get_json(connection, "/lol-gameflow/v1/session"))

    async def _refresh_lobby(self, connection) -> None:
        self._apply_lobby(await self._get_json(connection, "/lol-lobby/v2/lobby"))

    async def _on_ready_check(self, connection, event):
        cfg = self.config.snapshot()
        if not cfg.auto_accept:
            return
        data = event.data or {}
        if data.get("state") == "InProgress" and data.get("playerResponse") != "Accepted":
            await connection.request("post", "/lol-matchmaking/v1/ready-check/accept")
            self.status_cb("Auto-accepted match")

    async def _on_gameflow_session(self, connection, event):
        self._apply_gameflow(event.data)
        self._emit_info()

    async def _on_lobby(self, connection, event):
        self._apply_lobby(event.data)
        self._emit_info()

    async def _on_champ_select(self, connection, event):
        session = event.data
        self._apply_champ_select_phase(session)
        self._emit_info()

        if getattr(event, "type", None) == "Delete" or session is None:
            self._submitted.clear()
            return

        cfg = self.config.snapshot()

        candidate = decide_action(session, cfg)
        if candidate is None or candidate.id in self._submitted:
            return

        available = await self._available_ids(
            connection, BANNABLE_URL if candidate.type == "ban" else PICKABLE_URL
        )
        final = decide_action(session, cfg, available_ids=available) if available else candidate
        if final is None:
            return

        self._submitted.add(final.id)
        await connection.request(
            "patch",
            f"/lol-champ-select/v1/session/actions/{final.id}",
            data={"championId": final.champion_id, "completed": True},
        )
        verb = "Banned" if final.type == "ban" else "Picked"
        self.status_cb(f"{verb} champion {final.champion_id}")

    def _apply_gameflow(self, session) -> None:
        if not isinstance(session, dict):
            self._info.game_status = _GAMEFLOW_LABELS["None"]
            return
        phase = session.get("phase") or "None"
        label = _GAMEFLOW_LABELS.get(phase, phase)
        queue_data = (session.get("gameData") or {}).get("queue") or {}
        queue_id = queue_data.get("id")
        desc = queue_data.get("description") or _QUEUE_NAMES.get(queue_id, "")
        parts: list[str] = []
        if desc:
            parts.append(desc)
        if isinstance(queue_id, int) and queue_id > 0:
            parts.append(f"Queue {queue_id}")
        detail = " · ".join(parts)
        self._info.game_status = f"{label} — {detail}" if detail else label

    def _apply_lobby(self, lobby) -> None:
        if not isinstance(lobby, dict):
            self._info.selected_roles = DASH
            return
        member = lobby.get("localMember") or {}
        first = member.get("firstPositionPreference") or ""
        second = member.get("secondPositionPreference") or ""
        p1 = _POSITION_LABELS.get(first, first or DASH)
        p2 = _POSITION_LABELS.get(second, second or DASH)
        if p1 == DASH and p2 == DASH:
            self._info.selected_roles = DASH
        elif p2 == DASH or p1 == p2:
            self._info.selected_roles = p1
        else:
            self._info.selected_roles = f"{p1} / {p2}"

    def _apply_champ_select_phase(self, session) -> None:
        if not isinstance(session, dict):
            self._info.champ_select_phase = DASH
            return
        timer_phase = (session.get("timer") or {}).get("phase") or ""
        friendly = _CS_PHASE_LABELS.get(timer_phase, timer_phase or DASH)
        # Refine to a more specific label if it's currently my turn.
        local_cell = session.get("localPlayerCellId")
        for group in session.get("actions") or []:
            for action in group or []:
                if action.get("actorCellId") == local_cell and action.get("isInProgress"):
                    if action.get("type") == "ban":
                        friendly = "Banning"
                    elif action.get("type") == "pick":
                        friendly = "Picking"
                    break
        self._info.champ_select_phase = friendly

    def _emit_info(self) -> None:
        # Send a snapshot so the GUI's queue owns its own state.
        self.info_cb(replace(self._info))

    async def _get_json(self, connection, url: str) -> Any:
        """GET helper that returns parsed JSON or None on any failure. The
        LCU emits shaped error dicts on 4xx; callers ignore them by looking
        for the fields they actually need."""
        try:
            resp = await connection.request("get", url)
            return await resp.json()
        except Exception:
            return None

    async def _available_ids(self, connection, url: str) -> set[int] | None:
        try:
            resp = await connection.request("get", url)
            data = await resp.json()
            if isinstance(data, list):
                return {int(cid) for cid in data}
        except (asyncio.TimeoutError, ValueError, TypeError, KeyError):
            pass
        return None

    def run(self) -> None:
        """Blocking: starts the connector event loop (call from a thread)."""
        self.connector = self._build_connector()
        self.connector.start()

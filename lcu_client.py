"""LCU integration: connects to the running League client and drives the
auto-accept / auto-ban / auto-pick automation off websocket events.

`lcu-driver` handles lockfile discovery, auth, HTTPS/WSS, and reconnection. We
register three handlers:

  * connection ready/close  -> report status to the GUI
  * ready-check UPDATE      -> auto-accept
  * champ-select session    -> decide_action() then complete the ban/pick

`connector.start()` blocks on its own asyncio loop, so main.py runs an
LcuController in a daemon thread.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from lcu_driver import Connector

from champ_select import decide_action

BANNABLE_URL = "/lol-champ-select/v1/bannable-champion-ids"
PICKABLE_URL = "/lol-champ-select/v1/pickable-champion-ids"


class LcuController:
    """Owns the lcu-driver Connector and the automation handlers.

    Args:
        config: the shared AppConfig (read via .snapshot() on each event).
        status_cb: called with a short status string ("Connected", etc.);
            the GUI marshals this onto the Tk thread.
    """

    def __init__(self, config, status_cb: Callable[[str], None]):
        self.config = config
        self.status_cb = status_cb
        self._submitted: set[int] = set()

        self.connector = Connector()
        self.connector.ready(self._on_ready)
        self.connector.close(self._on_close)
        self.connector.ws.register(
            "/lol-matchmaking/v1/ready-check", event_types=("CREATE", "UPDATE")
        )(self._on_ready_check)
        self.connector.ws.register(
            "/lol-champ-select/v1/session", event_types=("CREATE", "UPDATE", "DELETE")
        )(self._on_champ_select)

    async def _on_ready(self, connection):
        self.status_cb("Connected to League client")

    async def _on_close(self, connection):
        self.status_cb("League client closed — waiting…")

    async def _on_ready_check(self, connection, event):
        cfg = self.config.snapshot()
        if not cfg.auto_accept:
            return
        data = event.data or {}
        if data.get("state") == "InProgress" and data.get("playerResponse") != "Accepted":
            await connection.request("post", "/lol-matchmaking/v1/ready-check/accept")
            self.status_cb("Auto-accepted match")

    async def _on_champ_select(self, connection, event):
        if getattr(event, "type", None) == "Delete" or event.data is None:
            self._submitted.clear()
            return

        session = event.data
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
        self.connector.start()

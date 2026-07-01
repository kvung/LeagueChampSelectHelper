"""Pure decision logic for champion select.

`decide_action` takes a champ-select session (the dict delivered by the LCU
`/lol-champ-select/v1/session` websocket event) plus a config snapshot, and
returns the single action to perform right now — or None if there's nothing to
do. Keeping this free of any network/IO makes it fully unit-testable off a
Windows machine, using recorded session fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    """An action to complete: PATCH .../actions/{id} with championId + completed."""
    id: int
    champion_id: int
    type: str  # "ban" or "pick"


def _local_cell_id(session: dict) -> int | None:
    cell = session.get("localPlayerCellId")
    return cell if isinstance(cell, int) else None


def assigned_lane(session: dict, local_cell: int) -> str | None:
    """Read the local player's assigned position, or None (e.g. blind/custom)."""
    for member in session.get("myTeam", []) or []:
        if member.get("cellId") == local_cell:
            pos = (member.get("assignedPosition") or "").strip().lower()
            return pos or None
    return None


def _current_action(session: dict, local_cell: int) -> dict | None:
    """The in-progress, not-yet-completed action owned by the local player.

    `session["actions"]` is a list of phases; each phase is a list of action
    dicts. Only one action is ever in progress for us at a time.
    """
    for phase in session.get("actions", []) or []:
        for action in phase:
            if (
                action.get("actorCellId") == local_cell
                and action.get("isInProgress")
                and not action.get("completed")
            ):
                return action
    return None


def _unavailable_champion_ids(session: dict) -> set[int]:
    """Champions already banned or picked/intended anywhere this game.

    Used as a fallback filter so we don't try to ban/pick something that's
    already gone. The live client also exposes pickable/bannable id endpoints;
    the caller may intersect with those for higher accuracy.
    """
    used: set[int] = set()
    for phase in session.get("actions", []) or []:
        for action in phase:
            cid = action.get("championId") or 0
            if cid and action.get("completed"):
                used.add(cid)
    for team_key in ("myTeam", "theirTeam"):
        for member in session.get(team_key, []) or []:
            for key in ("championId", "championPickIntent"):
                cid = member.get(key) or 0
                if cid:
                    used.add(cid)
    return used


def _first_available(priority: list[int], unavailable: set[int]) -> int | None:
    for champ_id in priority:
        if champ_id and champ_id not in unavailable:
            return champ_id
    return None


def decide_action(session: dict, cfg, available_ids: set[int] | None = None) -> Action | None:
    """Decide the ban/pick action to complete, or None.

    Args:
        session: the champ-select session dict from the LCU event.
        cfg: an AppConfig snapshot (auto_ban/auto_pick flags + per-lane lists).
        available_ids: optional set of champion ids the client reports as
            currently pickable/bannable. When provided it further constrains
            the choice; when None we rely solely on the session-derived
            "already used" set.
    """
    local = _local_cell_id(session)
    if local is None:
        return None

    action = _current_action(session, local)
    if action is None:
        return None

    lane = assigned_lane(session, local)
    if lane is None:
        return None  # no assigned lane (blind/custom) -> don't guess

    action_type = action.get("type")
    if action_type == "ban":
        if not cfg.auto_ban:
            return None
        priority = cfg.bans.get(lane, [])
    elif action_type == "pick":
        if not cfg.auto_pick:
            return None
        priority = cfg.picks.get(lane, [])
    else:
        return None

    if not priority:
        return None

    unavailable = _unavailable_champion_ids(session)
    if available_ids is not None:
        # Only consider champions the client says are still selectable.
        unavailable |= {cid for cid in priority if cid not in available_ids}

    champ_id = _first_available(priority, unavailable)
    if champ_id is None:
        return None

    return Action(id=int(action["id"]), champion_id=champ_id, type=action_type)

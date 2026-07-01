"""Unit tests for the pure champ-select decision logic.

Fully OS-independent — no League client or network needed. Run from the project
root with:  python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from champ_select import decide_action, assigned_lane  # noqa: E402
from config import AppConfig  # noqa: E402


def make_session(lane="top", action_type="ban", in_progress=True,
                 completed=False, actor=0, local=0, used_bans=None):
    """Build a minimal champ-select session for a single in-progress action."""
    actions = [[{
        "id": 10,
        "actorCellId": actor,
        "championId": 0,
        "completed": completed,
        "isInProgress": in_progress,
        "type": action_type,
    }]]
    if used_bans:
        actions.insert(0, [
            {"id": 5 + i, "actorCellId": 9, "championId": cid, "completed": True,
             "isInProgress": False, "type": "ban"}
            for i, cid in enumerate(used_bans)
        ])
    return {
        "localPlayerCellId": local,
        "myTeam": [{"cellId": 0, "assignedPosition": lane,
                    "championId": 0, "championPickIntent": 0}],
        "theirTeam": [],
        "actions": actions,
    }


def cfg_with(**kwargs):
    return AppConfig(**kwargs).snapshot()


class DecideActionTests(unittest.TestCase):
    def test_bans_first_available(self):
        cfg = cfg_with(auto_ban=True, bans={"top": [100, 200], "jungle": [],
                                             "middle": [], "bottom": [], "utility": []})
        action = decide_action(make_session(lane="top", action_type="ban"), cfg)
        self.assertIsNotNone(action)
        self.assertEqual(action.type, "ban")
        self.assertEqual(action.id, 10)
        self.assertEqual(action.champion_id, 100)

    def test_skips_already_banned_champion(self):
        cfg = cfg_with(auto_ban=True, bans={"top": [100, 200], "jungle": [],
                                            "middle": [], "bottom": [], "utility": []})
        session = make_session(lane="top", action_type="ban", used_bans=[100])
        action = decide_action(session, cfg)
        self.assertEqual(action.champion_id, 200)  # 100 taken -> next in list

    def test_picks_by_lane(self):
        cfg = cfg_with(auto_pick=True, picks={"middle": [777], "top": [],
                                              "jungle": [], "bottom": [], "utility": []})
        action = decide_action(make_session(lane="middle", action_type="pick"), cfg)
        self.assertEqual(action.type, "pick")
        self.assertEqual(action.champion_id, 777)

    def test_none_when_not_my_turn(self):
        cfg = cfg_with(auto_ban=True, bans={"top": [100], "jungle": [],
                                            "middle": [], "bottom": [], "utility": []})
        session = make_session(lane="top", action_type="ban", actor=3, local=0)
        self.assertIsNone(decide_action(session, cfg))

    def test_none_when_feature_disabled(self):
        cfg = cfg_with(auto_ban=False, bans={"top": [100], "jungle": [],
                                             "middle": [], "bottom": [], "utility": []})
        self.assertIsNone(decide_action(make_session(lane="top", action_type="ban"), cfg))

    def test_none_when_no_assigned_lane(self):
        cfg = cfg_with(auto_ban=True, bans={"top": [100], "jungle": [],
                                            "middle": [], "bottom": [], "utility": []})
        session = make_session(lane="", action_type="ban")
        self.assertIsNone(decide_action(session, cfg))

    def test_none_when_action_completed(self):
        cfg = cfg_with(auto_ban=True, bans={"top": [100], "jungle": [],
                                            "middle": [], "bottom": [], "utility": []})
        session = make_session(lane="top", action_type="ban", completed=True, in_progress=False)
        self.assertIsNone(decide_action(session, cfg))

    def test_available_ids_filter(self):
        cfg = cfg_with(auto_pick=True, picks={"bottom": [100, 200], "top": [],
                                              "jungle": [], "middle": [], "utility": []})
        session = make_session(lane="bottom", action_type="pick")
        action = decide_action(session, cfg, available_ids={200})
        self.assertEqual(action.champion_id, 200)

    def test_empty_priority_list_returns_none(self):
        cfg = cfg_with(auto_pick=True)  # all lanes empty by default
        self.assertIsNone(decide_action(make_session(action_type="pick"), cfg))


class AssignedLaneTests(unittest.TestCase):
    def test_reads_assigned_position(self):
        session = make_session(lane="jungle")
        self.assertEqual(assigned_lane(session, 0), "jungle")

    def test_empty_position_is_none(self):
        session = make_session(lane="")
        self.assertIsNone(assigned_lane(session, 0))


if __name__ == "__main__":
    unittest.main()

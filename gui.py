"""tkinter GUI: enable checkboxes + per-lane ban/pick priority dropdowns.

For each lane there are three ban slots and three pick slots. The slot order IS
the priority order (slot 1 = highest). Empty slots are ignored. Any change is
written straight back into the shared AppConfig and saved to disk, so the LCU
background thread always reads current settings via config.snapshot().

Status messages from the LCU thread arrive through a Queue and are drained on
the Tk thread via root.after(), which keeps all widget access single-threaded.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

from config import AppConfig, LANES, LANE_LABELS
from champions import Champions

SLOTS = 3
_BLANK = ""


class App:
    def __init__(self, cfg: AppConfig, champions: Champions):
        self.cfg = cfg
        self.champions = champions
        self._status_queue: "queue.Queue[str]" = queue.Queue()

        self.root = tk.Tk()
        self.root.title("League Champ Select Helper")
        self.root.resizable(False, False)

        self._champ_values = [_BLANK] + champions.names()
        self._combos: dict[str, dict[str, list[ttk.Combobox]]] = {}

        self._build_toggles()
        self._build_lane_grid()
        self._build_status_bar()
        self._load_into_widgets()

        self.root.after(100, self._drain_status)

    def _build_toggles(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Automation")
        frame.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

        self.var_accept = tk.BooleanVar(value=self.cfg.auto_accept)
        self.var_ban = tk.BooleanVar(value=self.cfg.auto_ban)
        self.var_pick = tk.BooleanVar(value=self.cfg.auto_pick)

        ttk.Checkbutton(frame, text="Auto Accept", variable=self.var_accept,
                        command=self._on_toggle).grid(row=0, column=0, padx=8, pady=6)
        ttk.Checkbutton(frame, text="Auto Ban", variable=self.var_ban,
                        command=self._on_toggle).grid(row=0, column=1, padx=8, pady=6)
        ttk.Checkbutton(frame, text="Auto Pick", variable=self.var_pick,
                        command=self._on_toggle).grid(row=0, column=2, padx=8, pady=6)

    def _build_lane_grid(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Priority per lane (slot 1 = highest)")
        frame.grid(row=1, column=0, padx=10, pady=4, sticky="ew")

        ttk.Label(frame, text="Lane").grid(row=0, column=0, padx=6, pady=(6, 2))
        for i in range(SLOTS):
            ttk.Label(frame, text=f"Ban {i + 1}").grid(row=0, column=1 + i, padx=4, pady=(6, 2))
        for i in range(SLOTS):
            ttk.Label(frame, text=f"Pick {i + 1}").grid(row=0, column=1 + SLOTS + i, padx=4, pady=(6, 2))

        for r, lane in enumerate(LANES, start=1):
            ttk.Label(frame, text=LANE_LABELS[lane]).grid(row=r, column=0, padx=6, pady=2, sticky="w")
            bans, picks = [], []
            for i in range(SLOTS):
                cb = self._make_combo(frame)
                cb.grid(row=r, column=1 + i, padx=4, pady=2)
                bans.append(cb)
            for i in range(SLOTS):
                cb = self._make_combo(frame)
                cb.grid(row=r, column=1 + SLOTS + i, padx=4, pady=2)
                picks.append(cb)
            self._combos[lane] = {"bans": bans, "picks": picks}

    def _make_combo(self, parent) -> ttk.Combobox:
        cb = ttk.Combobox(parent, values=self._champ_values, state="readonly", width=12)
        cb.set(_BLANK)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._on_champ_change())
        return cb

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Waiting for League client…")
        bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w")
        bar.grid(row=2, column=0, padx=10, pady=(4, 10), sticky="ew")
        if not self.champions.names():
            self.status_var.set("No champion data (offline). Connect once to load names.")

    def _load_into_widgets(self) -> None:
        for lane in LANES:
            self._fill_slots(self._combos[lane]["bans"], self.cfg.bans.get(lane, []))
            self._fill_slots(self._combos[lane]["picks"], self.cfg.picks.get(lane, []))

    def _fill_slots(self, combos: list[ttk.Combobox], champ_ids: list[int]) -> None:
        for i, cb in enumerate(combos):
            name = self.champions.name_for(champ_ids[i]) if i < len(champ_ids) else _BLANK
            cb.set(name if name in self._champ_values else _BLANK)

    def _collect_slots(self, combos: list[ttk.Combobox]) -> list[int]:
        ids: list[int] = []
        for cb in combos:
            name = cb.get()
            if name and name != _BLANK:
                cid = self.champions.id_for(name)
                if cid and cid not in ids:
                    ids.append(cid)
        return ids

    def _on_toggle(self) -> None:
        self.cfg.update(
            auto_accept=self.var_accept.get(),
            auto_ban=self.var_ban.get(),
            auto_pick=self.var_pick.get(),
        )

    def _on_champ_change(self) -> None:
        bans = {lane: self._collect_slots(self._combos[lane]["bans"]) for lane in LANES}
        picks = {lane: self._collect_slots(self._combos[lane]["picks"]) for lane in LANES}
        self.cfg.update(bans=bans, picks=picks)

    def set_status_threadsafe(self, message: str) -> None:
        """Callable from any thread; the Tk thread drains it via after()."""
        self._status_queue.put(message)

    def _drain_status(self) -> None:
        try:
            while True:
                self.status_var.set(self._status_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._drain_status)

    def run(self) -> None:
        self.root.mainloop()

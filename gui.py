"""tkinter GUI: enable checkboxes + per-lane ban/pick priority dropdowns.

Lanes are shown as notebook tabs (Top / Jungle / Middle / Bottom / Support).
Each tab has two rows — Bans, then Picks — with three priority slots each
(slot 1 = highest). Champion dropdowns are searchable: typing filters the
list as you type; on focus-out, partial text is resolved by case-insensitive
prefix match (or cleared if nothing matches).

Any change is written straight back into the shared AppConfig and saved to
disk, so the LCU background thread always reads current settings via
config.snapshot().

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
_NAV_KEYS = frozenset({"Up", "Down", "Left", "Right", "Return", "Escape",
                       "Tab", "ISO_Left_Tab", "Shift_L", "Shift_R",
                       "Control_L", "Control_R", "Alt_L", "Alt_R"})


class App:
    def __init__(self, cfg: AppConfig, champions: Champions):
        self.cfg = cfg
        self.champions = champions
        self._status_queue: "queue.Queue[str]" = queue.Queue()

        self.root = tk.Tk()
        self.root.title("League Champ Select Helper")
        self.root.resizable(False, False)

        self._champ_names = champions.names()
        self._champ_values = [_BLANK] + self._champ_names
        self._name_by_lower = {n.lower(): n for n in self._champ_names}
        self._combos: dict[str, dict[str, list[ttk.Combobox]]] = {}

        self._build_toggles()
        self._build_lane_tabs()
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

    def _build_lane_tabs(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Priority per lane (Priority 1 = highest)")
        frame.grid(row=1, column=0, padx=10, pady=4, sticky="ew")

        notebook = ttk.Notebook(frame)
        notebook.grid(row=0, column=0, padx=6, pady=6, sticky="ew")

        for lane in LANES:
            tab = ttk.Frame(notebook, padding=(12, 10))
            notebook.add(tab, text=LANE_LABELS[lane])
            bans, picks = self._build_lane_tab(tab)
            self._combos[lane] = {"bans": bans, "picks": picks}

    def _build_lane_tab(self, parent: ttk.Frame) -> tuple[list[ttk.Combobox], list[ttk.Combobox]]:
        """Populate one lane's tab: 'Slot N' headers on row 0, then Bans row,
        then Picks row. Returns the (ban_combos, pick_combos) lists."""
        for i in range(SLOTS):
            ttk.Label(parent, text=f"Priority {i + 1}").grid(
                row=0, column=1 + i, padx=4, pady=(0, 4)
            )

        ttk.Label(parent, text="Bans:").grid(row=1, column=0, padx=(0, 8), pady=4, sticky="e")
        bans = [self._make_combo(parent) for _ in range(SLOTS)]
        for i, cb in enumerate(bans):
            cb.grid(row=1, column=1 + i, padx=4, pady=4)

        ttk.Label(parent, text="Picks:").grid(row=2, column=0, padx=(0, 8), pady=4, sticky="e")
        picks = [self._make_combo(parent) for _ in range(SLOTS)]
        for i, cb in enumerate(picks):
            cb.grid(row=2, column=1 + i, padx=4, pady=4)

        return bans, picks

    def _make_combo(self, parent) -> ttk.Combobox:
        cb = ttk.Combobox(parent, values=self._champ_values, width=18)
        cb.set(_BLANK)
        cb.bind("<KeyRelease>", self._on_combo_key)
        cb.bind("<<ComboboxSelected>>", self._on_combo_select)
        cb.bind("<FocusOut>", self._on_combo_focus_out)
        return cb

    def _on_combo_key(self, event) -> None:
        # Ignore navigation / modifier keys so filtering doesn't fire on them.
        if event.keysym in _NAV_KEYS:
            return
        cb: ttk.Combobox = event.widget
        typed = cb.get().strip()
        if not typed:
            cb["values"] = self._champ_values
            return
        needle = typed.lower()
        matches = [n for n in self._champ_names if needle in n.lower()]
        # Empty list breaks the popdown on some Tk builds; fall back to blank.
        cb["values"] = matches if matches else [_BLANK]

    def _on_combo_select(self, _event) -> None:
        self._on_champ_change()

    def _on_combo_focus_out(self, event) -> None:
        cb: ttk.Combobox = event.widget
        current = cb.get().strip()
        resolved = _BLANK
        if current:
            needle = current.lower()
            exact = self._name_by_lower.get(needle)
            if exact:
                resolved = exact
            else:
                resolved = next(
                    (n for n in self._champ_names if n.lower().startswith(needle)),
                    _BLANK,
                )
        cb.set(resolved)
        cb["values"] = self._champ_values
        self._on_champ_change()

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Waiting for League client…")
        bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w")
        bar.grid(row=2, column=0, padx=10, pady=(4, 10), sticky="ew")
        if not self._champ_names:
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

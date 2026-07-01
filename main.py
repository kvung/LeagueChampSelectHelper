"""Entry point: load config + champion data, start the LCU controller on a
background daemon thread, and run the tkinter GUI on the main thread.
"""
from __future__ import annotations

import asyncio
import threading

import config as config_mod
from champions import load_champions
from gui import App
from lcu_client import LcuController


def _run_lcu(controller: LcuController) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        controller.run()
    except Exception as exc:
        controller.status_cb(f"LCU error: {exc}")


def main() -> None:
    cfg = config_mod.load()
    champions = load_champions()

    app = App(cfg, champions)
    controller = LcuController(cfg, app.set_status_threadsafe)

    thread = threading.Thread(target=_run_lcu, args=(controller,), daemon=True)
    thread.start()

    app.run()


if __name__ == "__main__":
    main()

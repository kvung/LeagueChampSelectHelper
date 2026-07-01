# League Champ Select Helper

A small Windows tool with a checkbox GUI that automates the League of Legends
champion-select flow via the **LCU API** (the local REST/WebSocket API the
League client exposes on your own machine). It can:

- **Auto Accept** the ready-check when a match is found.
- **Auto Ban** a champion based on your assigned lane.
- **Auto Pick** a champion based on your assigned lane.

Bans and picks use an **ordered priority list per lane** (5 lanes × 3 ban slots
× 3 pick slots). Slot 1 is highest priority; the tool bans/picks the first
champion in the list that is still available.

> It talks only to the League **client** through the client's own local API
> (auth is read from the client `lockfile`). It does **not** read or modify the
> game, memory, or network traffic.

## Terms of Service

Riot's Terms of Service prohibit third-party automation of the client. Even
though this is a personal convenience tool that gives no in-game advantage,
using it carries a risk of account action. Use it at your own risk.

## How it works

- [`lcu-driver`](https://github.com/sousa-andre/lcu-driver) discovers the running
  client via its `lockfile`, authenticates, and delivers WebSocket events.
- On a ready-check event → `POST /lol-matchmaking/v1/ready-check/accept`.
- On a champ-select session event → read your `assignedPosition`, find your
  in-progress ban/pick action, choose the top available champion from your list,
  and `PATCH /lol-champ-select/v1/session/actions/{id}` with `completed: true`.
- Champion names for the dropdowns come from Riot's Data Dragon and are cached to
  `champions.json`.

## Project layout

| File              | Purpose                                                  |
| ----------------- | -------------------------------------------------------- |
| `main.py`         | Entry point: starts the LCU thread + GUI                 |
| `gui.py`          | tkinter window: checkboxes + per-lane priority dropdowns |
| `lcu_client.py`   | `lcu-driver` connector + accept/ban/pick handlers        |
| `champ_select.py` | Pure `decide_action()` logic (unit-tested)               |
| `champions.py`    | Champion id↔name from Data Dragon, cached                |
| `config.py`       | Settings model + `config.json` persistence               |
| `tests/`          | Unit tests for the decision logic                        |

## Run from source (Windows, with the client installed)

```bat
python -m pip install -r requirements.txt
python main.py
```

Tick the checkboxes and choose champions per lane. Settings save automatically to
`config.json` next to the script/exe.

## Build the `.exe` (must be done on Windows)

PyInstaller cannot cross-compile a Windows `.exe` from macOS/Linux — run this on
a Windows machine (or a Windows VM / CI runner):

```bat
build.bat
```

Output: `dist\LeagueChampSelectHelper.exe` — a standalone executable that runs
without Python installed.

## Develop / test on macOS

The GUI and LCU pieces need Windows + the client, but the core decision logic is
pure and testable anywhere:

```bash
python3 -m unittest discover -s tests -v
```

## Notes

- If PyInstaller fails with a `psutil` / `AccessDenied` error, run
  `python -m pip install -U psutil` and rebuild (a known `lcu-driver` quirk).
- Auto-pick/ban only trigger when you have an **assigned lane** (e.g. ranked /
  draft). In blind pick or custom games there is no `assignedPosition`, so the
  tool stays out of the way.

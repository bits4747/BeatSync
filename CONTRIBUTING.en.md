# Contributing

English | [中文](CONTRIBUTING.md)

Thanks for wanting to help with BeatSync. This document covers local development, testing and commit conventions.

## Setting up

You need **Python 3.11+**.

```bash
git clone <your fork URL>
cd BeatSync
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev,gui]"
```

The `gui` extra installs `pywebview` (needed by the WebView desktop UI). Without it you can still run the
command line and the tkinter compatibility UI.
If you only want to try the program out and don't plan to change code, `pip install -r requirements.txt` is enough.

## Project layout

| Path | What it is |
| --- | --- |
| `tempo_converter.py` | Core: tempo detection, plan selection, stretching, metronome synthesis, batch, report, CLI |
| `tempo_converter_app.py` | Python backend for the WebView desktop app (JS ↔ Python bridge) |
| `tempo_converter_gui.py` | tkinter compatibility UI (fallback when WebView won't start) |
| `frontend/` | Desktop UI (plain HTML/CSS/JS, no build step — edit and reload) |
| `tests/` | Unit tests |
| `assets/` | Icons: `icon.svg` is the source file, `icon.png` / `icon.ico` are derived from it |

## Running the tests

```bash
pytest tests/ -v
```

The unit tests **do not need** any audio files — they synthesise click tracks in memory and finish within
tens of seconds. Run them before touching the detection / stretching / metronome / segmentation code.

When you change the UI (`frontend/`), previewing it in a browser is not enough: also run the desktop app with
`python tempo_converter_app.py` — the behaviour of the JS bridge cannot be simulated in a plain browser.

## Conventions

### Commit messages

Chinese or English, either is fine, but **the first line must say what changed**, and the body should say **why**.
Suggested prefixes:

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `test:` tests only
- `refactor:` refactor (no behaviour change)
- `chore:` build, dependencies, miscellaneous

Any behaviour change (especially output format, defaults, file naming) must spell out in the body:
"old behaviour → new behaviour → which users are affected".

### Code style

- Keep the existing style: type hints, and comments that explain *why* rather than *what*.
- Every new edge case needs a test; algorithm changes need reproducible numbers
  (measured before / after, not "feels faster").
- Do not add network calls — this project promises to run fully offline.

### Changes that touch defaults

Defaults such as `DEFAULT_MAX_SLOWDOWN` / `DEFAULT_MAX_SPEEDUP` decide which tracks get skipped. Before changing
them, include in the PR a before / after table of the resulting plans for the six source tempos
`60 / 85 / 100 / 120 / 150 / 180`.

## Version number and changelog

The current version is **1.0.0**, the first public release. **Every release must bump the version**, and these
three places must agree:

1. `version` in `pyproject.toml`
2. the title of the newly added top section in `CHANGELOG.md`
3. `MyAppVersion` in `_packaging/BeatSync.iss` (installer version; that script lives outside this repository)

How the version number should move (bug fix / new feature / breaking change) is documented at the top of
`CHANGELOG.md`. `CHANGELOG.md` only records **user-visible behaviour**; internal refactors don't need an entry.

## Sending a PR

1. Branch off the latest `main`: `git checkout -b fix/xxx`
2. Run `pytest tests/ -v` and make sure everything passes
3. Add a line to the current version section of `CHANGELOG.md` (or open a new section per the rules above)
4. Open the PR and describe the motivation, the approach and how you verified it

## Reporting issues

Please include:

- your OS and Python version (`python -V`)
- the **source BPM**, the target BPM, and the exact flags you used
- what you got versus what you expected

If the problem is "tempo was detected incorrectly", please mention the track's time signature (4/4, 3/4, 6/8, …).
Beat detection has a known octave ambiguity on non-4/4 material and on extreme tempos below 70 BPM or above
180 BPM; passing `--bpm` to set the source tempo manually works around it, so this detail matters.

# BeatSync

![icon](assets/icon.svg)

Batch-convert your music to the tempo you need.

English | [中文](README.md)

- **Runners**: fit your music to your cadence, so every step lands on a beat.
- **Musicians**: practise fundamentals at a given BPM with music playing; adjust a track's BPM precisely.

BeatSync avoids large tempo changes — **the metronome always clicks at the target tempo, and the music is only shifted the smallest possible amount.**

## How tempo matching works

Move the music to **n times or 1/n times** the target tempo (n is a whole number).

1. **Detect**: measure the source tempo A (or specify it by hand).
2. **Choose**: among all candidates, pick the one that changes the tempo least.
3. **Respect the limits**: the change must stay inside the allowed range, otherwise the other direction is tried; if both directions exceed the limits, the file is skipped.

### Example

Target 180 BPM, default limits: slow down 20%, speed up 50% (both adjustable)

| Source | Result                                                     |
| ------ | ---------------------------------------------------------- |
| 60     | music stays at 60, **no change**; 3 clicks per beat        |
| 85     | music at 90 (+5.9%); 2 clicks per beat                     |
| 100    | music at 90 (-10%); 2 clicks per beat                      |
| 120    | music at 180 (+50%); 1 click per beat                      |

The table holds for both **automatic detection** and **a manually specified tempo** (all figures are measured). A source of 120 sits exactly on the +50% limit, so the check leaves a 0.5% margin to absorb detection jitter — otherwise a track measured at 119.99 would be judged "needs +50.0%, limit 50%" and skipped entirely.

Automatic detection is most reliable in the **70–180 BPM** range. When the real tempo clearly falls outside it (a 360 BPM track is measured at about 120), pass `--bpm` to state the source tempo yourself: `--bpm 360` then gives "music unchanged, 1 click every 2 beats".

## Highlights

- **Smallest tempo change**: the music is only moved to a position that is a simple multiple of the target tempo (n times or 1/n times, n a whole number), choosing the least change while keeping the beat natural.
- **Adjustable speed-up / slow-down limits**: set how far it may slow down and how far it may speed up; anything beyond the limits is left alone rather than stretched until it sounds broken.
- **Accurate detection, metronome that never drifts**: detection uses autocorrelation refined on a comb grid, so steady-tempo tracks are pinned down to roughly 0.1 BPM. The metronome does not stack fixed intervals; it is placed directly on the music's own beat time line, so it never drifts or misses a beat.
- **Per-section speed change**: for tracks that change tempo or time signature midway, the music is not stretched in one go — it is split into sections and each section is brought to the target tempo precisely.
- **Six percussion sounds, adjustable relative level**: audition and pick a sound; its level follows the track's own loudness and the relative amount is adjustable.
- **Batch processing**: process a whole library in one go.
- **Several output formats**: WAV / MP3 / FLAC. MP3 output **matches the input file's bitrate by default** (no quality downgrade); you can also pick 320/256/192/160/128 and so on explicitly.
- **Keep the folder structure**: when converting a whole library, the output directory mirrors the input tree instead of piling every track into one level; this can be turned off in the interface (output then lands flat in the output directory).
- **Bilingual interface**: switch between Chinese and English with the "中 / EN" button in the top-right corner.

## Requirements

| Item      | Requirement |
| --- | --- |
| System    | **Windows 10 (1803 or later) or Windows 11, 64-bit** (recommended; this is what the installer targets). On Windows 7 / 8.1 you can still run the command line and the tkinter interface from source. |
| Runtime   | **WebView2 Runtime** (usually present on Win10/11, or installed by system updates) and **.NET Framework 4.x** (bundled with Win10/11) |
| Disk      | about **700 MB** installed (all dependencies included; the installer itself is about 150 MB) |
| Memory    | 4 GB is enough; 8 GB recommended for very long audio |
| Network   | **no internet connection is needed to install or to use it** |
| Python    | **with the installer you do not need Python**; running from source needs **Python 3.11+** |

Availability per platform:

| Platform | Command line (`tempo_converter.py`) | Graphical interface |
|---|---|---|
| Windows 10 / 11 | available (**source runs only**) | fully available (installer, recommended) |
| Windows 7 / 8.1 | available (**source runs only**) | desktop version will not start (no WebView2); the tkinter interface works |
| macOS / Linux | available (**source runs only**) | needs a little porting (the interface code currently contains Windows-specific calls) |

> The installer offers the Windows graphical interface only, and it has **no command line** — every "command line" entry in these tables means running from source.
> The installer itself is compiled for **Windows 10 (1803 or later) / Windows 11 64-bit**; for older systems and other platforms, run from source.

- **The command line and the core algorithm (detection, stretching, metronome, batching, CSV report) are cross-platform**: they only depend on librosa / soundfile / numpy / scipy, all of which ship official wheels, so they run wherever those are installed.
- **The graphical interface is currently written for Windows.** Moving it to macOS / Linux means replacing a few platform-specific calls: the desktop shell's "show in folder" uses `explorer /select` (use `open -R` on macOS, `xdg-open` on Linux), and the tkinter version uses `windll.shcore.SetProcessDpiAwareness` and a `.ico` icon. pywebview itself is cross-platform (Cocoa on macOS, GTK/Qt on Linux), so this is a matter of swapping a few branches, not rewriting the interface. **This repository provides Windows source; on non-Windows systems the ready-to-use entry point is the command line.**

- **Windows 10 (1803 or later) or Windows 11 is recommended**: the desktop version needs the WebView2 Runtime (usually present or added by updates on newer systems) and .NET Framework 4.x (bundled with Win10/11).
- **Windows 7 / 8.1**: the command line or the tkinter interface works when run from source; the pywebview desktop version basically cannot start because WebView2 does not support those systems.

---

## Quick start

### Installer (recommended, ordinary users)

Download `BeatSyncSetup.exe` and click Next through the wizard; shortcuts are created on the desktop and in the Start menu.
It installs to `C:\Users\<you>\AppData\Local\Programs\BeatSync` by default.
During setup you can choose whether to create a desktop shortcut, and the program folder also carries the Chinese and English manuals (`README.md` / `README.en.md`) and the license (`LICENSE`).

**The folder holding `BeatSyncSetup.exe` must not contain Chinese characters** — only plain ASCII letters, digits and symbols.
Double-clicking it from a folder whose path contains Chinese characters does nothing at all (it exits silently); move it to a plain ASCII path.

**What the installer gives you**: two graphical interfaces (desktop version + compatibility interface); every conversion feature is available in the interface, with no Python, no network and no command line needed.

### Running from source (developers)

Requires **Python 3.11+**:

```bash
pip install -r requirements.txt
python tempo_converter_app.py
```

On Windows you can also just double-click `BeatSync.bat` (a launcher that lives in the **source repository**, shipped with the source — the installer does not include it).

The interface defaults to Chinese; the "中 / EN" button in the top-right corner switches to English. See [Interface language](#interface-language).

### Two interfaces: desktop and compatibility

**The installer bundles two interfaces with identical conversion features** (same core code, one-to-one parameters and controls); only the look differs:

| Interface | Dependencies | Notes |
| --- | --- | --- |
| **Desktop** (default) | needs WebView2 and .NET | richer and better looking |
| **Compatibility** (tkinter) | **neither is needed** | plain looking, but still runs on machines without WebView2 / .NET |

### If the desktop version will not open, how to reach the compatibility interface

The desktop version depends on WebView2 and .NET (pythonnet). On a few machines it fails to start because one of those components is missing or its load is refused
(the typical error is `Win32Exception: Access is denied`, and it is written to `app_error.log` in the program folder).

**Once the desktop version will not open, no button inside the interface can be clicked** — so start the compatibility interface **from the Start menu**:

1. Click the Start menu (or press `Win`) and pick **BeatSync (Compatibility)** from the app list.
2. **Double-click `BeatSync-Compatibility.bat` in the install folder** (simplest, recommended).

When the desktop version does open, there is also a "switch to compatibility interface" entry at the bottom of the main window.

**When running from source** (no installer), run the tkinter version directly:

```bash
python tempo_converter_gui.py        # run the tkinter version directly
python tempo_converter_app.py --tk   # make the main program use tkinter
```

### Hide to the system tray on close

Clicking the window's **×** does **not** quit by default — it hides the window into
the system tray and the app keeps running in the background. A conversion that is
still in progress is not interrupted.

- To get the window back: **left-click the tray icon**, or right-click and pick
  **Show window**.
- To really terminate the program: right-click the tray icon and pick **Quit**.
- The tray icon is the application's own icon — the same `assets/icon.ico` used by
  the window, the taskbar and the desktop shortcut.
- To turn the behaviour off: clear **Hide to the system tray on close** on the last
  row of *Conversion settings* (bottom of the parameter area in the compatibility
  UI). The × then quits immediately. The value is stored in `ui_prefs.json` next to
  the executable and shared by both UIs.

The tray is **optional**: Windows only, and only when `pystray` and `Pillow` are
installed (bundled in the installer). Running from source without those two
packages greys out the checkbox and explains why, so a checked box that does
nothing can never happen.

---

## Command line (source runs only)

> **The installer has no command line entry.** The installed `BeatSync.exe` is a GUI program:
> it does not accept command-line arguments and does not write to the console (double-clicking or starting it with arguments only opens the interface).
> To use the command line, install Python and the dependencies as described in [Running from source](#running-from-source-developers), then run the commands below from the source directory.

A complete command line entry point is available in addition to the graphical interface.

```bash
# convert to 180 BPM
python tempo_converter.py song.mp3 -t 180

# add a woodblock metronome
python tempo_converter.py song.mp3 -t 180 --metronome woodblock

# batch-convert a whole folder (including subfolders)
python tempo_converter.py ./music -t 180 --recursive

# one output directory (keeps the original folder tree)
python tempo_converter.py ./music -t 180 --recursive -o D:\out

# same, but all flat
python tempo_converter.py ./music -t 180 --recursive -o D:\out --no-mirror-dirs

# custom tempo-change limits
python tempo_converter.py song.mp3 -t 180 --max-slowdown 15 --max-speedup 30

# detect the tempo only, do not convert
python tempo_converter.py song.mp3 --detect-only

# specify the source tempo by hand, skipping detection
python tempo_converter.py song.flac -t 90 --bpm 88

# stretch precisely to the target tempo
python tempo_converter.py song.mp3 -t 180 --strict

# per-section speed change: split at detected tempo changes, stretch each section on its own
# (auto segmentation is the default, so this flag is usually unnecessary;
#  --segments off disables it, --segments on forces it)
python tempo_converter.py song.mp3 -t 180 --segments on
```

The output is Chinese by default; add `--lang en` to make the console output and the report English.
For every option see `python tempo_converter.py --help` (Chinese) or `--lang en --help` (English).

### Option reference

| Option | Meaning |
|---|---|
| `-t, --target` | target BPM (may be omitted with `--detect-only`) |
| `--bpm` | specify the source tempo by hand, skipping detection |
| `--detect-only` | detect the tempo only, do not convert |
| `--strict` | stretch precisely to the target BPM (no multiple relationship; the change may be large) |
| `--metronome` | add a metronome and pick its sound (off by default) |
| `--click-rel-db` | metronome level relative to the music (dB), default -6 |
| `--engine` | `phasevocoder` keeps the pitch (default) or `resample` for a tape-style shift |
| `--format` | `source` (default, same as the input) / `wav` / `mp3` / `flac` / `ogg` |
| `--mp3-bitrate` | mp3 output bitrate: `auto` (default, match the input file) / `320` / `256` / `224` / `192` / `160` / `128` / `112` / `96` |
| `-o, --out-dir` | output directory (default: `tempo_converted/` beside each source file) |
| `--no-mirror-dirs` | with an output directory, do not mirror the tree — write everything flat |
| `--recursive` | search subfolders recursively |
| `--report` | CSV report path (default `conversion_report.csv` in the current directory) |
| `--max-slowdown` | maximum slow-down percentage, default 20 |
| `--max-speedup` | maximum speed-up percentage, default 50 |
| `--segments` | three-state per-section mode: `auto` (default — segment automatically when a clear tempo change is detected), `on` (force segmentation; any infeasible section skips the whole track), `off` (always one rate for the whole track); a bare flag means `on`; ignored with `--bpm` |

---

## Per-section speed change

A track is matched to a single detected BPM, but if a clear tempo change is detected inside it (sections differ by ≥15%) it is split into speed sections, each stretched with its own minimum-stretch plan. You can also force segmentation with `--segments on` or disable it with `--segments off` (or the "Auto per-section" switch in either interface).

Why it matters: on tracks like Rush's "YYZ", the intro runs at a different tempo from the body. One global rate (140→180, ×1.28) leaves the intro's metronome completely off — the intro's own pulse is around 208 BPM, so after stretching it sits near 266, nowhere near the 180 clicks. With segmentation the intro is handled at its own tempo and the clicks land.

**Neighbouring sections are merged automatically**: when two sections differ only by measurement jitter (same output tempo and the same click density per beat) they are not split apart — every split means another splice and another seam. Before merging, the combined section is re-measured, and the merge only happens if its beat grid lines up at least as well as the better of the two originals; a genuine tempo change makes that alignment drop, so it is never swallowed.

**Layer jitter inside one piece of music collapses into a single section**: frame-by-frame tempo estimation sometimes counts the same music at different metrical layers (90 and 120 alternating, for example), which looks like a tempo change but is only a different way of counting. When the section tempi all fall inside one integer-ratio family (up to 16×, tolerance about 1.35×) and the tempo does not move in one direction only, they are treated as layer jitter and merged; inside a family, the layer is chosen purely by stretch cost — the one with the smallest stretch wins. A genuine one-directional change (90→120→150) does not qualify and is still split.

**Detected tempo converges to an integer-ratio family**: the reported tempo is an integer multiple or integer fraction of the real tempo (n or 1/n), never a fraction such as 2/3 or 3/2 — those cannot be compensated for and would put the clicks off the beat. Within a family the cheapest layer wins, so a track that is really 180 may be reported as 90 (1/2, two clicks per beat, still on the beat).

**Sections whose beat is genuinely ambiguous can still miss the integer ratio**: a few stretches have two almost equally good tempo candidates (the YYZ intro measures 102.8 against a hand-labelled 114, a ratio of 1.109) and the program can only pick the better-scoring one. Use "Source BPM" to set such a tempo by hand.

**Gradual changes (accelerando / ritardando) are out of scope**: such a track has no constant tempo, so it is processed at one global rate and the clicks drift away from the beat towards the beginning and the end. Pass a representative tempo with `--bpm`, or cut the track into steady-tempo pieces and process them separately.

## Output

**Naming**: `original_<target>bpm.ext` (for example `song_180bpm.wav`); on a name clash `_1`, `_2`, `_3` are appended.

**Location**: by default into `tempo_converted/` beside each source file;
with `-o` the input tree is mirrored into that directory.

**Report**: CSV, written to `conversion_report.csv` in the current directory by default.

---

## Supported input formats

| Format | Notes |
| --- | --- |
| **WAV / MP3 / FLAC / OGG / AIFF** | supported directly, no extra components needed |
| **M4A / AAC / WMA** | **not supported**: the bundled decoder (libsndfile) does not read these containers, and librosa 1.0 no longer falls back to ffmpeg, so **installing ffmpeg does not help**. Convert them to wav / mp3 / flac first |

Both the interface and the command line scan for these extensions; M4A / AAC / WMA files are accepted into the list but produce a clear "the audio decoder does not support this format" message instead of vanishing silently.

Output formats are listed in the option reference under `--format` (`source` / `wav` / `mp3` / `flac` / `ogg`) and in the interface's format selector.
OGG is a command-line-only option: a single write is limited in sample count, so **over-long audio is saved as WAV instead and the remark says so**.

---

## Project layout

**This is the source repository layout** (what you see when running from source or developing on it).
The installer does not unpack these `.py` files — it has `BeatSync.exe`, `BeatSync.exe.config`,
`BeatSync-Compatibility.bat`, an `_internal\` folder, plus `README.md`, `README.en.md`,
`LICENSE` and `assets\`.

```
tempo_converter.py        core: detection / candidate choice / stretching / metronome / batch / report; CLI entry
tempo_converter_app.py    desktop shell: pywebview window + JS bridge + drag and drop + smoke test
tempo_converter_gui.py    tkinter fallback interface
tray_icon.py              system tray (optional deps: pystray + Pillow; hide to tray on close)
frontend/                 index.html + app.css + app.js (no external dependencies, no build step)
tests/                    unit tests (pytest; click tracks are synthesised in memory, no audio files needed)
assets/                   icons (icon.svg source plus the derived icon.png / icon.ico)
BeatSync.bat              double-click launcher on Windows (source only; not in the installer)
pyproject.toml            packaging metadata, dependency groups, pytest configuration
requirements.txt          Python dependencies (used when installing with plain pip)
.github/workflows/ci.yml  GitHub Actions: unit tests across platforms and Python versions
CHANGELOG.md              changelog (including the version-numbering rule)
CONTRIBUTING.md           contribution guide (dev setup, tests, commit conventions)
CONTRIBUTING.en.md        same guide in English
LICENSE                   MIT license
```

Dependencies: librosa, soundfile, numpy, scipy, pywebview (**only needed when running from source**; the installer bundles them).
Hiding to the tray additionally needs pystray + Pillow (also bundled in the installer; without them only the tray is missing, conversion is unaffected).

---

## Interface language

The graphical interface **defaults to Chinese**; the "中 / EN" button in the top-right corner (just left of the day/night mode button) switches between Chinese and English in one click.

- **What switches**: every piece of text in the interface — the file list, parameter labels, tempo-limit hints, sound names (Beep / Woodblock / Cowbell / Click / Sidestick / Clap), buttons, progress and result messages, dialog texts.
- **The button's own tooltip**: in Chinese the button reads "中 / EN" and its tooltip points at "switch to English"; in English it is the other way round.
- After switching in the desktop version, the **log, result notes, click relationships and the CSV report** are all produced by the backend in the same language, so no Chinese leaks into the English interface.
- **The tkinter fallback interface** has the same "中 / EN" button in the top-right corner: switching rebuilds the interface while the added files and all settings are kept as they are. Its conversion parameters match the desktop version: a "keep the original folder structure" checkbox (greyed out when no output directory is set), two tempo-limit boxes (0-100, defaults 20 / 50), and a metronome level that can be typed as a dB value in addition to the slider, all synchronised live.
- **The command line** (source runs only) defaults to Chinese; add `--lang en` (or set the environment variable `BEATSYNC_LANG=en`) to make the console output and the CSV report English. `--lang` only changes the display language and does not affect any algorithm behaviour.

---

## License

This project is released under the **MIT license**; the full text is in [LICENSE](LICENSE).

In short: you may freely use, modify and distribute this software (including commercially), as long as you keep the copyright notice and the license text in copies or substantial portions; the software is provided "as is", without warranty of any kind.

Third-party dependencies (librosa, soundfile, numpy, scipy, pywebview and others) follow their own licenses; see the projects listed in `requirements.txt`.

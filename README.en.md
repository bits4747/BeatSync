# BeatSync

![icon](assets/icon.svg)

Batch-convert your music to the tempo you need.

English | [中文](README.md)

- **Runners**: fit your music to your cadence, so every step lands on a beat.
- **Musicians**: practise fundamentals at a given BPM with music playing; adjust a track's BPM precisely.

BeatSync avoids large tempo changes -- **the metronome always clicks at the target tempo, and the music is only shifted the smallest possible amount.**

## How tempo matching works

Move the music to **n times or 1/n times** the target tempo (n is a whole number).

1. **Detect**: measure the source tempo A (or specify it by hand). The detected value is reported **as measured, without octave folding** — a 60 BPM track is reported as 60 BPM (since v1.1.0; older versions folded slow songs into the [70, 180) range).
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
| 360    | music stays at 360, **no change**; 1 click every 2 beats   |

The table holds for both **automatic detection** and **manually specified tempo**. Known limitation: beat detection may land on an octave multiple/submultiple for non-4/4 material (3/4, 6/8, half-time) and sparse percussion; after the removal of folding, such misreads show up directly in the report. When in doubt, enter the tempo manually.

## Highlights

- **Smallest tempo change**: the music is only moved to a position that is a simple multiple of the target tempo (n times or 1/n times, n a whole number), choosing the least change while keeping the beat natural.
- **Adjustable speed-up / slow-down limits**: set how far it may slow down and how far it may speed up; anything beyond the limits is left alone rather than stretched until it sounds broken.
- **Accurate detection, metronome that never drifts**: detection uses autocorrelation refined on a comb grid, so steady-tempo tracks are pinned down to roughly 0.1 BPM. The metronome does not stack fixed intervals; it is placed directly on the music's own beat time line, so it never drifts or misses a beat.
- **Six percussion sounds, adjustable relative level**: audition and pick a sound; its level follows the track's own loudness and the relative amount is adjustable.
- **Batch processing**: process a whole library in one go.
- **Several output formats**: WAV / MP3 / FLAC.
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

> The installer offers the Windows graphical interface only, and it has **no command line** -- every "command line" entry in these tables means running from source.
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

**The folder holding `BeatSyncSetup.exe` must not contain Chinese characters** -- only plain ASCII letters, digits and symbols.
Double-clicking it from a folder whose path contains Chinese characters does nothing at all (it exits silently); move it to a plain ASCII path.

**What the installer gives you**: two graphical interfaces (desktop version + compatibility interface); every conversion feature is available in the interface, with no Python, no network and no command line needed.

### Running from source (developers)

Requires **Python 3.11+**:

```bash
pip install -r requirements.txt
python tempo_converter_app.py
```

On Windows you can also just double-click `BeatSync.bat` (a launcher that lives in the **source repository**, shipped with the source -- source only; the installer does not include it).

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

**Once the desktop version will not open, no button inside the interface can be clicked** -- so start the compatibility interface **from the Start menu**:

1. Click the Start menu (or press `Win`) and pick **BeatSync (Compatibility)** from the app list.
2. **Double-click `BeatSync-Compatibility.bat` in the install folder** (simplest, recommended).

When the desktop version does open, there is also a "switch to compatibility interface" entry at the bottom of the main window.

**When running from source** (no installer), run the tkinter version directly:

```bash
python tempo_converter_gui.py        # run the tkinter version directly
python tempo_converter_app.py --tk   # make the main program use tkinter
```

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
python tempo_converter.py song.mp3 -t 180 --segments
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
| `-o, --out-dir` | output directory (default: `tempo_converted/` beside each source file) |
| `--no-mirror-dirs` | with an output directory, do not mirror the tree -- write everything flat |
| `--recursive` | search subfolders recursively |
| `--report` | CSV report path (default `conversion_report.csv` in the current directory) |
| `--max-slowdown` | maximum slow-down percentage, default 20 |
| `--max-speedup` | maximum speed-up percentage, default 50 |
| `--segments` | per-section speed change: detect tempo changes inside the track and stretch each section with its own minimum-stretch plan (seams cross-faded); ignored with `--bpm` |

---

## Per-section speed change

By default the whole track is matched to a single detected BPM. If a song changes tempo halfway (say 100 BPM in the verse, 140 BPM in the chorus), one global rate leaves the metronome drifting off the beat through the changed part. With `--segments` (or the "Per-section" switch in either interface):

1. the tempo curve is split at detected change points by least-squares segmentation; a steady track yields one section and falls back to the normal single-plan path;
2. each section is measured and stretched on its own, using the original minimum-stretch rule;
3. each seam searches a low-energy cut on **both** sides and applies a 5 ms equal-power cross-fade, so the join stays below the music's own transient level (a "seam score > 1" note in the report means "worth an ear check");
4. the metronome follows each section's own beat grid, so clicks stay on the beat across seams;
5. if **any** section has no feasible plan within the current limits, the **whole track is left unchanged** (no file is produced; the remark starts with "Skipped:"). We never stretch only the feasible sections — that would leave the two sides of a seam at different speeds.

The report is still one row: the detected BPM / stretched BPM / speed change of the sections are joined with ` / `, and per-section details go into the remarks; the UI also lists each section's speed side by side (e.g. `100→90｜154→180`). **Without the switch, behaviour is byte-for-byte the same as before.**

## Output

**Naming**: `original_<target>bpm.ext` (for example `song_180bpm.wav`); on a name clash `_1`, `_2`, `_3` are appended.

**Location**: by default into `tempo_converted/` beside each source file;
with `-o` the input tree is mirrored into that directory.

**Report**: CSV, written to `conversion_report.csv` in the current directory by default.

---

## Project layout

**This is the source repository layout** (what you see when running from source or developing on it).
The installer does not unpack these `.py` files -- it has a single `BeatSync.exe`, an `_internal\` folder,
plus `README.md`, `README.en.md`, `LICENSE` and `assets\`.

```
tempo_converter.py        core: detection / candidate choice / stretching / metronome / batch / report; CLI entry
tempo_converter_app.py    desktop shell: pywebview window + JS bridge + drag and drop + smoke test
tempo_converter_gui.py    tkinter fallback interface
frontend/                 index.html + app.css + app.js (no external dependencies)
BeatSync.bat              double-click launcher on Windows (source only; not in the installer)
assets/                   icon source icon.svg (shared by the interface and the installer)
requirements.txt          Python dependencies
LICENSE                   MIT license
```

Dependencies: librosa, soundfile, numpy, scipy, pywebview (**only needed when running from source**; the installer bundles them).

---

## Interface language

The graphical interface **defaults to Chinese**; the "中 / EN" button in the top-right corner (just left of the day/night mode button) switches between Chinese and English in one click.

- **What switches**: every piece of text in the interface -- the file list, parameter labels, tempo-limit hints, sound names (Beep / Woodblock / Cowbell / Click / Sidestick / Clap), buttons, progress and result messages, dialog texts.
- **The button's own tooltip**: in Chinese the button reads "中 / EN" and its tooltip points at "switch to English"; in English it is the other way round.
- After switching in the desktop version, the **log, result notes, click relationships and the CSV report** are all produced by the backend in the same language, so no Chinese leaks into the English interface.
- **The tkinter fallback interface** has the same "中 / EN" button in the top-right corner: switching rebuilds the interface while the added files and all settings are kept as they are. Its conversion parameters match the desktop version: a "keep the original folder structure" checkbox (greyed out when no output directory is set), two tempo-limit boxes (0-100, defaults 20 / 50), and a metronome level that can be typed as a dB value in addition to the slider, all synchronised live.
- **The command line** (source runs only) defaults to Chinese; add `--lang en` (or set the environment variable `BEATSYNC_LANG=en`) to make the console output and the CSV report English. `--lang` only changes the display language and does not affect any algorithm behaviour.

---

## License

This project is released under the **MIT license**; the full text is in [LICENSE](LICENSE).

In short: you may freely use, modify and distribute this software (including commercially), as long as you keep the copyright notice and the license text in copies or substantial portions; the software is provided "as is", without warranty of any kind.

Third-party dependencies (librosa, soundfile, numpy, scipy, pywebview and others) follow their own licenses; see the projects listed in `requirements.txt`.

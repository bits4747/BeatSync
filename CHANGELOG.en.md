# Changelog

English | [中文](CHANGELOG.md)

This project follows [Semantic Versioning](https://semver.org/).
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Versioning rules**: current version **1.2.5**. Every release bumps the version
and adds a section below:

- `1.0.x` — bug fixes only; no behaviour or output changes
- `1.x.0` — new features, or changed defaults / output (old → new behaviour is
  noted in the entry)
- `2.0.0` — breaking changes (file naming, CLI arguments, report columns, etc.)

The same version number must stay consistent in three places: this file,
`version` in `pyproject.toml`, and `MyAppVersion` in the installer script
`_packaging/BeatSync.iss`.

---

## [1.2.5] — 2026-10-25

Bug fixes only: the two root causes of metronome/music misalignment (cumulative
drift of constant-tempo grids, and the family collapse discarding measured layers).

### Fixed

- **Beat-grid rebuilds now re-lock beat by beat, removing the cumulative drift
  of constant-tempo grids.** After the phase flip (1.2.4), the dotted-level fix
  (1.2.0) and the family collapse (1.2.2), the beat grid was rebuilt as a
  **constant-tempo grid** (`phase + k × period`). Real songs have ±1%-level
  tempo fluctuation (live recordings especially), and the detected layer value
  itself carries 0.1–0.5% error — the deviation between a constant grid and the
  music's true beats is **cumulative** (measured: a 0.4% mismatch drifts about
  one second over 4 minutes). It sounds like "the metronome drifts more and
  more out of sync". **New behaviour**: all three rebuild points re-lock beats
  by tracking — the next beat takes the strongest onset frame within ±1/4 beat
  of "last locked beat + period" (phase-locked-loop style: deviation is
  absorbed per beat, never accumulates; off-beat positions lie outside the
  window so eighth-note bass cannot be locked), then a robust polynomial fit
  smooths the result. Clicks now follow the music's own beats. Beat count is
  identical to the constant skeleton, so metronome density still equals the
  target BPM exactly.
- **Family collapse now protects the dominant measured layer, fixing 3/2
  misfrequencies.** When every segment measures the same tempo (e.g. 110) but
  a half layer (55) has a smaller stretch cost, the collapse picked a layer no
  segment ever measured — that cost is a lie: nominally +9%, it actually
  stretches 110→120, a 3/2 relation against the 180 metronome, misaligned for
  the whole track. **New behaviour**: if the duration-weighted dominant
  measured layer's cost is within (0.02, 0.35] octaves of the best candidate,
  the measured layer wins. Regression checks: "Full Moon"'s dominant measured
  124 vs 61.57 differs by 0.49 > 0.35, so 61.57 is still chosen (no
  regression); "She Xiang"'s two layers tie (0.0008 < 0.02), so the lower
  83.02 is still chosen (no regression).
- **Measured fix cases** (the deca joins batch reported as "still misaligned"
  on 2026-10-25): Yan Jing Li 54.97→**109.93**, Ye Jian Du Bai 54.99→**109.98**,
  Go Slow 59.91→**110.02** (all three repaired from wrong-layer 3/2
  misfrequency to the measured layer, 2 clicks per beat on the beat); Wo Bu
  Yong Wen / Fa Shan Ke Chen / Somewhere now have clicks that follow the music
  instead of slowly drifting. YYZ still 2 segments (102.8 / 140.7), She Xiang
  83.02 one segment, Hong Chen Ke Zhan 89.94 one segment, Full Moon 61.57 one
  segment.

### Known limitations

- **Songs whose segments all fall to the same wrong layer may still pick the
  wrong layer**: if per-segment detection consistently counts every segment at
  a half layer (measured: "Xia Ye Wan Feng" — every segment measures 55.01,
  true tempo ≈110), the family collapse picks within that wrong family; the
  measured-layer protection has no true value to protect. Whole-track
  detection often gets these right (that is how Yan Jing Li was fixed), but
  the two cases cannot be told apart automatically; setting the source BPM
  manually is recommended.

---

## [1.2.4] — 2026-10-25

Bug fixes only.

### Fixed

- **The beat grid sat half a beat off when eighth-note bass drowned the kick**:
  when a track's eighth notes are played by a bass guitar (low-frequency, same
  band as the kick) at a level close to the beats, the full-band onset flux of
  the comb fit locks the grid onto the eighth positions — tempo, stretch ratio
  and reported numbers are all correct, but the metronome is **phase-inverted
  by half a beat** and sounds misaligned throughout. Measured on 「乏善可陳」
  (true 96): the kick band (<120 Hz) carries 5.1% more energy on the anti-phase
  than on the detected grid. **New behaviour**: after refinement a "kick-band
  phase verdict" runs — the onset strength of the pure kick band (<120 Hz) is
  measured at the grid phase and at the anti-phase (±half a beat); if the
  anti-phase exceeds the grid phase by 2%, the grid shifts by half a beat.
  Calibrated on six known-tempo tracks: the two needing the flip (乏善可陳
  1.051, 失败的晚餐 1.365 — detected as 90 = half of 180, so the anti-phase is
  the true beat) and the four correct ones (0.689–0.935) are cleanly
  separated; after the flip 乏善可陳's kick ratio turns 0.952 (grid phase
  dominant).

---

## [1.2.3] — 2026-10-25

No detection or conversion results change; code clean-up and speed-ups only.

### Performance

- **Segmented path is ~60% faster**: the full-track mel spectrogram used to be
  computed 2–3 times (once for the frame-wise tempo curve, once for per-segment
  detection, once more when `auto` fell back to whole-track detection). It is
  now computed once in `process_file` and threaded through the `mel=` parameter
  into `_segment_plans` → `detect_tempo_segments` → per-segment detection.
  Measured on 4-minute tracks: detection + plan 14.0 s → **5.9 s** (−58%) and
  11.7 s → **4.2 s** (−64%). Detection and conversion results are bit-identical.

### Clean-up

- `_collapse_same_family` rewritten as a single path: family coverage (≥98%) →
  octave-lattice candidates → per-long-segment hit-rate gate → grid rebuilt at
  the chosen layer. The legacy shape-heuristic branch (used only by old unit
  tests) and the unused `_family_layer` were removed; unit tests now drive the
  hit-rate gate directly with synthetic onsets.
- `_comb_refine` and `_comb_fit_at` intentionally remain two implementations,
  with the reason documented in the code: their coarse-phase sampling differs,
  and merging them would shift sample positions and drift the refined results.

---

## [1.2.2] — 2026-10-25

Bug fixes only. Acceptance criteria (set 2026-10-25): ① report the real BPM;
② constant-tempo tracks must not be split into multiple segments; ③ real
multi-tempo tracks must be detected and segmented.

### Fixed

- **Constant-tempo tracks jumped in speed between segments**: when the long
  segments of one track landed on different layers of the same integer family
  (「满月」*Man Yue*: intro detected 95.3, body detected 122.8 — both wrong
  layers of a 61 BPM track), each segment picked its own clicks-per-beat, so
  stretch ratios of −5.5% and +46.6% coexisted and the music jumped mid-song.
  **Old behaviour**: collapse only fired for back-and-forth jitter or
  all-flat long segments. **New behaviour**: when one family base covers ≥98%
  of the long-segment duration, the layer is chosen on the **octave lattice
  (×2/÷2 of the measured speeds)** by minimal stretch cost — for 「满月」 the
  true layer 61.57 was never measured by any segment, but it is half of the
  measured layer and has the smallest cost; the track collapses to a single
  segment at −2.5% with 3 clicks per beat. Safety is guarded by two gates:
  ① family coverage of 98% (「YYZ」's 102.8 intro vs 140.4 body differ by
  1.366×, beyond SNAP_MAX, so no base covers them → segments kept), and
  ② the candidate layer must pass the hit-rate gate on every long segment
  (any octave layer of the real tempo change 90|120|90 fails on some segment
  → segments kept). Measured: 「满月」 61.57 (true 61) as one segment,
  「奢香夫人」 83.02 (true 83) as one segment, 「我不用问」 94.90 as one
  segment, YYZ still 2 segments (boundaries match the true ones); 50 unit
  tests, the core regression suite and the three real-track criteria all pass.

---

## [1.2.1] — 2026-10-25

Bug fixes only.

### Fixed

- **Constant-tempo tracks accelerated in the middle**: when the long segments
  (≥10 s) agreed with each other but a short segment in between produced a
  wild estimate, the old collapse thresholds (requiring ≥3 long segments and
  at least one rise plus one fall) could not fire, and the wrong layer of that
  short segment was stretched on its own. Measured on 「我不用問」(*Wo Bu Yong
  Wen*, whole track ~95 BPM): a 9.7-second middle section was counted at
  126.2 (= 95×4/3, the dotted layer) and stretched **+42.6%** — it sounded
  like "the middle suddenly speeds up for no reason". **New behaviour**: when
  all long segments sit on the same step, the track has no real tempo change
  and the short segment's wild value is absorbed as layer jitter — family and
  layer are decided from the long segments only (short segments are unreliable
  and do not vote), and the collapse still has to pass the hit-rate gate over
  the whole span. After the fix the track is one segment at 94.9 BPM, −5.2%,
  and the mid-song acceleration is gone; two-step shapes like YYZ
  (102.8|140.4) are unaffected.

---

## [1.2.0] — 2026-10-24

Changed the tempo-detection output (old → new behaviour below).

### Fixed

- **Detection no longer gets dragged to a dotted layer by the 120 BPM prior**:
  the log-normal prior at 120 BPM inside `beat_track` flips the layer when the
  true layer's peak is only slightly stronger than a fake peak near 120, which
  puts the real beats on the 1/3 and 2/3 positions of the detected grid (a 3/4
  or 3/2 "dotted relation") — about half of the metronome clicks then land
  between beats, and the reported BPM is off by 1.5× or 4/3×. **Old
  behaviour**: 「失败的晚餐」(*Shi Bai De Wan Can*, true 180) was detected as
  119.99 and 「乏善可陳」(*Fa Shan Ke Chen*, true 96) as 128.01. **New
  behaviour**: after refinement a "dotted-layer correction" runs — if the
  thirds positions of the detected grid carry beat-level onset energy
  (salience ≥ 0.85; normal layers measure ≤ 0.77), the detection is moved to
  the ×3/4 or ×3/2 neighbour (whichever is closer to the 120 BPM perceptual
  anchor) and the beat grid is rebuilt, at most two chained steps; if the
  neighbour is still salient the original layer is kept. Across a 343-track
  library 56 tracks triggered the correction; 「乏善可陳」 went 128.01 →
  **96.00**, and the regression spot-checks on known-tempo tracks were all
  unchanged (奢香夫人 83, 红尘客栈 90, YYZ 140.4, 很久以後 146, 一路逆風 125);
  42+4 unit tests, the core regression suite and the three real-track criteria
  (constant tracks 1 segment / YYZ 2 segments / click density = target) all
  pass.

### Known limitations

- **The dotted direction falls back to the 120 perceptual anchor**: data
  criteria (autocorrelation, grid score) are fooled by strong eighth-note
  grids when comparing the ×3/4 and ×3/2 neighbours (for 「乏善可陳」 the true
  96 and the eighth layer 192 score nearly the same, sometimes inverted), so
  the direction is whichever neighbour is closer to 120 BPM — the comfortable
  human tapping rate, same source as `start_bpm`. Tracks whose truth lies in
  the ×3/2 direction land on the ×3/4 side — e.g. 「失败的晚餐」 (true 180) now
  reports 90 and 草東《大風吹》(*Da Feng Chui*, ~176) reports 88: still a clean
  integer ratio (2×), the metronome still lands on every beat via the
  clicks-per-beat compensation, but the reported number differs from a hand
  tap by a factor of two.
- **Compound-meter tracks may be corrected by mistake**: a true 6/8 track with
  strong third-position subdivisions also triggers the correction (no instance
  found in the 343-track library); the boundary is a thirds salience of 0.85.
- **Marginal salience is left alone**: layers whose thirds salience sits
  between 0.77 and 0.99 keep their original verdict (e.g. 「錯過不錯」 at
  126.42, truth unknown, measured 0.82).
- **「在這裡停一下」(*Zai Zhe Li Ting Yi Xia*, true 86) still reports 152**: the
  true layer and the wrong layer differ by ≈7/4 — neither a power of two nor a
  3/4 relation, so the dotted correction cannot reach it. Fill the BPM by hand
  for such tracks.

---

## [1.1.1] — 2026-10-24

Bug fixes only; no default behaviour or output changes.

### Fixed

- **The metronome turned into a "machine gun"**: when 1.1.0 merged same-family
  segments into one, it recorded the chosen family layer as the segment's
  tempo (e.g. 93.0) but kept the pre-merge beat positions of the individual
  segments (spacings corresponding to 134.9). With the two inconsistent,
  `click_schedule` clicked at the wrong spacing — measured 261 clicks/minute
  against a target of 180. **The beat grid is now rebuilt at the chosen layer**
  (spacing locked to 60/layer-tempo), and the grid hit-rate must confirm that
  this layer really explains the music; otherwise no merge happens.
- **Real tempo changes were merged by mistake**: 1.1.0 distinguished layer
  jitter from real tempo changes by whether the per-segment tempos zig-zag,
  but unreliable short segments polluted that direction test. YYZ ends with an
  8-second quiet outro measured at 119.6, which turned the one-way change
  103 → 141 into "up once, down once", so the whole track was misjudged as
  jitter and the intro's tempo change was erased (output as one segment at
  140.86 with the intro off-beat). **Direction is now judged only from
  segments no shorter than 10 s and at least 6% of the track**, and adjacent
  segments within 10% of each other count as the same step.

---

## [1.1.0] — 2026-10-24

Changed the default behaviour and output of segmented conversion (old → new
behaviour below).

### Fixed

- **Constant-tempo tracks are no longer split into many segments**: frame-wise
  tempo estimation counts the same music at different metrical layers
  (e.g. alternating 90 and 120); the change-point detector sees such a curve
  and cuts 7 segments whose target speeds alternate 180/90 while the stretch
  ratios oscillate between 1.50 and 1.00 — the converted music lurches. **Old
  behaviour**: each segment was stretched and spliced separately. **New
  behaviour**: as long as all segment tempos fall into the same "integer
  family" (up to 16×, log2 tolerance 0.43 ≈ 1.35×, absorbing layer mistakes
  like 4/3) and the tempo is not moving in one direction, they are judged to
  be layer jitter of one piece of music and merged into one segment. Within
  the family the layer is chosen by minimal stretch cost (ties prefer fewer
  clicks per beat, i.e. denser clicks). Measured: 5 of 7 real tracks collapsed
  from multiple segments to 1; real tempo changes (one-directional, e.g.
  90→120→150) are still segmented.
- **Detected tempos converge into the integer family**: the reported tempo is
  no longer a fractional multiple of the real one (2/3, 3/2 — values that
  cannot be compensated and genuinely break the metronome), but the
  lowest-cost layer within the family. Measured: 「在這裡停一下」 (true 86)
  reports 87.0 and 「失败的晚餐」 (true 180) reports 90.0 — both integer
  fractions of the real tempo, so the metronome still lands on the beats.

### Known limitations

- **Ambiguous passages may still be off the integer ratio**: e.g. YYZ's intro
  measures 102.8 BPM (manually labelled 114), a ratio of 1.109 — not an
  integer ratio. The passage has two nearly tied grid candidates (103 and 113
  score 1.82 vs 1.78); this is the precision limit of detection, not something
  family logic can fix. Use the manual "source BPM" field for such passages.

---

## [1.0.1] — 2026-10-23

Bug fixes only; no default behaviour or output changes.

### Fixed

- **Multi-tempo tracks no longer produce a pile of redundant segments**:
  merging adjacent segments used to require their stretch ratios to match to
  1e-9, which never happens, so reports kept showing adjacent segments with
  identical target speeds and clicks-per-beat differing by fractions of a
  percent (an extra splice with no audible difference). Merging now uses the
  relative tempo difference (within 6%) and output-plan equivalence, plus an
  extra gate: after merging, the combined segment is re-detected and only kept
  if its beat-grid alignment is no worse than the better of the two originals
  — real tempo changes align clearly worse and are unaffected.
- **Change-point segmentation could collapse a whole track into one segment
  when many short segments existed**: `detect_tempo_segments` picked the wrong
  boundary to remove for "too short" segments (the last short segment merged
  into the first two), restarting the cascade every round — the same track
  gave non-monotonic results like "1 segment at min_seg_s=4, 10 segments at
  min_seg_s=8". Short segments now always merge into the side with the closer
  tempo, and only the shared boundary is removed.

### Performance

- **Overall segmented-conversion time down ~15%**: change-point detection and
  per-segment detection used to recompute the STFT/mel for the same audio;
  the full-track spectrogram is now computed once and reused by frame slices
  (measured: identical detection results). End-to-end on 7 real tracks:
  76.1 s → 64.2 s.

---

## [1.0.0] — 2026-10-22

First public release: CLI, WebView desktop UI and a tkinter fallback UI,
Chinese/English interface, a Windows installer, MIT licence.

### Core

- **Minimal stretch**: the metronome always clicks at the target tempo; the
  music only moves to the nearest n-times or 1/n-times position relative to
  the target, choosing the smallest change that keeps the clicks on the beats.
  For a target of 180 BPM: a 60 BPM source is untouched (3 clicks per beat),
  85 → 90 (+5.9%, 2 clicks per beat), 120 → 180 (+50%, 1 click per beat).
- **Detected tempo is reported as measured, no octave folding**: `detect_tempo`
  reports the detector's raw result — 60 BPM is reported as 60 BPM (an older
  build repeatedly multiplied/divided by 2 to fold results into [70, 180),
  dragging slow tracks up; that behaviour is retired; `normalize_tempo()` is
  kept for compatibility and returns its input unchanged).
- **The metronome does not drift**: clicks are placed on the music's own beat
  timeline (comb-grid refinement + phase search + robust polynomial
  smoothing), not at fixed intervals. Measured phase error on an even click
  track: 0.0000 s.
- **Stretch limits**: by default at most 20% slower and 50% faster, freely
  adjustable; files exceeding both are skipped. The boundary check keeps a
  0.5% margin to absorb detection jitter (otherwise 120→180 sitting exactly at
  +50% would be rejected when measured as 119.99).

### Segmented conversion (on by default)

- Tempo-change points inside a track are detected, the track is cut into
  tempo sections, each processed independently by the same "minimal stretch"
  rule; splice points are searched on both sides for low-energy positions and
  joined with a 5 ms equal-power crossfade. The metronome follows each
  section's own beat grid without crossing beats at splices.
- **Alignment-quality gate**: each section is scored by "onset strength at
  grid points ÷ mean onset strength"; sections below 1.10 are merged into a
  more trustworthy neighbour and re-detected instead of being forced.
- **Segmentation threshold**: adjacent sections differing by <15% in tempo are
  not split, avoiding seams for a few percent; when a constant track yields a
  single section the whole-track path takes over, matching the single-tempo
  behaviour.
- **Infeasible sections merge into neighbours (`auto`)**: a section with no
  plan inside the limits merges into the nearest-tempo neighbour and is
  re-detected; if the merged section is feasible the file is still produced.
- **`on` (forced segmentation) keeps strict semantics**: any section outside
  the limits → the whole track is skipped with no output file (never "convert
  only the feasible ones", which would desync the splices).
- Switches: CLI `--segments auto|on|off` (bare flag equals `on`, default
  `auto`), the "auto segmented conversion" checkboxes in both UIs, frontend
  `#segments`. Manual `--bpm` disables segmentation.
- The report stays one line per file: detected BPM / stretched BPM / stretch
  ratio / clicks per beat joined with ` / ` per section, per-section details
  in the remark, detection mode labelled "auto · segmented"; the frontend file
  rows show per-section speeds side by side (e.g. `100→90｜154→180`), in both
  languages.

### Audio and output

- WAV / MP3 / FLAC / OGG / AIFF decode directly. **M4A / AAC / WMA are not
  supported**: libsndfile does not read those containers, librosa 1.0 has no
  audioread fallback, and installing ffmpeg does not help; adding them shows a
  clear message instead of silently skipping.
- Output `source` (same format as the input) / WAV / MP3 / FLAC / OGG. OGG has
  a per-call sample-count limit; over-long audio automatically falls back to
  WAV with a note in the remark.
- **MP3 output follows the input bitrate by default** (320 when unreadable),
  instead of being silently downgraded (libsndfile defaults to ~142 kbps
  variable when no bitrate is given — a 320 kbps source would shrink to 40%);
  adds `--mp3-bitrate` (`auto`/320/256/224/192/160/128/112/96) and a UI
  bitrate selector; the bitrate ↔ libsndfile `compression_level` mapping was
  calibrated per step.
- Six metronome timbres (beep / woodblock / cowbell / click / sidestick /
  clap), volume auto-matched to the track's loudness and adjustable
  (default −6 dB).
- Batch processing with recursive search and mirrored output hierarchy
  (optional); CSV report with 15 columns, UTF-8-SIG encoded.

### Interface

- Desktop app (pywebview + native HTML/CSS/JS, no build step) and a tkinter
  fallback with one-to-one parameter mapping; if the desktop window cannot
  open, the Start-menu shortcut or `BeatSync-Compatibility.bat` opens the
  fallback directly.
- Chinese/English: a "中 / EN" switch in the top-right corner; UI text, logs,
  remarks and the CSV report all follow the selected language.
- Persistent settings (language / theme / parameters); no crashes in private
  browsing mode.

### Performance

- The same track (4 m 27 s, 48 kHz stereo, target 180 BPM): CLI end-to-end
  **30.6 s → 17.9 s**, in-process processing **26.6 s → 16.9 s**; the output
  wav's SHA256 is identical before and after. Four sources:
  - `_grid_score`'s "max onset strength within the window" now uses one
    sliding-maximum pass over the whole envelope (`_dilate_max`,
    `scipy.ndimage.maximum_filter1d`): a single `_comb_refine` drops
    **0.85 s → 0.035 s** (~24×), the largest previous hotspot.
  - `detect_tempo` computes the hop=256 mel spectrogram once and derives the
    hop=512 onset envelope from its even frames (bit-identical): ~**2.1×**
    faster detection.
  - `_segment_plans` reuses previous slice results when re-detecting after a
    merge (3 of 9 detections per track used to be duplicates).
  - Whole-track detection is deferred until segmentation declines to take
    over, so the segmented path no longer wastes a full-track pass.

### Engineering

- `tests/test_tempo.py`: pytest unit tests, all on in-memory click tracks —
  they run in seconds and need no test audio.
- `pyproject.toml`: `pip install -e .` with a `beatsync` CLI entry point;
  optional `gui` / `dev` dependency groups.
- `.github/workflows/ci.yml`: unit tests on Windows / macOS / Linux × Python
  3.11 / 3.12 / 3.13.
- `CHANGELOG.md`, `CONTRIBUTING.md`, `.gitattributes` (line-ending
  normalisation).
- Windows installer: single-file setup into the user directory without admin
  rights, bilingual wizard, desktop / Start-menu shortcuts, full-tree cleanup
  on uninstall.

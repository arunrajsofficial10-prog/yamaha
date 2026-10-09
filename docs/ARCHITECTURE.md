# Architecture: MP3 → MIDI → Keyboard Style Generator

Status:
- **Phases 1–3 are built:** upload, BPM/beats/meter, key, corrections.
- **Also built:** drum-groove extraction (the drum part of Phase 6) and SFF1 keyboard-style
  export with **rhythm parts only** (Phase 11). This covers every Yamaha series that can load
  styles: Genos, PSR-SX, PSR-S, PSR-E/EW and PSR-I.
- **Not built:** chords, bass and the chord-relative pitched parts (Phases 4 and 9), so exported
  styles currently contain drums only.

Section 10 covers the drum and export design.

## 1. Stack and why

| Concern | Choice | Reason |
|---|---|---|
| Analysis engine | Python 3.13 + **librosa 1.0**, numpy, scipy | Mature, well-tested music DSP (onset strength, DP beat tracker, CQT chroma, HPSS). PHP (XAMPP) has no comparable audio library; analysis in PHP would mean shelling out to Python anyway. |
| Audio decoding | **soundfile** (bundles libsndfile 1.2.2) | Decodes MP3, WAV, FLAC and OGG natively, so no ffmpeg dependency. |
| Tags | mutagen | ID3/Vorbis title, artist and album. |
| API | FastAPI + uvicorn | Async uploads with streaming size limit, background jobs, typed JSON. |
| Frontend | Plain HTML/CSS + ES modules, no build step | Served by the same process. Web Audio is used for playback and the metronome, canvas for the waveform, SVG for the charts. |
| MIDI (Phase 5+) | mido | Already installed; writes SMF type-1 with tempo, time-signature and key-signature meta events. |

The folder lives under `xampp/htdocs`, but Apache is **not** used. The app runs on
`http://127.0.0.1:8765` through `start.bat`. If it must sit behind Apache later, reverse-proxy
`/` to port 8765 with `mod_proxy`.

## 2. Layout

```
backend/
  app/
    main.py          HTTP API + static frontend mount
    config.py        limits, sample rate, thresholds, PPQ
    errors.py        user-facing error types (code + safe message)
    jobs.py          single-worker background jobs with progress
    projects.py      file-based project store, corrections, effective values, export
    theory.py        pitch classes, MIDI formula, chord-relative transforms, ranges   (§7, §9, §10, §23)
    grid.py          BPM formula, subdivision, quantisation, ticks                    (§4, §5, §6, §15, §16)
    audio/
      loader.py      format sniffing, validation, metadata, decode → mono 22.05 kHz
      tempo.py       beats, BPM, half/double resolution, meter, downbeats, confidence
      key.py         tuning, harmonic chroma, template matching, windowed keys
      pipeline.py    stage registry (implemented + planned), warnings, re-tracking
  tests/             synthetic ground truth + real recordings as MP3
frontend/            index.html, css/app.css, js/{app,api,player,waveform,charts}.js
data/projects/<id>/  project.json, audio.<ext>, analysis.json
docs/ARCHITECTURE.md
start.bat
```

## 3. Data model

Each project is a directory. Three rules:

1. **Detected values are immutable.** `analysis.json` is exactly what the detectors produced.
2. **Corrections are separate.** `project.json → corrections` holds the user overrides: `bpm`,
   `tonic`, `mode`, `timeSignature`, `downbeatPhase`, `beatOffsetMs`. It also holds `_beats`,
   the beat grid re-tracked when the BPM is corrected.
3. **Downstream phases read `projects.effective()` only.** It merges the two into one view
   (BPM, labelled beats with bar and beat numbers, downbeats, key, key signature, the list of
   corrected fields). This is how corrections reach MIDI and style generation (spec §25).

`export.lakstyle` (spec §28) is the effective view plus the raw analysis, in JSON. `sections`,
`chords`, `tracks` and `style` are present but empty until their phases exist.

## 4. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/projects` | Multipart upload. The extension and magic bytes are checked, size is capped at 150 MB, and duration is checked against the 8 s–20 min range. |
| GET | `/api/projects`, `/api/projects/{id}` | List, or get project + analysis + effective + active job. |
| POST | `/api/projects/{id}/analyze` | Starts a background job (202). Returns the existing job if one is already running. |
| GET | `/api/jobs/{id}` | Status, stage, progress and message, or a user-safe error. |
| PATCH | `/api/projects/{id}/corrections` | Partial update. `null` clears a correction. A BPM change re-tracks the beats. |
| GET | `/api/projects/{id}/audio` | Original file, for playback. |
| GET | `/api/projects/{id}/analysis.json`, `/export.lakstyle` | Downloads. |
| DELETE | `/api/projects/{id}` | Removes the project and its audio. |

Errors are always `{"error": {"code", "message"}}`, and the message is written for end users
(spec §29). Unexpected exceptions are logged server-side and returned as a generic message.

## 5. Algorithms (Phases 2–3)

**Beats and BPM.** Median-aggregated spectral-flux onset envelope → dynamic-programming beat
tracker. BPM = 60 / mean inter-beat interval (spec §4), after dropping intervals more than
±25% from the median.

**Half/double time.** Autocorrelation always favours slower tempos, so the decision uses
rhythmic structure instead:

- If the **midpoints** between beats carry as much kick + snare energy (<150 Hz and
  150–2000 Hz onsets) as the beats, the tempo is doubled.
- If **every other beat** is nearly empty of drums, the tempo is halved.

The beats are then re-tracked, locked to the new tempo. Thresholds are calibrated on synthetic
grooves and real recordings. The UI always shows the ×2 and ÷2 candidates as one-click fixes.

**Meter and downbeats.** For each beat, three accent values are computed: low-band onset
(kick), harmonic change (1 − cosine similarity of beat-synchronous chroma) and full-band onset.

- Every (meter ∈ {3, 4}, phase) pair is scored by on-phase minus off-phase accent, plus chroma
  self-similarity at bar-length lags.
- 3/4 must win by a margin, because 4/4 dominates popular music.
- Other meters (2/4, 5/4, 6/8, 7/8, 9/8, 12/8) are user-selectable. Compound meters are
  counted in dotted-quarter pulses.

**Key.**

1. Tuning estimate.
2. HPSS: keep the harmonic part.
3. CQT chroma, weighted by RMS so quiet passages count less.
4. √-compress the profile.
5. Pearson-correlate it with the 24 rotated **Albrecht–Shanahan** major/minor profiles.
   Pearson correlation is the spec's Σ profile·template applied to normalised vectors.

Albrecht–Shanahan beat Krumhansl–Kessler in our comparison: 7/7 against 3/7, because KK called
A minor "A major" when the minor third was quiet. Running the same scoring on 20 s windows gives
key stability and modulation warnings.

**Confidence (spec §24).** Each detector reports 0–1. These are heuristics, not
probabilities:

- **BPM:** beat regularity × half/double-time decisiveness × beat coverage.
- **Key:** best correlation, plus its margin over the runner-up.
- **Meter:** the score gap between 3 and 4.

Anything below 0.6 shows a review warning.

### Measured accuracy (from the test suite)

| Input | Result |
|---|---|
| Synthetic 4/4 at 84, 96, 120 and 140 BPM | BPM within 2%, 4/4 ✓ (140 needed the half/double fix) |
| Synthetic 3/4 waltz, 150 BPM | 3/4 ✓ |
| Synthetic C, E♭, G major; A, E minor | all ✓ |
| Downbeats with a pickup | median error < 70 ms ✓ |
| Real: *Sugar Plum Fairy* (E minor), Brahms Hungarian Dance 5, orchestral (G minor) | both ✓, decoded from MP3 |
| Real: *Sweet Waltz* | 3/4 ✓ |
| Real: 4 more tracks without published ground truth | steady grids, BPM consistent with beats |

## 6. Known limitations

- **Downbeat detection is heuristic.** madmom's neural downbeat tracker would be better, but it
  has no Python 3.13 wheels. Upgrade path: run it in a separate 3.10 environment as a stage
  plugin.
- **Highly syncopated, rubato or beatless material** produces low confidence. The UI asks for
  manual BPM in these cases (handled, not hidden).
- **Re-tracking after a BPM correction** decodes the audio again (~1–3 s per request).
- **Jobs live in memory.** A server restart loses running jobs; the project is left in
  `analysisStatus: "running"` and can simply be re-analysed.

## 7. Roadmap: how later phases plug in

Every stage is registered in `audio/pipeline.STAGES`. A stage reads and writes the shared
context, and its output is stored under its own key in `analysis.json`.

| Phase | Stage | Planned engine | Inputs from earlier phases |
|---|---|---|---|
| 4 | Chords | Beat-synchronous chroma + chord templates (maj, min, 7, maj7, m7, sus2, sus4, dim, aug) with Viterbi smoothing, biased by the key. Optional upgrade: a CRNN chord model. | effective beats, key |
| 5 | MIDI | mido. Ticks = beats × PPQ on the tracked grid (§15, §16); velocity = 30 + amp × 90 (§17); tempo, time-signature and key-signature metas. | effective grid, key |
| 6 | Stems + transcription | **Demucs** (drums / bass / other / vocals; PyTorch, ~1–3 min per song on CPU) → onset classification for drums (GM 36/38/42/46/49/45/47/50) and **basic-pitch** for bass and chord notes, each with per-event confidence. | audio |
| 7 | MIDI editor | Canvas piano roll in the frontend, using an undo/redo command stack. | MIDI tracks |
| 8 | Sections | Self-similarity of beat-synchronous chroma + MFCC + RMS → novelty boundaries snapped to downbeats → cluster labels → INTRO / MAIN A–D / FILL / BREAK / ENDING. User-editable. | grid, chords |
| 9 | Chord-relative engine | `theory.to_chord_relative` / `from_chord_relative` / `clamp_to_range` (already implemented and tested). Each source note becomes (interval from the chord root, octave), and is replayed against any chord the player holds. | MIDI, chords |
| 10 | Style model | Sections × parts (RHYTHM 1/2, BASS, CHORD 1/2, PAD, PHRASE 1/2), pattern length in bars, volume, pan, transpose. Stored in `project.json → style`. | all of the above |
| 11 | Keyboard export | **Keyboard-profile plugins** (below). | style model |
| 12 | Preview | Web Audio / WebMIDI playback of the style with a chord input. | style model |

### Keyboard-profile architecture (Phase 11)

```
backend/app/keyboards/
  base.py        KeyboardProfile: id, maker, family, models[], file extension, sections supported,
                 channels/parts, PPQ, note-range limits, export(style) -> bytes, validate(style)
  yamaha_sff1.py PSR-xxx (older) and Tyros1-era SFF1
  yamaha_sff2.py Tyros 2–5, Genos, PSR-SX: SFF2 (CASM + OTS + MDB/MH blocks)
  ...            casio / korg / roland profiles are added the same way
```

Each profile states exactly which models it targets and which sections and parts it supports.
Nothing claims universal `.sty` compatibility (spec §26). A Yamaha profile is written only after
the target model / format is confirmed (see open questions).

## 8. Open questions for the product owner

1. **Target keyboard(s) for the first export profile.** Which exact models? For example PSR-SX
   (SFF2), Genos, or a PSR-E entry model, which cannot load user `.sty` files at all.
2. **"Variation type 1, 2, 3, 4."** Does this mean the style's Main A/B/C/D variations, or
   keyboard series/generations to export for?
3. **Stem separation cost.** Demucs needs ~2 GB of model data and minutes of CPU per song.
   Is a local CPU acceptable, or is a GPU or server available?

## 10. Drum groove extraction and keyboard export

**Aim: the style plays the song's own beat.** "Originality" here is measurable: the hit
positions, velocities and micro-timing (swing, push, lay-back) of the recording, not a preset.

### 10.1 Transcription (`audio/drums.py`)

1. HPSS percussive part → STFT (hop 256 = 11.6 ms).
2. Sqrt-magnitude band flux in three bands: kick 40–130 Hz, snare 1.2–5 kHz, hats 7–11 kHz.
   Log scaling was tried and amplified cross-talk (kick precision 0.18).
3. Detect each onset once (summed flux, plus per-band peaks), then **classify it by spectral
   shape**.
   - A kick under a snare needs stronger low-band evidence.
   - A snare must have mid ≥ 0.85 × high.
   - A hat must not be just the top end of a snare.
   - The minimum energy over a decay window separates closed hat, open hat and crash; using a
     window ignores re-triggering by the next hit.
4. Velocity = 30 + 90 × normalised strength (spec §17). Each instrument is mapped to its
   GM note (spec §11).

Accuracy on synthetic ground truth (±40 ms), precision/recall:

| | Kick | Snare | Open hat | Crash | Closed hat |
|---|---|---|---|---|---|
| Score | 0.97/1.0 | 0.97/0.96 | 1.0/0.96 | 1.0/1.0 | 0.99/0.6 |

Closed-hat recall is the weak spot: a hat struck together with a snare is masked in a mix.
Groove extraction restores those hats **only** when the hat line is otherwise a regular
8th-note pattern, and it counts the restored hits (`inferredMaskedHats`).

### 10.2 Is there a drum kit at all?

A style is refused rather than faked. Two independent tests must pass:

- **Noise-like hits.** The median spectral flatness of hat/cymbal hits must be ≥ 0.70, measured
  only up to the file's real bandwidth (MP3 low-pass made the first version fail on
  low-bitrate files). If there are few hats, the snares must be clearly noise-like instead.
- **Rhythmic hits.** At least 60% of kick and snare hits must fall within ±15% of a grid step
  on the 16th or triplet grid. Random placement scores about 0.30.

Results on 8 real recordings, all as MP3:

| Verdict | Recordings |
|---|---|
| Drums found | drum & bass loop, electronic, country shuffle |
| Refused | solo piano, string orchestra, celesta, music box, whale song |

The music box passes the rhythm test but fails the noise test. The whale song passes the noise
test but fails the rhythm test.

### 10.3 Groove extraction (`groove.py`)

- **Grid.**
  - The beat grid is the user-corrected one.
  - It is smoothed with a local linear fit to remove tracker jitter, and extrapolated to cover
    the drums in the intro and outro.
  - The constant latency between tracked beats and drum attacks is removed: it is grid
    alignment, not feel.
- **Subdivision.** Straight 16ths or triplet/shuffle, whichever grid the kick/snare hits align
  with best.
- **Pattern discovery.**
  - Bars are clustered by cosine distance.
  - The pattern length (1, 2 or 4 bars) is the shortest period that repeats as well as the best
    one.
  - A Main must play at least two blocks back-to-back, so recurring fills don't become Mains.
- **Consensus.** A hit is kept if it appears in ≥ 50% of a groove's repetitions, with median
  velocity and median micro-timing offset. This removes transcription noise without
  quantizing the feel.
- **Section ordering.** Main A–D are ordered by intensity.
- **Fills, intro, ending, break.** Fills are the song's own bars that break the groove at phrase
  ends. The intro is where the drums enter, and the ending is the last drum bars. The break is
  the sparsest real bar.
- **Labelling.** Every section is labelled: `extracted` (from the song), `fallback` (e.g. the
  song has fewer grooves than the keyboard has Mains) or `generated`. The UI shows these as
  badges.
- **Quantize.** 0–100%, applied at export (spec §6). The default 0% keeps the drummer's timing.

Measured on a synthetic song with 33% swing: swing 33.6%, both grooves slot-exact, both fills
found.

### 10.4 Keyboard profiles and the SFF1 writer (`keyboards/`)

**One format for every series.** SFF1 is the only format the E- and i-series accept, and SFF2
instruments load SFF1 and convert it. The writer follows the Wierzba/Bedesem *Style Files –
Introduction and Details* v2.1:

- **Layout.** Type 0 SMF. Bar 1 holds time signature, tempo, `SFF1`, the style name, `SInt`,
  GM-On and the drum-kit setup. Sections start at bar 2, and fills are exactly one bar long.
- **Channels.** Drums are on MIDI channel 10. This is a "simple" style (channels 9–16 in their
  standard roles), so no CASM block is needed.

The writer's output is verified by parsing it back with mido for every profile (test_style.py).

| Family | Models | Sections | PPQ | Size budget | How sure |
|---|---|---|---|---|---|
| Arranger | Genos/2, PSR-SX600–920, PSR-S670–975 | Intro A–B, Main A–D, Fill AA–DD, Break, Ending A–B | 1920 | none | format reference |
| Portable USB | PSR-E473, EW425, E463, EW410, I500 | Intro A, Main A–B, Fill AA/BB/AB/BA, Ending A | 480 | 50 KB | E473: owner's manual; others: community reports |
| Portable via computer | PSR-E383, E373, I400, I425 | same | 480 | 50 KB (I425: 30 KB) | community reports |
| No style loading | PSR-E283/E273/E263 | MIDI file only | – | – | – |

- **Two-variation keyboards (PSR-E / EW / I).** These have only Main A/B buttons. The song's
  grooves are extracted as up to four (A–D, ordered by intensity) and split into **two style
  files**:
  - `Song 1.STY` carries grooves A + B.
  - `Song 2.STY` carries grooves C + D, mapped onto the keyboard's A/B buttons, with their own
    fills.

  A second file is made only when the song has at least 3 distinct grooves. The report's
  `styleFiles` list and the section `origin` field show the mapping. `styles.zip` bundles both.
- **User-edited variations.**
  - The user can choose which song groove plays on each Main button, with any groove on any
    button and repeats allowed. On 2-variation keyboards they also choose 1 or 2 styles.
  - The choice is stored per layout kind in `project.json → variationMap`, as
    `{"two": [["D","A"], ...], "four": [["A","B","C","D"]]}`. It is set with
    `PUT /api/projects/{id}/variations`, and `styles: null` resets to automatic.
  - Each groove brings its own fill. On 4-variation keyboards the Break is kept.
  - If a re-analysis no longer finds a groove used in a saved choice, the automatic choice is
    used and a note is added to the report.
- **Over-budget files.** If a file exceeds its budget, patterns are shortened step by step
  (4 → 2 → 1 bars), and every change is reported.
- **File names** contain exactly one period: PSR-E models treat everything after the first
  period as the extension.
- **Unverified models** carry a "check your manual" badge in the UI.

### 10.5 Still missing for a full accompaniment style

Bass, chord, pad and phrase parts need chord detection (Phase 4) plus pitched-part
transcription and the chord-relative engine (Phases 6 and 9). `theory.py` already has the
CMaj7-relative transforms that SFF requires for channels 11–16.

## 9. Running and testing

```
start.bat                                   first run creates backend\.venv and installs requirements
backend\.venv\Scripts\python -m pytest backend\tests -q     run from backend\: 100 tests, ~90 s
```

Real-recording tests download librosa's CC-licensed example tracks once, and are skipped when
offline.

## 11. Hosting (free): Hugging Face Spaces

The repository is a ready-made **Docker Space**. `README.md` carries the Space front matter
(`sdk: docker`, `app_port: 7860`), and the `Dockerfile` builds the app on `python:3.13-slim`.

| Setting (env) | Container value | Effect |
|---|---|---|
| `LAK_PUBLIC` | `1` | Each browser sees only its own songs. Ownership uses an anonymous random id the browser keeps in localStorage and sends as `X-Client-Id`. Project ids are random 128-bit values. |
| `LAK_RETENTION_DAYS` | `3` | Songs untouched for 3 days are deleted, on startup and on every upload. |
| `LAK_DATA_DIR` | `/home/user/app/data` | Where uploads and analyses are stored. |
| `NUMBA_CACHE_DIR` | `/tmp/numba-cache` | A writable JIT cache for librosa. The image build warms it. |

Free tier (CPU Basic, 2 vCPU, 16 GB RAM):

- Storage is **not persistent**. A restart or rebuild clears all songs, so users must download
  their styles.
- The Space sleeps after a period without visitors and takes about a minute to wake.
- Analysis runs one job at a time, so concurrent visitors queue.

Local use is unchanged: `start.bat` runs with `LAK_PUBLIC` off and keeps songs forever.

# MeleeComboMaker

Automatically detect and render combo highlight clips from [Slippi](https://slippi.gg/) `.slp` replay files.

---

## What it does

MeleeComboMaker reads a Melee replay, finds the best combo sequences, and outputs trimmed `.mp4` clips — ready to post on Instagram, TikTok, or YouTube.

```
replay.slp  →  combo detection  →  Dolphin render  →  FFmpeg trim  →  combo_01.mp4
```

---

## Motivation

Manually scrubbing through replays to find highlight moments is tedious. This tool automates the full pipeline: parse the replay data, score every punish sequence, render only the frames that matter, and output a clip named after the player who landed the combo.

---

## Pipeline

```
.slp file
   │
   ├─ 1. Combo Detection (melee/detection.py)
   │       HitSequence state-machine — requires 5+ discrete hits,
   │       gap limits, offstage leniency, attacker-interrupt checks
   │
   ├─ 2. Full-match Render (melee/renderer.py)
   │       Dolphin (Slippi playback build) → full .mp4
   │
   ├─ 3. Clip Trimming (melee/trimmer.py)
   │       FFmpeg cuts each combo window from the full render
   │
   └─ 4. Output
           {PlayerName}_{replay}_{combo_01..N}.mp4
```

---

## Requirements

| Dependency | Notes |
|---|---|
| Python 3.11+ | |
| [Slippi Launcher](https://slippi.gg/) | Provides the Dolphin playback build |
| [FFmpeg](https://ffmpeg.org/) | For clip trimming |
| Melee ISO | `Super Smash Bros. Melee (v1.02).iso` |
| [slp2mp4](https://github.com/NunoDasNeves/slp-to-mp4) | Wired up via `slp2mp4/config_windows.json` |

---

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/andrewdkim47/MeleeComboMaker.git
cd MeleeComboMaker
```

**2. Create a virtual environment (Python 3.11+)**
```bash
python -m venv .venv311
# Windows
.venv311\Scripts\activate
# macOS/Linux
source .venv311/bin/activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
pip install -e .
```

**4. Configure slp2mp4**

Copy the example config and fill in your paths:
```bash
cp slp2mp4/config_windows.example.json slp2mp4/config_windows.json
```

```json
{
  "melee_iso": "C:/path/to/SSBM.iso",
  "dolphin_dir": "C:/path/to/Slippi Launcher/playback",
  "ffmpeg": "C:/path/to/ffmpeg.exe",
  "resolution": "720p",
  "widescreen": false,
  "bitrateKbps": 8000
}
```

---

## Usage

### Generate combo clips from a replay

```bash
python scripts/render_clips.py tests/replays/my_game.slp -o comboVids/
```

This will:
1. Detect combos in the replay
2. Render the full match via Dolphin (takes a few minutes)
3. Trim each combo window with FFmpeg
4. Output clips named `{PlayerName}_{replay}_combo_01.mp4`, etc.

### Options

```
python scripts/render_clips.py <slp_path> [options]

  -o, --output-dir          Output directory for clips (default: comboVids)
  --max-combos N            Max clips to export (default: 5)
  --padding-frames N        Buffer frames around each combo (default: 120)
  --full-video-path PATH    Skip re-rendering — use an existing full-match mp4
  --full-video-output-dir   Where to write the full render (default: generatedVids)
```

### Skip re-rendering (if full video already exists)

```bash
python scripts/render_clips.py tests/replays/my_game.slp \
  -o comboVids/ \
  --full-video-path "generatedVids/my_game.mp4"
```

---

## Combo Detection

Combos are detected using the **HitSequence** algorithm:

- **5+ discrete hits** required (multi-hit moves like Fox dair count as one)
- **Max 5s gap** between hits; extended to **10s** when victim is offstage/recovering
- **Attacker interrupt**: combo is invalidated if the attacker takes 3+ distinct hits
- Clips start **3.5s before** the first hit and end **2.5s after** the last hit (or **3s after** a kill)
- Falls back to a rolling-window detector for older replay formats

Combos are scored by damage dealt and ranked — the best clip is always `combo_01`.

---

## Project Structure

```
melee/              Core library
  detection.py      HitSequence combo detector
  renderer.py       Dolphin render wrapper
  trimmer.py        FFmpeg clip trimmer
  assembler.py      moviepy highlight assembler
  data/             Action state + attack name tables

scripts/
  render_clips.py   CLI entry point
  visualize.py      Dev tool — combo intensity graphs

server/
  app.py            Flask API (upload → analyze → render → download)

tests/
  replays/          Sample .slp files for testing

slp2mp4/            slp-to-mp4 integration (Dolphin + FFmpeg config)
```

---

## Supported Stages (offstage detection)

Battlefield · Final Destination · Dreamland 64 · Fountain of Dreams · Yoshi's Story · Pokémon Stadium

---

## Limitations

- **1v1 only** — teams and free-for-all are not supported
- **8 minute max** match duration
- Rendering requires a legal Melee ISO and Dolphin playback build
- Windows is the primary supported platform (Dolphin path config)


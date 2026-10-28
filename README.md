# Vidigen

A Windows desktop app that turns a prompt (plus, optionally, a song) into a 30–90 second vertical (9:16) music video for TikTok. It runs entirely on your own machine using local image, video and language models.

![Vidigen](docs/screenshot.png)

## How it works

Only the vision prompt is required. Without a song, cuts follow a beat grid at the tempo you choose and the video is exported silent, ready for a sound picked inside TikTok. Your own footage is optional too (video-to-video), and so is the cast.

1. **Beat analysis.** The song is decoded with ffmpeg, then a numpy beat tracker (spectral-flux onsets, autocorrelation tempo, dynamic-programming beats) finds tempo, beats, downbeats and a loudness curve. By default the clip is the most energetic stretch of the song (usually the chorus or drop), starting on a downbeat.
2. **Shot planning.** Cuts land on beats: one bar per shot in loud sections, two bars in quiet ones. A local LLM via [Ollama](https://ollama.com) (default `qwen3:8b`) writes a prompt and camera move for each shot from your concept and style. Without Ollama it falls back to a template planner.
3. **Keyframes.** A text-to-image model renders one still per shot. Shots that feature a cast member are conditioned on that character's face photos through an IP-Adapter face model, so they look like the person.
4. **Motion.** An image-to-video model animates each keyframe. If you add your own footage, each shot instead restyles the matching part of that clip (video-to-video).
5. **Edit and mix.** ffmpeg fits every clip to 1080×1920 at 30 fps and its exact beat-synced length, cuts them together, lays the song under it with fades, and writes a suggested caption that includes the song credit.

## Characters

![Character editor](docs/characters.png)

On the **Characters** tab, upload 3–6 photos of yourself (or of someone who has agreed to it) and confirm you have the right to use the likeness. Vidigen crops each face and uses the crops as references. Then tune the character:

- **Generate previews**: four portraits from the current settings.
- **Revisions**: notes such as "hair a bit shorter" that are added to the description for the next round.
- **Likeness**: how strongly the photos steer the face versus the scene and style.
- **Style**: realistic, cartoon, anime, 3D animated or meme.
- **Pin a preview** ("This is me"): it becomes the avatar and, optionally, an extra reference.
- Untick photos that pull the likeness off.

On the **Create** tab, pick characters in the **Cast** row under the vision prompt. Each shot features one cast member, framed medium or closer so the face carries.

## Presets

| Preset | VRAM | Keyframes | Motion | Download |
|---|---|---|---|---|
| Test mode | none | procedural art, face pasted in | ffmpeg pan/zoom | none |
| Lite | 6 GB+ | SDXL-Turbo + IP-Adapter face | ffmpeg pan/zoom | ~9 GB |
| Balanced | 12 GB+ | SDXL 1.0 + IP-Adapter face | LTX-Video 0.9.7 distilled | ~32 GB |
| Quality | 24 GB+ | FLUX.1-schnell + XLabs IP-Adapter | Wan 2.2 TI2V-5B | ~60 GB |

The Welcome screen recommends a preset from the detected GPU (an RTX 5080 with 16 GB gets **Balanced**). You can switch presets per render once they're installed.

## Song rights

If you add a song, you can note its licence (your own work, CC0, public domain, CC BY / BY-SA, or a licence you hold) and the title and artist. That credit goes into the video metadata and the suggested caption. Vidigen can't check copyright for you and never blocks a render; CC BY songs just get a reminder to add the credit. Good sources: [Free Music Archive](https://freemusicarchive.org), [ccMixter](https://ccmixter.org), [Pixabay Music](https://pixabay.com/music/), [Musopen](https://musopen.org).

## Setup (Windows)

![Welcome screen](docs/welcome.png)

Requirements: Windows 10/11 and Node.js 22. An NVIDIA GPU is needed for the AI presets.

```powershell
git clone https://github.com/michaelbudko/vidigen
cd vidigen\app
npm install
npm run dev
```

The **Welcome** screen does the rest:

1. **Python.** It finds Python 3.10+ (skipping the Microsoft Store stub). If there isn't one, **Install Python for me** installs Python 3.12 with winget.
2. **Engine.** It creates a private environment and installs the engine. ffmpeg comes bundled, so nothing else is needed.
3. **Scan.** It reads your GPU, VRAM, driver, CPU, RAM and free disk, then recommends a preset.
4. **Install.** It installs PyTorch for CUDA 12.8 (required for RTX 50-series), the AI libraries, and the preset's models, with progress bars. Optionally it also installs Ollama and a director LLM (`qwen3:8b` on 10 GB+ cards).

Reopen it any time with **Setup** in the title bar. `setup.ps1` still exists for a scripted install.

## Command line

```powershell
cd engine
.\.venv\Scripts\python -m vidigen_engine system
.\.venv\Scripts\python -m vidigen_engine render song.mp3 "a paper boat in a neon storm" --style vaporwave --preset balanced --license own_work --duration 45
```

## Layout

```
app/       Electron + React + Vite desktop UI
  electron/  main process: starts the engine, file dialogs, media protocol
  src/       renderer UI
engine/    Python engine (FastAPI on 127.0.0.1, token-protected)
  vidigen_engine/
    audio.py      beat/tempo/energy analysis
    planner.py    beat-synced shot list, LLM or template prompts, cast assignment
    characters.py character store: photos, face crops, revisions, previews
    hardware.py   GPU/CPU/RAM/disk scan
    installer.py  Welcome-screen setup: PyTorch, AI libraries, model downloads, Ollama
    backends/     image (mock, diffusers) and video (pan/zoom, LTX-Video, Wan) generators
    compose.py    ffmpeg fit, cut and mix
    pipeline.py   render jobs
    server.py     local API for the UI
```

## Status

The whole flow is tested end to end in Test mode: Welcome, character creation, previews and revisions, and renders with a cast. The diffusers backends (SDXL, SDXL-Turbo and FLUX with IP-Adapter face; LTX-Video; Wan) and the model downloads are written against diffusers 0.42, but they still need a first run on a real GPU to tune steps, resolution, likeness strength and memory settings.

Next up: stronger identity models (InstantID / PhotoMaker) if IP-Adapter likeness isn't close enough, a ComfyUI backend option, lyric and caption overlays, beat-synced transitions, and a packaged Windows installer.

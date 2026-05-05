# Youtok

YouTube → 9:16 short-form clip cutter. Auto download → transcribe → topic-segment by LLM → snap to scene/pause → render with title overlay + word-highlight subtitle.

**Status:** Planning (2026-05-04). Build pending.

## Quick start

### Prerequisites

- Python 3.11+
- ffmpeg + ffprobe (bundled in `assets/bin/` per OS, or system-installed)
- (Optional) NVIDIA GPU with CUDA 12 for fast WhisperX
- Anthropic API key for Claude Sonnet 4.6

### Mac dev

```bash
cd projects/youtok
./scripts/install-mac.sh
cp .env.example .env  # fill ANTHROPIC_API_KEY
./scripts/run-server.sh   # http://localhost:8000
# In another terminal:
./scripts/run-worker.sh
```

### Windows production

```powershell
cd projects\youtok
.\scripts\install-win.ps1
copy .env.example .env  # fill ANTHROPIC_API_KEY
.\scripts\run-server.ps1
# In another terminal:
.\scripts\run-worker.ps1
```

### License activation

First run: open `http://localhost:8000` → enter license key → tool binds to this machine.

To generate a license (admin only):

```bash
python -m youtok.license.keygen \
  --email user@example.com \
  --expires 2026-12-31 \
  --max-jobs-per-day 100
# prints: YOUTOK-XXXX-XXXX...
```

## Architecture

Local web app, single-machine deployment, license-locked.

```
Browser (localhost) → FastAPI server → SQLite ←→ Worker (Python)
                                          ↓
                                    Pipeline modules
                                    (yt-dlp, WhisperX, Claude API, ffmpeg)
```

## Pipeline overview

1. **Download** YouTube URL (yt-dlp) → mp4 + 16kHz wav
2. **Transcribe** word-level (WhisperX) → sentence-numbered JSON
3. **Topic segment** (Claude Sonnet 4.6, 15 rules) → topic tree
4. **Validate + auto-split** (Stage B LLM) → cleaned tree
5. **Length normalize** → ≥60s clips, max 240s
6. **Snap cut points** to pause + shot boundary (PySceneDetect)
7. **Render** 9:16 (1080×1920) with:
   - Title overlay top (Inter Bold, "Title - Part X/Y")
   - Word-highlight subtitle bottom (CapCut-style ASS karaoke)
8. **Cleanup** source mp4/wav

Output:

```
{output_dir}/{video-slug_videoId}/
├── clips/
│   ├── 01_<topic-slug>.mp4
│   ├── 02_<topic-slug>.mp4
│   └── ...
├── manifest.json
├── transcript.json
└── topic-tree.json
```

## Build sessions (Claude Code)

Build is split into 6 sessions for parallel execution. See `sessions/`:

- `01-foundation.md` — sequential, must run first
- `02-pipeline.md` — parallel, core video logic
- `03-web-ui.md` — parallel, FastAPI + Jinja + HTMX
- `04-worker.md` — parallel, job queue + progress
- `05-integration.md` — sequential, after 02+03+04
- `06-polish.md` — sequential, last

Each session is a self-contained prompt for one Claude Code window.

## Spec

Full spec: [`SPEC.md`](./SPEC.md).

## Wiki entries

- Design doc: `wiki/sources/projects/youtok.md`
- Entity: `wiki/entities/youtok.md`
- Decision: `wiki/sources/decisions/2026-05-04-launch-youtok.md`
- Session: `wiki/sources/sessions/2026-05-04-design-youtok.md`

## Owner

Hai Phan — `phanhai.work@gmail.com`

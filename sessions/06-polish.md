# Session 06 — Polish

> Run **after Session 05 integration**. Last session.

## Goal

Add the production polish: structured logging, error UX, settings page, optional packaged build, smoke test, README final pass.

## Read first

- `../SPEC.md` sections 16 (errors), 21 (risks), 22 (future)
- `data/integration-notes.md` from Session 05

## Tasks

### 1. Logging hardening

In `youtok/__init__.py`:

```python
from loguru import logger
import sys
from youtok.config import settings

logger.remove()  # remove default

logger.add(
    sys.stderr,
    level=settings.log_level,
    format="<green>{time:HH:mm:ss}</green> <level>{level: <8}</level> {name}:{function}:{line} - {message}",
    colorize=True,
)
logger.add(
    settings.data_dir / "logs" / "youtok-{time:YYYY-MM-DD}.log",
    rotation="10 MB",
    retention="14 days",
    level="DEBUG",
    encoding="utf-8",
    enqueue=True,  # safe across processes
)
```

### 2. Better error UX

For each failure mode, define user-facing message:

| Internal | User sees |
|---|---|
| `yt_dlp.utils.DownloadError: Video unavailable` | "Video unavailable or removed from YouTube." |
| `OSError: No space left on device` | "Disk full — free up space and retry." |
| `anthropic.RateLimitError` | "AI service rate-limited. Will retry automatically." |
| `subprocess.CalledProcessError` (ffmpeg) | "Video encoding failed — file may be corrupted." |
| `whisperx.OOM` | "Audio too long for current memory. Try a shorter video." |
| `InvalidLicense` | "License key invalid or expired." |

Build a `friendly_error(exc)` helper in `core/errors.py`:

```python
ERROR_MAP = {
    "Video unavailable": "Video unavailable or removed from YouTube.",
    "No space left": "Disk full — free up space and retry.",
    ...
}

def friendly_error(exc: Exception) -> str:
    msg = str(exc)
    for needle, friendly in ERROR_MAP.items():
        if needle in msg:
            return friendly
    return f"Unexpected error: {type(exc).__name__}. See logs."
```

In `queue/tasks.py`, set `job.error_message = friendly_error(e)` on failure.

### 3. Settings page

`/settings` route showing:
- License info (email, expires, machine_id, max_jobs_per_day)
- Anthropic API key status (set / not set, **never display the key**)
- Storage stats (total disk used by data/ folder)
- Default config (min/max clip duration, paste new defaults)
- Version number from `pyproject.toml`

Template `web/templates/settings.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="grid grid-cols-2 gap-6">
  <div class="glass rounded-2xl p-6">
    <h2 class="text-xl font-bold mb-4">License</h2>
    <dl class="space-y-2 text-sm">
      <div><dt class="text-white/60 inline">Email:</dt> <dd class="inline">{{ license.email }}</dd></div>
      <div><dt class="text-white/60 inline">Expires:</dt> <dd class="inline">{{ license.expires_at or "Never" }}</dd></div>
      <div><dt class="text-white/60 inline">Machine:</dt> <dd class="inline font-mono text-xs">{{ license.machine_id }}</dd></div>
      <div><dt class="text-white/60 inline">Max jobs/day:</dt> <dd class="inline">{{ license.max_jobs_per_day or "Unlimited" }}</dd></div>
    </dl>
  </div>
  
  <div class="glass rounded-2xl p-6">
    <h2 class="text-xl font-bold mb-4">Configuration</h2>
    <form method="POST" class="space-y-3">
      <label class="block">
        <span class="text-sm text-white/60">Min clip duration (sec)</span>
        <input name="min_clip" type="number" value="{{ config.min_clip_duration_sec }}" class="glass w-full rounded p-2 mt-1">
      </label>
      <label class="block">
        <span class="text-sm text-white/60">Max clip duration (sec)</span>
        <input name="max_clip" type="number" value="{{ config.max_clip_duration_sec }}" class="glass w-full rounded p-2 mt-1">
      </label>
      <button class="gradient-btn px-4 py-2 rounded text-sm">Save</button>
    </form>
  </div>
  
  <div class="glass rounded-2xl p-6 col-span-2">
    <h2 class="text-xl font-bold mb-4">Storage</h2>
    <p class="text-sm text-white/60 mb-2">data/ folder uses {{ data_size_mb }} MB</p>
    <p class="text-xs text-white/40">Logs older than 14 days are auto-pruned.</p>
  </div>
</div>
{% endblock %}
```

### 4. Smoke test script

`scripts/smoke-test.py`:

```python
"""
End-to-end smoke test. Submits 1 known-stable test video,
waits for completion, verifies clips output.
Exit 0 = pass, exit 1 = fail. Suitable for cron / CI.
"""
import sys, time, json
from pathlib import Path
import httpx
from youtok.config import settings

TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # 18-second video, "Me at the zoo"
TEST_OUTPUT = Path("/tmp/youtok-smoke")


def main():
    TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    
    # Submit
    r = httpx.post("http://localhost:8000/jobs/", data={
        "source_url": TEST_URL,
        "output_dir": str(TEST_OUTPUT),
    }, follow_redirects=False)
    if r.status_code != 303:
        print(f"FAIL submit: {r.status_code}"); sys.exit(1)
    job_id = r.headers["HX-Redirect"].split("/")[-1]
    
    # Poll
    for _ in range(120):  # 10 min max
        time.sleep(5)
        r = httpx.get(f"http://localhost:8000/jobs/{job_id}/manifest", follow_redirects=False)
        if r.status_code == 200:
            manifest = r.json()
            print(f"PASS: {manifest['total_clips']} clips")
            sys.exit(0)
    
    print("FAIL: timed out"); sys.exit(1)


if __name__ == "__main__":
    main()
```

(Note: this 18s test video is too short to actually segment — use it to test the upload+download path, not full pipeline. For full pipeline smoke, pick a 3min Khan Academy video as in Session 05.)

### 5. (Optional) Packaged build

PyInstaller `--onedir` build for distribution.

`build.spec`:

```python
# PyInstaller spec
a = Analysis(
    ['src/youtok/cli.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/fonts', 'assets/fonts'),
        ('assets/keys', 'assets/keys'),
        ('assets/bin', 'assets/bin'),
        ('src/youtok/web/templates', 'youtok/web/templates'),
        ('src/youtok/web/static', 'youtok/web/static'),
        ('alembic', 'alembic'),
        ('alembic.ini', '.'),
    ],
    hiddenimports=[
        'youtok.api.main',
        'youtok.queue.tasks',
        'youtok.db.models',
    ],
)
...
```

Bundle wrapper script `start.bat` (Windows):

```batch
@echo off
cd /d "%~dp0"
start "Youtok Server" /B youtok.exe server
start "Youtok Worker" /B youtok.exe worker
timeout /t 3 >nul
start http://localhost:8000
```

`youtok` CLI gains:
- `youtok server` — runs uvicorn
- `youtok worker` — runs huey consumer
- `youtok activate` — CLI activation prompt

This is optional for MVP. CLI dev install via `uv` is fine for both Mac and Windows.

### 6. README final

Sections to expand:
- Troubleshooting (from Session 05's integration-notes)
- Performance notes (CPU vs GPU benchmarks)
- License management (how to gen, how to reset, what happens on machine swap)
- Common YouTube failures (bot detection, age-restricted, region-locked)
- FAQ

### 7. CHANGELOG.md

Start one:

```markdown
# Changelog

## [0.1.0] - 2026-XX-XX
First MVP release.

### Features
- YouTube download via yt-dlp
- WhisperX word-level transcription
- Topic segmentation via Claude Sonnet 4.6 with 15-rule prompt
- 9:16 render with title overlay + word-highlight subtitles
- License key activation (offline RSA, machine-locked)
- FastAPI + HTMX glass-morphism UI
- Single-URL, bulk paste, channel scrape modes
- Mac (arm64) and Windows support
```

## Acceptance test

1. Submit a malformed URL → get friendly error message in UI, not Python traceback.
2. `/settings` page renders correctly with current license info.
3. `python scripts/smoke-test.py` passes against a running server+worker.
4. `loguru` log file exists at `data/logs/youtok-{date}.log`, rotates at 10MB.
5. README has all troubleshooting entries from integration session.

## Anti-patterns to avoid

- Logging the license key, API key, or any token at any level.
- Settings page with editable fields that don't actually persist (worse than read-only).
- PyInstaller build that misses datas (templates, fonts) — test the bundled exe on a clean machine.
- Smoke test that depends on server being run from a specific cwd.

## Tool is shipped when

All sessions 01–06 acceptance tests pass on both Mac and Windows. Wiki entry status updated `Planning` → `Active` (or `Shipped`).

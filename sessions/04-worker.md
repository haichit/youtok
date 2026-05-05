# Session 04 — Worker (queue + channel scraping)

> Run **after Session 01**. Independent of 02, 03 — can run in parallel.

## Goal

Build the Huey worker that polls the jobs table, calls `core.pipeline.run_pipeline`, and updates progress. Plus channel scraping module that enumerates videos from a YouTube channel/playlist URL.

## Read first

- `../SPEC.md` sections 4 (architecture — worker process), 6 (Job + Clip schema), 7.6 (pipeline orchestrator), 13 (channel scraping)
- Code skeleton from Session 01: `src/youtok/queue/`, `src/youtok/db/`

## Deliverables

### 1. `queue/huey_app.py`

```python
from huey import SqliteHuey
from youtok.config import settings

huey = SqliteHuey(
    name="youtok",
    filename=str(settings.queue_db_path),
    immediate=False,
    results=False,
    store_none=False,
)
```

### 2. `queue/tasks.py`

```python
from datetime import datetime
from loguru import logger
from youtok.queue.huey_app import huey
from youtok.db.base import SessionLocal
from youtok.db.models import Job
from youtok.core.pipeline import run_pipeline


@huey.task(retries=0, retry_delay=0)
def process_job(job_id: int):
    logger.info(f"Worker picked up job {job_id}")
    
    def progress_callback(step: str, pct: int, message: str = ""):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.current_step = step
                job.progress_pct = pct
                if message:
                    logger.info(f"job {job_id} | {step} {pct}% | {message}")
                db.commit()
    
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "downloading"
        job.started_at = datetime.utcnow()
        db.commit()
    
    try:
        run_pipeline(job_id, progress_callback)
    except Exception as e:
        logger.exception(f"Job {job_id} failed")
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.status = "failed"
            job.error_message = str(e)[:500]
            job.finished_at = datetime.utcnow()
            db.commit()
        # Re-raise so Huey marks task as failed in queue log
        raise
```

### 3. Worker runner

`scripts/run-worker.sh`:

```bash
#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
uv run huey_consumer youtok.queue.huey_app.huey \
    --workers 1 \
    --logfile data/logs/worker.log \
    --verbose
```

`scripts/run-worker.ps1`:

```powershell
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING="utf-8"
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
uv run huey_consumer youtok.queue.huey_app.huey `
    --workers 1 `
    --logfile data\logs\worker.log `
    --verbose
```

**Critical**: `--workers 1`. Video transcoding is CPU-intensive; running multiple workers will thrash the disk + GPU. Only bump if user has serious hardware.

### 4. `core/channel.py` — Channel scraping

```python
import json
import subprocess
from datetime import datetime
from pydantic import BaseModel
from youtok.config import settings


class VideoMeta(BaseModel):
    video_id: str
    url: str
    title: str
    duration_sec: float | None
    upload_date: str | None  # YYYYMMDD


class ChannelFilters(BaseModel):
    min_duration_sec: int = 0
    max_duration_sec: int = 99999
    limit: int = 100


def enumerate_channel(url: str, filters: ChannelFilters) -> list[VideoMeta]:
    """
    yt-dlp --flat-playlist --dump-json -I 1:N <url>
    Outputs one JSON per line with metadata for each video.
    Note: --flat-playlist doesn't include duration — need a second pass
    or accept that duration filtering happens after submitting.
    """
    cmd = [
        str(settings.ytdlp),
        "--flat-playlist",
        "--dump-json",
        "-I", f"1:{filters.limit}",
        url,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
    
    videos = []
    for line in out.stdout.strip().splitlines():
        info = json.loads(line)
        videos.append(VideoMeta(
            video_id=info["id"],
            url=info.get("url") or info.get("webpage_url") or f"https://youtube.com/watch?v={info['id']}",
            title=info.get("title", ""),
            duration_sec=info.get("duration"),
            upload_date=info.get("upload_date"),
        ))
    
    # Filter by duration if available
    filtered = [
        v for v in videos
        if v.duration_sec is None or
           filters.min_duration_sec <= v.duration_sec <= filters.max_duration_sec
    ]
    
    return filtered
```

For full duration-based filtering, do a second pass — `yt-dlp --print "%(id)s %(duration)s" <url>` for each video. But for MVP, accept that some videos in preview show "duration unknown" and let the user check manually.

### 5. Channel job spawning

In `api/routes/jobs.py` (Session 03 may have stubbed):

```python
@router.post("/channel")
async def create_from_channel(
    channel_url: str = Form(...),
    selected_video_urls: list[str] = Form(...),  # checkbox names
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    license = manager.get_active_license(db)
    
    # Parent job
    parent = crud.create_job(
        db, license_id=license.id, source_type="channel",
        source_url=channel_url, output_dir=output_dir, status="running",
    )
    
    # Child jobs (one per video)
    child_ids = []
    for url in selected_video_urls:
        child = crud.create_job(
            db, license_id=license.id, source_type="video",
            source_url=url, output_dir=output_dir,
            parent_job_id=parent.id,
        )
        process_job(child.id)
        child_ids.append(child.id)
    
    return RedirectResponse("/dashboard", status_code=303,
                            headers={"HX-Redirect": "/dashboard"})
```

Parent job aggregate progress: pct = avg(child.pct), status = "done" when all children done. Implement a small helper that updates parent based on children.

### 6. Channel preview template

`web/templates/partials/channel_preview.html`:

```html
<form method="POST" action="/jobs/channel" class="space-y-4">
  <input type="hidden" name="channel_url" value="{{ channel_url }}">
  <input name="output_dir" placeholder="/Users/me/Videos/Cuts" required class="w-full glass rounded-lg p-3">
  
  <div class="glass rounded-xl p-4 max-h-96 overflow-y-auto">
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm text-white/60">{{ videos|length }} videos found</span>
      <button type="button" onclick="toggleAll(this)" class="text-xs text-accent-pink">Toggle all</button>
    </div>
    {% for v in videos %}
    <label class="flex items-center gap-3 py-2 hover:bg-white/5 rounded">
      <input type="checkbox" name="selected_video_urls" value="{{ v.url }}" checked>
      <div class="flex-1">
        <div class="text-sm">{{ v.title }}</div>
        <div class="text-xs text-white/40">{% if v.duration_sec %}{{ "%.0f"|format(v.duration_sec / 60) }}min{% else %}? min{% endif %} · {{ v.upload_date or '?' }}</div>
      </div>
    </label>
    {% endfor %}
  </div>
  
  <button type="submit" class="gradient-btn px-6 py-2 rounded-lg font-semibold">Create {{ videos|length }} jobs</button>
</form>

<script>
  function toggleAll(btn) {
    const cbs = document.querySelectorAll('input[name="selected_video_urls"]');
    const allChecked = Array.from(cbs).every(cb => cb.checked);
    cbs.forEach(cb => cb.checked = !allChecked);
  }
</script>
```

### 7. Logging

In `youtok/__init__.py` or `cli.py`, configure loguru:

```python
from loguru import logger
from youtok.config import settings

logger.add(
    settings.data_dir / "logs" / "youtok-{time}.log",
    rotation="10 MB",
    retention="7 days",
    level=settings.log_level,
    encoding="utf-8",
)
```

### 8. Resume on crash

If worker process crashes mid-job, on restart:

```python
# scripts/run-worker.sh prepend:
uv run python -c "
from youtok.db.base import SessionLocal
from youtok.db.models import Job
from datetime import datetime, timedelta
with SessionLocal() as db:
    # Mark stuck jobs as failed if started > 30 min ago and not in done/failed
    stuck = db.query(Job).filter(
        Job.status.notin_(['done', 'failed', 'pending']),
        Job.started_at < datetime.utcnow() - timedelta(minutes=30),
    ).all()
    for j in stuck:
        j.status = 'failed'
        j.error_message = 'Worker crashed mid-job; manual restart needed'
    db.commit()
"
```

Better: track heartbeat in DB. For MVP, the timeout-based recovery above is enough.

## Acceptance test

Worker:

```bash
# Terminal 1
./scripts/run-server.sh
# Terminal 2
./scripts/run-worker.sh
```

Submit a job via UI (http://localhost:8000). Watch worker terminal:
- Should print `[INFO] Worker picked up job 1`
- Then call into pipeline (which may stub if Session 02 not yet integrated — for now, mock pipeline.run_pipeline to just sleep + emit progress events)
- Job status should progress: pending → downloading → ... → done
- Dashboard refreshes every 5s and shows updated status
- WebSocket on job detail page emits live progress

Channel scraping (CLI test):

```bash
uv run python -c "
from youtok.core.channel import enumerate_channel, ChannelFilters
videos = enumerate_channel('https://youtube.com/@veritasium', ChannelFilters(limit=10))
for v in videos:
    print(v.title, v.duration_sec)
"
```

Expected: 10 videos, with titles. Some may have `duration_sec=None`.

UI test:
- New Job → Channel tab → paste channel URL → click Preview
- Should render a list of checkbox rows
- Toggle some checkboxes off → click "Create N jobs" → redirects to dashboard with N new jobs

## Anti-patterns to avoid

- Running multiple worker processes — disk thrash, GPU OOM, race conditions in pipeline workdir.
- Updating job progress on every word transcribed — debounce to 1-second granularity.
- Catching `Exception` and silently passing in pipeline — at least log + mark job failed.
- Hardcoding `python -m huey_consumer` — use the installed `huey_consumer` script from venv.
- Not setting `PYTHONIOENCODING=utf-8` on Windows — yt-dlp output with non-ASCII titles will crash.
- Spawning child jobs for channel synchronously in API handler — this could block UI for 30s+ on a big channel. If yt-dlp enumerate takes > 10s, do it async (background task) and show a loading spinner.

## When done

Notify integration session (05). Provide:
- One end-to-end test job that completed successfully
- Worker log file with progress events

# Session 03 — Web UI (FastAPI + Jinja + HTMX + glass-morphism)

> Run **after Session 01**. Independent of 02, 04 — can run in parallel.

## Goal

Build the FastAPI server with all HTML pages (activate, dashboard, new job, job detail), API endpoints (mock data ok at first), license middleware, and WebSocket progress endpoint. Glass-morphism design from veo-farm.

## Read first

- `../SPEC.md` sections 4 (architecture), 10 (license), 11 (UI design tokens), 12 (API endpoints)
- Code skeleton from Session 01: `src/youtok/api/`, `src/youtok/web/`

## Deliverables

### 1. `api/main.py` — FastAPI factory

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from youtok.api.routes import activate, jobs, channels, pages
from youtok.api.ws import register_ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown


def create_app() -> FastAPI:
    app = FastAPI(title="Youtok", lifespan=lifespan)
    
    app.mount("/static", StaticFiles(directory="src/youtok/web/static"), name="static")
    
    app.include_router(pages.router)
    app.include_router(activate.router, prefix="/activate")
    app.include_router(jobs.router, prefix="/jobs")
    app.include_router(channels.router, prefix="/channels")
    register_ws(app)
    
    return app


app = create_app()
```

### 2. `api/deps.py` — license middleware

```python
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from youtok.license.manager import is_activated


async def license_required(request: Request):
    """Use as dependency on protected routes. Activate page bypasses."""
    if not is_activated():
        raise HTTPException(
            status_code=302,
            headers={"Location": "/activate"},
        )
    return True
```

For HTML routes, prefer `RedirectResponse` over exception:

```python
def check_license_or_redirect():
    if not is_activated():
        return RedirectResponse("/activate", status_code=302)
    return None
```

### 3. `api/routes/pages.py` — HTML routes

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="src/youtok/web/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_activated():
        return RedirectResponse("/activate")
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    jobs = crud.list_jobs(db, limit=50)
    stats = crud.get_stats(db)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "jobs": jobs, "stats": stats,
    })


@router.get("/jobs/new", response_class=HTMLResponse)
async def jobs_new(request: Request):
    if redirect := check_license_or_redirect():
        return redirect
    return templates.TemplateResponse("new_job.html", {"request": request})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    if redirect := check_license_or_redirect():
        return redirect
    job = db.get(Job, job_id)
    clips = crud.list_clips(db, job_id)
    return templates.TemplateResponse("job_detail.html", {
        "request": request, "job": job, "clips": clips,
    })
```

### 4. `api/routes/activate.py`

```python
@router.get("/", response_class=HTMLResponse)
async def activate_page(request: Request):
    return templates.TemplateResponse("activate.html", {"request": request})


@router.post("/", response_class=HTMLResponse)
async def activate_submit(
    request: Request,
    license_key: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        license = manager.activate(license_key, db)
    except InvalidLicense as e:
        return templates.TemplateResponse("activate.html", {
            "request": request, "error": str(e),
        })
    return RedirectResponse("/dashboard", status_code=303)
```

### 5. `api/routes/jobs.py`

```python
@router.get("/")
async def list_jobs_partial(request: Request, partial: int = 0, db: Session = Depends(get_db)):
    jobs = crud.list_jobs(db, limit=50)
    if partial:
        return templates.TemplateResponse("partials/job_table.html", {"request": request, "jobs": jobs})
    raise HTTPException(404)  # full page goes through /dashboard


@router.post("/")
async def create_job(
    source_url: str = Form(...),
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    license = manager.get_active_license(db)
    job = crud.create_job(db,
        license_id=license.id,
        source_type="video",
        source_url=source_url,
        output_dir=output_dir,
    )
    # enqueue
    from youtok.queue.tasks import process_job
    process_job(job.id)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303,
                            headers={"HX-Redirect": f"/jobs/{job.id}"})


@router.post("/bulk")
async def create_bulk(
    urls: str = Form(...),
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    license = manager.get_active_license(db)
    job_ids = []
    for url in urls.splitlines():
        url = url.strip()
        if not url:
            continue
        job = crud.create_job(db, license_id=license.id, source_type="bulk",
                              source_url=url, output_dir=output_dir)
        process_job(job.id)
        job_ids.append(job.id)
    return RedirectResponse("/dashboard", status_code=303,
                            headers={"HX-Redirect": "/dashboard"})


@router.post("/channel")
async def create_from_channel(
    channel_url: str = Form(...),
    selected_video_ids: list[str] = Form(...),
    output_dir: str = Form(...),
    db: Session = Depends(get_db),
):
    """Spawn parent + N child jobs."""
    ...


@router.delete("/{job_id}")
async def delete_job(job_id: int, db: Session = Depends(get_db)):
    crud.delete_job(db, job_id)
    return {"ok": True}


@router.get("/{job_id}/clips")
async def clips_partial(job_id: int, request: Request, db: Session = Depends(get_db)):
    clips = crud.list_clips(db, job_id)
    return templates.TemplateResponse("partials/clip_grid.html", {
        "request": request, "clips": clips,
    })


@router.get("/{job_id}/manifest")
async def manifest_file(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    manifest_path = Path(job.output_dir) / "manifest.json"
    return FileResponse(manifest_path, media_type="application/json")
```

### 6. `api/routes/channels.py`

```python
@router.post("/preview")
async def preview_channel(
    request: Request,
    channel_url: str = Form(...),
    min_duration_sec: int = Form(0),
    max_duration_sec: int = Form(99999),
    limit: int = Form(50),
):
    from youtok.core.channel import enumerate_channel
    videos = enumerate_channel(channel_url, ChannelFilters(
        min_duration_sec=min_duration_sec,
        max_duration_sec=max_duration_sec,
        limit=limit,
    ))
    return templates.TemplateResponse("partials/channel_preview.html", {
        "request": request, "videos": videos, "channel_url": channel_url,
    })
```

(`core/channel.py` to be implemented in Session 04 — for now this route can return mock data so frontend dev unblocked.)

### 7. `api/ws.py`

```python
import asyncio, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from collections import defaultdict

# in-memory pub/sub
subscribers: dict[int, set[WebSocket]] = defaultdict(set)


def register_ws(app: FastAPI):
    @app.websocket("/ws/jobs/{job_id}")
    async def ws_job(ws: WebSocket, job_id: int):
        await ws.accept()
        subscribers[job_id].add(ws)
        try:
            while True:
                await asyncio.sleep(60)
                await ws.send_text(json.dumps({"ping": True}))
        except WebSocketDisconnect:
            subscribers[job_id].discard(ws)


async def broadcast_progress(job_id: int, payload: dict):
    """Called by background task that polls jobs table for progress changes."""
    dead = set()
    for ws in subscribers[job_id]:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.add(ws)
    subscribers[job_id] -= dead
```

A separate background task (started in `lifespan`) polls jobs table every 1s:

```python
async def progress_watcher():
    last_state = {}  # job_id -> (status, pct)
    while True:
        await asyncio.sleep(1)
        with SessionLocal() as db:
            running = db.query(Job).filter(Job.status.notin_(["done", "failed"])).all()
            for j in running:
                key = (j.status, j.progress_pct)
                if last_state.get(j.id) != key:
                    last_state[j.id] = key
                    await broadcast_progress(j.id, {
                        "step": j.current_step or j.status,
                        "pct": j.progress_pct,
                        "message": j.error_message or "",
                        "status": j.status,
                    })
```

Start in lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(progress_watcher())
    yield
    task.cancel()
```

### 8. Templates — glass-morphism layout

`web/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Youtok{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@2.0.3"></script>
  <link rel="stylesheet" href="https://rsms.me/inter/inter.css">
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            'bg-deep': '#0A0E27',
            'bg-mid': '#151B3D',
            'accent-purple': '#A855F7',
            'accent-pink': '#EC4899',
          },
          fontFamily: { sans: ['Inter', 'system-ui'] },
        },
      },
    };
  </script>
  <style>
    .glass {
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.1);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
    }
    .gradient-btn {
      background: linear-gradient(135deg, #A855F7 0%, #EC4899 100%);
    }
    .gradient-btn:hover { filter: brightness(1.15); }
  </style>
</head>
<body class="bg-bg-deep text-white min-h-screen relative overflow-hidden font-sans">
  <!-- Floating orbs background -->
  <div class="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
    <div class="absolute -top-40 -left-40 w-96 h-96 bg-accent-purple/30 rounded-full blur-3xl"></div>
    <div class="absolute top-1/2 right-0 w-96 h-96 bg-accent-pink/20 rounded-full blur-3xl"></div>
    <div class="absolute -bottom-40 left-1/3 w-96 h-96 bg-accent-purple/20 rounded-full blur-3xl"></div>
  </div>

  {% block nav %}
  {% if show_nav %}
  <nav class="glass mx-6 mt-6 rounded-2xl px-6 py-4 flex items-center justify-between">
    <a href="/dashboard" class="text-2xl font-bold bg-gradient-to-r from-accent-purple to-accent-pink bg-clip-text text-transparent">Youtok</a>
    <div class="flex gap-4">
      <a href="/dashboard" class="hover:text-accent-pink">Dashboard</a>
      <a href="/jobs/new" class="gradient-btn px-4 py-2 rounded-lg font-semibold">+ New Job</a>
    </div>
  </nav>
  {% endif %}
  {% endblock %}

  <main class="container mx-auto px-6 py-8">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

`web/templates/activate.html`:

```html
{% extends "base.html" %}
{% set show_nav = false %}
{% block content %}
<div class="flex items-center justify-center min-h-[80vh]">
  <div class="glass rounded-3xl p-12 w-full max-w-md">
    <h1 class="text-4xl font-bold bg-gradient-to-r from-accent-purple to-accent-pink bg-clip-text text-transparent mb-2">Activate Youtok</h1>
    <p class="text-white/60 mb-8">Enter your license key to bind this machine.</p>
    
    {% if error %}
    <div class="bg-red-500/20 border border-red-500/50 rounded-lg p-3 mb-4 text-red-200 text-sm">{{ error }}</div>
    {% endif %}
    
    <form method="POST" action="/activate/" class="space-y-4">
      <textarea name="license_key" required rows="4" placeholder="YOUTOK-..." 
                class="w-full bg-bg-mid/50 border border-white/10 rounded-lg p-4 text-sm font-mono focus:border-accent-purple outline-none"></textarea>
      <button type="submit" class="gradient-btn w-full py-3 rounded-lg font-semibold">Activate</button>
    </form>
  </div>
</div>
{% endblock %}
```

`web/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% set show_nav = true %}
{% block content %}
<!-- Stats grid -->
<div class="grid grid-cols-4 gap-4 mb-6">
  <div class="glass rounded-2xl p-6">
    <div class="text-white/60 text-sm">Total</div>
    <div class="text-3xl font-bold mt-2">{{ stats.total }}</div>
  </div>
  <div class="glass rounded-2xl p-6">
    <div class="text-white/60 text-sm">Running</div>
    <div class="text-3xl font-bold mt-2 text-accent-pink">{{ stats.running }}</div>
  </div>
  <div class="glass rounded-2xl p-6">
    <div class="text-white/60 text-sm">Done</div>
    <div class="text-3xl font-bold mt-2 text-emerald-400">{{ stats.done }}</div>
  </div>
  <div class="glass rounded-2xl p-6">
    <div class="text-white/60 text-sm">Failed</div>
    <div class="text-3xl font-bold mt-2 text-red-400">{{ stats.failed }}</div>
  </div>
</div>

<!-- Job table -->
<div class="glass rounded-2xl p-6">
  <div class="flex items-center justify-between mb-4">
    <h2 class="text-xl font-bold">Jobs</h2>
    <a href="/jobs/new" class="gradient-btn px-4 py-2 rounded-lg text-sm font-semibold">+ New</a>
  </div>
  <div hx-get="/jobs?partial=1" hx-trigger="every 5s" hx-swap="innerHTML">
    {% include "partials/job_table.html" %}
  </div>
</div>
{% endblock %}
```

`web/templates/partials/job_table.html`:

```html
<table class="w-full text-left">
  <thead>
    <tr class="text-white/60 text-sm border-b border-white/10">
      <th class="py-3">Title</th>
      <th>Status</th>
      <th>Progress</th>
      <th>Clips</th>
      <th>Created</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    <tr class="border-b border-white/5 hover:bg-white/5">
      <td class="py-3"><a href="/jobs/{{ job.id }}" class="hover:text-accent-pink">{{ job.video_title or job.source_url }}</a></td>
      <td><span class="px-2 py-1 rounded text-xs bg-white/10">{{ job.status }}</span></td>
      <td>
        <div class="w-32 h-2 bg-white/10 rounded-full overflow-hidden">
          <div class="h-full gradient-btn" style="width: {{ job.progress_pct }}%"></div>
        </div>
      </td>
      <td>{{ job.clips_count }}</td>
      <td class="text-white/60 text-sm">{{ job.created_at.strftime('%m-%d %H:%M') }}</td>
      <td><a href="/jobs/{{ job.id }}" class="text-accent-pink">View</a></td>
    </tr>
    {% endfor %}
  </tbody>
</table>
```

`web/templates/new_job.html`:

```html
{% extends "base.html" %}
{% set show_nav = true %}
{% block content %}
<div class="glass rounded-3xl p-8 max-w-3xl mx-auto">
  <h1 class="text-3xl font-bold mb-6">New Job</h1>
  
  <!-- Tabs -->
  <div class="flex gap-2 mb-6 border-b border-white/10">
    <button onclick="showTab('single')" class="tab-btn px-4 py-2 active">Single URL</button>
    <button onclick="showTab('bulk')" class="tab-btn px-4 py-2">Bulk (paste many)</button>
    <button onclick="showTab('channel')" class="tab-btn px-4 py-2">Channel</button>
  </div>
  
  <div id="tab-single" class="tab-pane">
    <form method="POST" action="/jobs/" class="space-y-4">
      <input name="source_url" placeholder="https://youtube.com/watch?v=..." required class="w-full glass rounded-lg p-3">
      <input name="output_dir" placeholder="/Users/me/Videos/Cuts" required class="w-full glass rounded-lg p-3">
      <button class="gradient-btn px-6 py-2 rounded-lg font-semibold">Submit</button>
    </form>
  </div>
  
  <div id="tab-bulk" class="tab-pane hidden">
    <form method="POST" action="/jobs/bulk" class="space-y-4">
      <textarea name="urls" rows="10" placeholder="paste URLs, one per line" required class="w-full glass rounded-lg p-3 font-mono text-sm"></textarea>
      <input name="output_dir" placeholder="/Users/me/Videos/Cuts" required class="w-full glass rounded-lg p-3">
      <button class="gradient-btn px-6 py-2 rounded-lg font-semibold">Submit All</button>
    </form>
  </div>
  
  <div id="tab-channel" class="tab-pane hidden">
    <form hx-post="/channels/preview" hx-target="#channel-result" class="space-y-4">
      <input name="channel_url" placeholder="https://youtube.com/@channel" required class="w-full glass rounded-lg p-3">
      <div class="grid grid-cols-3 gap-2">
        <input name="min_duration_sec" type="number" value="300" placeholder="Min sec" class="glass rounded-lg p-3">
        <input name="max_duration_sec" type="number" value="1800" placeholder="Max sec" class="glass rounded-lg p-3">
        <input name="limit" type="number" value="50" placeholder="Limit" class="glass rounded-lg p-3">
      </div>
      <button class="gradient-btn px-6 py-2 rounded-lg font-semibold">Preview</button>
    </form>
    <div id="channel-result" class="mt-6"></div>
  </div>
</div>

<script>
  function showTab(name) {
    document.querySelectorAll('.tab-pane').forEach(e => e.classList.add('hidden'));
    document.getElementById('tab-' + name).classList.remove('hidden');
    document.querySelectorAll('.tab-btn').forEach(e => e.classList.remove('active', 'text-accent-pink', 'border-b-2', 'border-accent-pink'));
    event.target.classList.add('active', 'text-accent-pink', 'border-b-2', 'border-accent-pink');
  }
</script>
{% endblock %}
```

`web/templates/job_detail.html`:

```html
{% extends "base.html" %}
{% set show_nav = true %}
{% block content %}
<div class="grid grid-cols-3 gap-6">
  <!-- Left: progress -->
  <div class="col-span-1 glass rounded-2xl p-6">
    <h2 class="text-xl font-bold mb-2">{{ job.video_title or job.source_url }}</h2>
    <div class="text-white/60 text-sm mb-4">{{ job.status }}</div>
    <div class="w-full h-3 bg-white/10 rounded-full overflow-hidden">
      <div id="progress-bar" class="h-full gradient-btn transition-all" style="width: {{ job.progress_pct }}%"></div>
    </div>
    <div id="progress-step" class="text-sm mt-2 text-accent-pink">{{ job.current_step or '—' }}</div>
    <div id="progress-message" class="text-sm text-white/60 mt-1"></div>
    
    {% if job.status == 'failed' %}
    <div class="bg-red-500/20 border border-red-500/50 rounded-lg p-3 mt-4 text-red-200 text-sm">{{ job.error_message }}</div>
    {% endif %}
  </div>
  
  <!-- Right: clips -->
  <div class="col-span-2 glass rounded-2xl p-6">
    <h2 class="text-xl font-bold mb-4">Clips ({{ clips|length }})</h2>
    <div id="clip-grid" hx-get="/jobs/{{ job.id }}/clips" hx-trigger="load, every 3s" hx-swap="innerHTML">
      <div class="text-white/60 text-sm">Loading...</div>
    </div>
  </div>
</div>

<script>
const ws = new WebSocket(`ws://${location.host}/ws/jobs/{{ job.id }}`);
ws.onmessage = (ev) => {
  const data = JSON.parse(ev.data);
  if (data.ping) return;
  document.getElementById('progress-bar').style.width = data.pct + '%';
  document.getElementById('progress-step').textContent = data.step;
  document.getElementById('progress-message').textContent = data.message;
  if (data.status === 'done' || data.status === 'failed') {
    setTimeout(() => location.reload(), 1500);
  }
};
</script>
{% endblock %}
```

`web/templates/partials/clip_grid.html`:

```html
<div class="grid grid-cols-2 gap-4">
  {% for clip in clips %}
  <div class="glass rounded-xl p-4">
    <div class="text-xs text-accent-pink mb-1">Part {{ clip.part_number }}/{{ clip.total_parts }}</div>
    <div class="font-semibold mb-1">{{ clip.topic_name }}</div>
    <div class="text-xs text-white/60">{{ "%.1f"|format(clip.duration_sec) }}s · coherence {{ "%.1f"|format(clip.coherence_score) }}/5</div>
    <div class="text-xs text-white/40 mt-2 font-mono break-all">{{ clip.output_path }}</div>
  </div>
  {% else %}
  <div class="text-white/60 text-sm col-span-2">No clips yet — pipeline still running.</div>
  {% endfor %}
</div>
```

### 9. Run script

`scripts/run-server.sh`:

```bash
#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
uv run uvicorn youtok.api.main:app --host 127.0.0.1 --port 8000 --reload
```

`scripts/run-server.ps1`:

```powershell
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING="utf-8"
uv run uvicorn youtok.api.main:app --host 127.0.0.1 --port 8000 --reload
```

## Acceptance test

```bash
# Server only — Session 04 worker not yet built
./scripts/run-server.sh
# Visit http://localhost:8000
```

Verify:
1. Visiting `/` redirects to `/activate` (license not present).
2. `/activate` page renders with glass card, Inter font, floating orbs background.
3. Submitting an invalid license key shows error message.
4. Submitting a valid license key (test with one generated via `keygen.py` from Session 01) → redirects to `/dashboard`.
5. Dashboard renders with stats cards (zeros initially), empty job table.
6. New Job page renders 3 tabs, switching tabs works.
7. Submitting a job (without worker running, just to test API) → redirects to `/jobs/{id}` showing pending status.
8. WebSocket connects on job detail page (check browser DevTools Network tab → WS).

## Anti-patterns to avoid

- Building React/Vue — keep server-rendered + HTMX.
- Using inline `<style>` for full pages — use Tailwind utility classes.
- Polling jobs API faster than 5s in HTMX — DB load.
- Forgetting `hx-swap` on job table refresh — page jumps.
- Embedding the license key in localStorage — only `data/license.json` is source of truth.
- Building the activate POST without checking if already activated — should redirect to dashboard if so.

## When done

Notify integration session (05). Provide:
- URL of running server
- Screenshot of dashboard with at least 1 mock job

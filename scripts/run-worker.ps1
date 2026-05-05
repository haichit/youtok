Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path data\logs | Out-Null

# Resume on crash: mark stuck jobs as failed
uv run python -c @"
from datetime import datetime, timedelta
from youtok.db.base import SessionLocal
from youtok.db.models import Job
with SessionLocal() as db:
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    stuck = db.query(Job).filter(
        Job.status.notin_(['done', 'failed', 'pending']),
        Job.started_at != None,
        Job.started_at < cutoff,
    ).all()
    for j in stuck:
        j.status = 'failed'
        j.error_message = 'Worker crashed mid-job; marked failed on restart'
        j.finished_at = datetime.utcnow()
    db.commit()
    if stuck:
        print(f'[crash-recovery] marked {len(stuck)} stuck job(s) as failed')
"@

uv run huey_consumer youtok.queue.huey_app.huey `
    --workers 1 `
    --logfile data\logs\worker.log `
    --verbose

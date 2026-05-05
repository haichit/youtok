#!/usr/bin/env bash
# Test script: inserts a job via DB, then polls until worker completes it.
# Prerequisites: run-worker.sh must be running in another terminal with YOUTOK_MOCK_PIPELINE=1
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONIOENCODING=utf-8

echo "=== Acceptance Test: Worker picks up job ==="
echo ""

# Insert a test job and get its ID
JOB_ID=$(uv run python -c "
from youtok.db.base import SessionLocal, Base, engine
from youtok.db.models import Job, License
from datetime import datetime

# Ensure tables exist
Base.metadata.create_all(engine)

with SessionLocal() as db:
    # Ensure a license exists (required FK)
    lic = db.query(License).first()
    if not lic:
        lic = License(
            key_hash='test-hash-000',
            email='test@test.com',
            machine_id='test-machine',
            activated_at=datetime.utcnow(),
            status='active',
        )
        db.add(lic)
        db.commit()
        db.refresh(lic)

    job = Job(
        license_id=lic.id,
        source_type='video',
        source_url='https://youtube.com/watch?v=TEST123',
        output_dir='/tmp/youtok-test-output',
        status='pending',
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    print(job.id)
")

echo "Created job ID: $JOB_ID"
echo "Waiting for worker to pick it up (timeout 30s)..."
echo ""

# Enqueue the job via Huey
uv run python -c "
from youtok.queue.tasks import process_job
process_job(${JOB_ID})
print('Task enqueued')
"

# Poll job status
for i in $(seq 1 30); do
    STATUS=$(uv run python -c "
from youtok.db.base import SessionLocal
from youtok.db.models import Job
with SessionLocal() as db:
    job = db.get(Job, ${JOB_ID})
    print(f'{job.status}|{job.progress_pct}|{job.current_step or \"-\"}')
")
    echo "  [$i s] $STATUS"

    # Check if terminal state
    if echo "$STATUS" | grep -q "^done\|^failed"; then
        echo ""
        echo "=== RESULT ==="
        echo "$STATUS"
        if echo "$STATUS" | grep -q "^done"; then
            echo "✓ Job completed successfully!"
        else
            echo "✗ Job failed."
            exit 1
        fi
        exit 0
    fi
    sleep 1
done

echo "✗ Timeout — job did not complete in 30s"
exit 1

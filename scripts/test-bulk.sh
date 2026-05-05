#!/usr/bin/env bash
# Test script: bulk submit 3 URLs → 3 Job rows → worker processes serially.
# Prerequisites: run-worker.sh must be running with YOUTOK_MOCK_PIPELINE=1
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONIOENCODING=utf-8

echo "=== Acceptance Test: Bulk submit 3 URLs ==="
echo ""

JOB_IDS=$(uv run python -c "
from youtok.db.base import SessionLocal, Base, engine
from youtok.db.models import Job, License
from youtok.queue.tasks import process_job
from datetime import datetime

Base.metadata.create_all(engine)

urls = [
    'https://youtube.com/watch?v=BULK_A',
    'https://youtube.com/watch?v=BULK_B',
    'https://youtube.com/watch?v=BULK_C',
]

with SessionLocal() as db:
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

    ids = []
    for url in urls:
        job = Job(
            license_id=lic.id,
            source_type='video',
            source_url=url,
            output_dir='/tmp/youtok-test-bulk',
            status='pending',
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        ids.append(job.id)

    # Enqueue all
    for jid in ids:
        process_job(jid)

    print(','.join(str(i) for i in ids))
")

echo "Created jobs: $JOB_IDS"
echo "Waiting for all 3 to complete (timeout 60s, mock = ~11s each serial)..."
echo ""

IFS=',' read -ra IDS <<< "$JOB_IDS"

for i in $(seq 1 60); do
    ALL_DONE=true
    LINE=""
    for JID in "${IDS[@]}"; do
        STATUS=$(uv run python -c "
from youtok.db.base import SessionLocal
from youtok.db.models import Job
with SessionLocal() as db:
    j = db.get(Job, ${JID})
    print(f'{j.status}:{j.progress_pct}%')
")
        LINE="$LINE  job$JID=$STATUS"
        if ! echo "$STATUS" | grep -q "^done\|^failed"; then
            ALL_DONE=false
        fi
    done
    echo "  [$i s]$LINE"

    if $ALL_DONE; then
        echo ""
        echo "=== ALL 3 JOBS COMPLETE ==="
        echo "✓ Bulk submit test passed!"
        exit 0
    fi
    sleep 1
done

echo "✗ Timeout — not all jobs completed in 60s"
exit 1

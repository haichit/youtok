#!/usr/bin/env bash
# Start Huey worker with hard ceiling = 10 threads.
# Actual concurrent jobs is gated by the `concurrent_jobs` setting (1..10) in the UI.
# Editing the UI setting takes effect immediately for queued jobs (no need to restart).

set -e
cd "$(dirname "$0")/.."

mkdir -p data/logs

WORKERS=${WORKERS:-10}
echo "Starting Huey worker with $WORKERS thread ceiling."
echo "Adjust concurrent jobs in /settings (default: 1)."

exec uv run huey_consumer youtok.queue.huey_app.huey --workers "$WORKERS"

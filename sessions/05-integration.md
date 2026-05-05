# Session 05 — Integration

> Run **after Sessions 02, 03, 04 all done.**

## Goal

Wire the three independent modules together, run an actual end-to-end test on both Mac and Windows, fix integration bugs.

## Read first

- All of `../SPEC.md`
- Output reports from Sessions 02, 03, 04 (test results, sample manifests)
- Logs from any failures

## Tasks

### 1. Replace pipeline mocks with real implementation

If Session 03 used a mocked `run_pipeline` (just `time.sleep` + progress emits) to develop UI without 02, replace it with the real one from Session 02.

### 2. Verify shared models

Check that `core.pipeline.ClipPlan` (Session 02) maps cleanly to `db.models.Clip` (Session 01) when written. Specifically:

```python
# in pipeline.run_pipeline, after rendering each clip:
db.add(Clip(
    job_id=job_id,
    part_number=i,
    total_parts=len(plan),
    topic_name=clip.topic_name,
    parent_topic=clip.parent_topic,
    start_sec=clip.start_sec,
    end_sec=clip.end_sec,
    duration_sec=clip.duration_sec,
    output_path=str(clip_path),
    coherence_score=clip.coherence_score,
    warnings_json=json.dumps(clip.warnings),
    transcript_text=collect_text(clip.start_sec, clip.end_sec, transcript),
    sentence_range_start=clip.sentence_range_start,
    sentence_range_end=clip.sentence_range_end,
))
```

### 3. WebSocket progress integration

Confirm: when worker (`queue/tasks.py`) calls `progress_callback` → updates `Job.progress_pct/current_step` in DB → server's `progress_watcher` task picks up the change → `broadcast_progress` to all WebSocket subscribers.

Tune polling interval if needed. 1s default is fine; bump to 2s if it's too noisy in logs.

### 4. End-to-end test on Mac

```bash
cd projects/youtok
./scripts/install-mac.sh
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY=sk-ant-...

# Generate a test license (admin private_key.pem assumed elsewhere)
python scripts/gen-license.py \
  --private-key ~/keys/youtok-private.pem \
  --email test@hai.local \
  --expires 2026-12-31 \
  --max-jobs-per-day 50

# Output: YOUTOK-XXX...

# Start both processes
./scripts/run-server.sh    # terminal 1
./scripts/run-worker.sh    # terminal 2

# Browser: http://localhost:8000 → activate with the key from above
# Submit a 3-minute test video
```

Test video suggestion: a Khan Academy 5-min explainer (sufficient structure, English, free).

Watch:
- `data/logs/worker.log` for pipeline progress
- Dashboard auto-refreshes
- Job detail WebSocket shows live updates

Checks after job completes:
- Output folder contains: `clips/`, `manifest.json`, `transcript.json`, `topic-tree.json`
- `data/workdir/job-{id}/` should be empty (source.mp4, source.wav cleaned)
- Each clip plays in QuickTime
- Each clip is 1080×1920
- Subtitles show word-by-word highlight in yellow
- Title appears at top with "Part X/Y"

### 5. End-to-end test on Windows

Copy project folder to a Windows machine. Run `install-win.ps1`. Same flow as Mac.

Common Windows issues to verify:
- Long file paths (>260 chars) — enable LongPathsEnabled in registry or use UNC `\\?\C:\...`
- Slashes in paths — must be all `pathlib.Path`
- `PYTHONIOENCODING=utf-8` — verify in run scripts
- Antivirus blocking yt-dlp.exe / ffmpeg.exe — exclude `assets/bin/win/` if needed
- Console emoji — `loguru` may need `colorize=False` if Windows terminal doesn't support ANSI

### 6. Channel test

Channel URL: pick a small educational channel (e.g. CGP Grey). Limit to 3 videos, all <30 minutes.

```
New Job → Channel → paste URL → Preview → check 3 → Create 3 jobs
```

Verify:
- 3 child jobs + 1 parent in DB
- Worker processes them sequentially (one at a time)
- Parent aggregate progress reflects child completion

### 7. License lock test

```bash
# Activate on Mac
# Submit a job, wait for done
# Stop both processes
# Copy entire project folder to another Mac
# Start server on second Mac
```

Expected: redirects to /activate. The key is bound to first Mac's machine_id. Pasting the same key on second Mac fails with "License bound to different machine".

### 8. Failure-mode tests

a. **Invalid YouTube URL**: submit `https://youtube.com/invalid` → job fails, error message shown in UI, not silent.

b. **Network drop during download**: pull network mid-download → job fails with retry suggestion.

c. **LLM rate limit**: submit 10 jobs rapid-fire → at most 1 in-flight (worker single), others pending → no rate limit error from Anthropic since serial.

d. **Disk full**: artificially restrict disk space → pre-flight check should refuse before download.

e. **Empty license file**: `rm data/license.json` → next request → redirects to /activate.

### 9. Bug-fix patterns expected

Likely bugs to find:
- ASS subtitle file path with spaces — wrap in `'` for ffmpeg's `subtitles=...`
- ffmpeg drawtext text containing `:` or `'` — escape properly
- WhisperX punctuation re-tokenization — words may have trailing `.` mid-sentence
- Race condition: worker writes Job.progress_pct while server reads → use SQLite WAL (already enabled in Session 01)
- WebSocket disconnect when laptop sleeps → frontend should auto-reconnect

### 10. Update README + SPEC

After integration, update `README.md` with:
- Actual install times observed
- Known limitations (e.g. "GPU recommended for videos > 10 min")
- Troubleshooting section

Update `SPEC.md` if any architectural decisions were revised during integration.

## Deliverables

1. Successful end-to-end run on Mac (screenshot of done job + clips folder)
2. Successful end-to-end run on Windows
3. Successful channel batch (3 videos)
4. License lock verification
5. Updated README with troubleshooting section
6. List of bugs found + fixed in `data/integration-notes.md`

## Acceptance criteria

- All 6 deliverables checked off
- Both OS pass E2E test
- No critical bugs left open
- Logs show no unhandled exceptions during the test runs

## Anti-patterns to avoid

- "Works on my machine" — must verify on the OS it's not running on right now (test machine for Windows if you're on Mac).
- Skipping the disk-full pre-flight check — easy to forget, painful to debug.
- Patching bugs in the wrong session's files — write a note in integration-notes.md if a bug was found in 02/03/04, but fix in the right module.
- Pinning to a specific test video URL that may go down later — pick one or two stable channels.

## When done

Notify polish session (06). Provide integration-notes.md.

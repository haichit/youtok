# Youtok — Full Spec

> **For Claude Code.** This is the full build spec. Read top-to-bottom before starting any session.

## 0. Project identity

- **Name:** Youtok
- **Owner:** Hai Phan (phanhai.work@gmail.com)
- **Repo:** `/Users/phanthanhhai/Desktop/Hai brain's/projects/youtok/`
- **Wiki:** `wiki/sources/projects/youtok.md`
- **Tech stack:** Python 3.11, FastAPI, Jinja2 + HTMX + Tailwind CDN, SQLite, Huey, WhisperX, yt-dlp, ffmpeg, PySceneDetect, Anthropic SDK
- **Distribution:** Local-only. Web UI on `http://localhost:8000`. License key locked per machine.

## 1. Goal

Auto-cut YouTube videos (educational, ~15 min) into 9:16 short-form clips suitable for TikTok / Reels / Shorts. Output:

- Each clip ≥60s, ≤240s.
- Each clip is one self-contained sub-topic — never half-topic A + half-topic B.
- 9:16 layout 1080×1920 with title overlay (top) and word-highlight subtitle (bottom).
- Cut points snap to pause + shot boundary (no mid-sentence audio cut, no mid-shot visual cut).
- Industrial reliability — runs unattended, no manual review needed.

## 2. Non-goals

- Not a video downloader.
- Not a publisher (no auto-post to TikTok / FB / YT).
- Not multi-language at MVP — English source only.
- Not a SaaS — single-machine, license-locked.
- No GUI desktop app — browser UI on localhost.

## 3. Tech stack rationale

| Choice | Why |
|---|---|
| **Python full-stack** | Single language; ML libs (WhisperX) have best Python support. |
| **FastAPI + Jinja2 + HTMX** | Server-rendered, no React build, glass-morphism CSS via Tailwind CDN. |
| **SQLite + SQLAlchemy** | Single file, zero-config, perfect for local-only tool. |
| **Huey + SQLite backend** | Background queue without Redis. Windows-friendly. |
| **WhisperX** | Word-level timestamps via forced alignment. Critical for word-highlight subs. |
| **PySceneDetect** | De-facto standard shot boundary detection. |
| **Claude Sonnet 4.6** | Best at structured analysis of long transcripts. JSON tool-use reliable. |
| **License key (offline RSA)** | No license server needed. CLI-only generation for MVP. |

## 4. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Browser (localhost:8000)                                │
│  - Activate page (license entry)                         │
│  - Dashboard (job table + progress)                      │
│  - New Job page (single / bulk / channel)                │
│  - Job Detail (live progress + clips grid)               │
└──────────────┬──────────────────────────────────────────┘
               │ HTTP + WebSocket (live progress)
               ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI server (uvicorn, port 8000)                     │
│  - Routes: /, /activate, /jobs, /channels, /ws           │
│  - License middleware (every request except /activate)   │
│  - Static + templates                                    │
└──────────────┬──────────────────────────────────────────┘
               │
       ┌───────┴────────────────┐
       ▼                        ▼
   SQLite (data/app.db)    Huey worker (separate process)
   - licenses              ├─ poll jobs table
   - jobs                  ├─ run pipeline
   - clips                 └─ update progress
                                     │
                                     ▼
                              Pipeline (src/youtok/core/)
                              ├─ downloader.py (yt-dlp)
                              ├─ transcriber.py (WhisperX)
                              ├─ segmenter.py (Claude API)
                              ├─ snapper.py (PySceneDetect)
                              ├─ compositor.py (ffmpeg)
                              └─ pipeline.py (orchestrator)
```

Two processes:
1. **Server** — FastAPI/uvicorn. UI + API + WebSocket.
2. **Worker** — Huey consumer. Picks jobs from DB, runs pipeline, updates progress.

Both processes share `data/app.db` (SQLite WAL mode). Server emits WebSocket from a Job watcher that polls DB for progress changes (or uses LISTEN/NOTIFY-style polling).

## 5. Folder structure

```
projects/youtok/
├── README.md
├── SPEC.md                               # this file
├── sessions/                             # session prompts for Claude Code
│   ├── 01-foundation.md
│   ├── 02-pipeline.md
│   ├── 03-web-ui.md
│   ├── 04-worker.md
│   ├── 05-integration.md
│   └── 06-polish.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── alembic.ini
├── alembic/
│   └── versions/
├── assets/
│   ├── fonts/
│   │   └── Inter-Bold.ttf                # bundled font, no system dep
│   ├── bin/
│   │   ├── mac/                          # ffmpeg, ffprobe, yt-dlp (mac arm64)
│   │   └── win/                          # ffmpeg.exe, ffprobe.exe, yt-dlp.exe
│   └── keys/
│       └── public_key.pem                # for license verification (embedded)
├── data/                                 # gitignored
│   ├── app.db
│   ├── queue.db                          # huey backend
│   ├── license.json                      # activation cache
│   └── workdir/                          # transient: source.mp4, wav
├── src/
│   └── youtok/
│       ├── __init__.py
│       ├── config.py                     # pydantic-settings
│       ├── core/
│       │   ├── __init__.py
│       │   ├── downloader.py
│       │   ├── transcriber.py
│       │   ├── segmenter.py
│       │   ├── snapper.py
│       │   ├── compositor.py
│       │   ├── pipeline.py
│       │   └── slug.py
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── client.py                 # anthropic SDK wrapper
│       │   ├── prompts.py                # Stage A, B, with 15 rules
│       │   └── schemas.py                # pydantic models for LLM I/O
│       ├── license/
│       │   ├── __init__.py
│       │   ├── manager.py                # verify, activate
│       │   ├── machine_id.py             # cross-platform HWID
│       │   └── keygen.py                 # admin CLI (private)
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py                   # engine + session
│       │   ├── models.py                 # License, Job, Clip
│       │   └── crud.py
│       ├── queue/
│       │   ├── __init__.py
│       │   ├── huey_app.py
│       │   └── tasks.py                  # @huey.task process_job
│       ├── api/
│       │   ├── __init__.py
│       │   ├── main.py                   # FastAPI factory
│       │   ├── deps.py                   # license guard
│       │   ├── routes/
│       │   │   ├── activate.py
│       │   │   ├── jobs.py
│       │   │   ├── channels.py
│       │   │   └── pages.py              # HTML routes (Jinja)
│       │   └── ws.py                     # WebSocket /ws/jobs/{id}
│       ├── web/
│       │   ├── templates/
│       │   │   ├── base.html             # glass-morphism layout
│       │   │   ├── activate.html
│       │   │   ├── dashboard.html
│       │   │   ├── new_job.html
│       │   │   ├── job_detail.html
│       │   │   └── partials/
│       │   │       ├── job_row.html
│       │   │       ├── progress_bar.html
│       │   │       └── clip_card.html
│       │   └── static/
│       │       ├── tailwind.css          # CDN copy or compiled
│       │       └── htmx.min.js
│       └── cli.py                        # debug entry: download/transcribe/run
├── scripts/
│   ├── install-mac.sh
│   ├── install-win.ps1
│   ├── run-server.sh
│   ├── run-server.ps1
│   ├── run-worker.sh
│   ├── run-worker.ps1
│   ├── gen-license.py                    # admin tool wrapper
│   └── reset-license.py                  # admin: clear data/license.json
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── sample-3min.mp4
    │   └── sample-transcript.json
    ├── test_segmenter.py
    ├── test_snapper.py
    ├── test_license.py
    └── test_e2e.py
```

## 6. Database schema (SQLite)

```python
# src/youtok/db/models.py

class License(Base):
    __tablename__ = "licenses"
    id: int                       # PK
    key_hash: str                 # SHA256 of full key
    email: str
    machine_id: str               # bound HWID
    activated_at: datetime
    expires_at: datetime | None   # nullable = perpetual
    max_jobs_per_day: int | None  # nullable = unlimited
    features_json: str            # future-proof flags
    status: str                   # 'active' | 'expired'
    created_at: datetime

class Job(Base):
    __tablename__ = "jobs"
    id: int                       # PK
    license_id: int               # FK
    parent_job_id: int | None     # FK self, channel job spawns child video jobs
    source_type: str              # 'video' | 'channel' | 'playlist' | 'bulk'
    source_url: str
    output_dir: str               # absolute path on local FS
    status: str                   # see status enum
    progress_pct: int             # 0-100
    current_step: str | None      # 'downloading' | 'transcribing' | ...
    config_json: str              # JobConfig serialized
    error_message: str | None
    video_title: str | None
    video_duration_sec: float | None
    clips_count: int              # default 0
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

class Clip(Base):
    __tablename__ = "clips"
    id: int                       # PK
    job_id: int                   # FK
    part_number: int              # 1-indexed
    total_parts: int
    topic_name: str
    parent_topic: str | None
    start_sec: float
    end_sec: float
    duration_sec: float
    output_path: str              # absolute mp4 path
    coherence_score: float        # 0-5 from Stage B
    warnings_json: str            # rules that failed
    transcript_text: str          # full text of clip
    sentence_range_start: str     # 'S001'
    sentence_range_end: str       # 'S023'
```

Job status enum:

```
pending → downloading → transcribing → segmenting → snapping
       → cutting → done | failed
```

## 7. Pipeline detail

### 7.1 Download (`core/downloader.py`)

```python
def download_video(url: str, work_dir: Path) -> DownloadResult:
    """
    Returns: {video_path, audio_path, title, video_id, duration_sec, channel_name}
    Uses yt-dlp binary from assets/bin/{platform}/.
    Format: best mp4 ≤1080p (don't download 4K — waste).
    Audio: 16kHz mono wav for WhisperX.
    """
```

CLI: `yt-dlp -f "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]" -o "%(id)s.%(ext)s" --merge-output-format mp4 <url>`

Then: `ffmpeg -i source.mp4 -ar 16000 -ac 1 -vn source.wav`

Pre-check: free disk space ≥ 5GB; abort if less.

### 7.2 Transcribe (`core/transcriber.py`)

```python
def transcribe(audio_path: Path, language: str = "en", device: str = "auto") -> Transcript:
    """
    device: 'auto' detects CUDA → cuda, else cpu.
    Loads faster-whisper model (large-v3 if GPU; base if CPU).
    Then runs WhisperX alignment for word-level timestamps.
    Returns Transcript with sentence segmentation.
    """
```

Sentence segmentation:
- Use `nltk.sent_tokenize(text)` after concatenating word texts with their punctuation.
- For each sentence, find `start = first word start`, `end = last word end`.
- Assign IDs `S001, S002, ..., S{N:03d}`.

Output schema (`Transcript`):

```python
class WordToken(BaseModel):
    word: str
    start: float
    end: float

class Sentence(BaseModel):
    id: str            # 'S001'
    text: str
    start: float
    end: float
    words: list[WordToken]

class Transcript(BaseModel):
    language: str
    duration_sec: float
    sentences: list[Sentence]
```

### 7.3 Topic segmentation (`core/segmenter.py` + `llm/`)

The hard part. Three stages.

#### Stage A — Outline (1 LLM call)

Input: full sentence-numbered transcript + video title.

Output schema:

```python
class StageAOutput(BaseModel):
    main_topic: str
    intro_strip: SentenceRange | None  # to discard
    outro_strip: SentenceRange | None
    sub_topics: list[SubTopic]

class SubTopic(BaseModel):
    name: str
    start_sentence: str   # must exist in transcript
    end_sentence: str
    parent: str | None
    children: list["SubTopic"] = []
```

Use Anthropic tool-use to force structured output. Validate with pydantic; reject + retry once if sentence IDs don't exist.

#### Stage B — Validation (parallel LLM calls)

For each leaf sub-topic from Stage A, call LLM with:
- Just that sub-topic's text (not full transcript).
- Ask: coherence_score 1-5, start_adjust (sentences), end_adjust, internal_break (if any).

Output:

```python
class StageBOutput(BaseModel):
    coherence_score: int  # 1-5
    start_adjust: int     # signed, sentences to shift start
    end_adjust: int
    internal_break: SentenceRange | None  # if mid-topic shifts to new topic
    notes: str
```

Apply adjustments. If `internal_break` returned → split sub-topic into 2 (auto, since user opted in for full automation).

#### Stage C — Length normalize (deterministic Python, no LLM)

Flatten leaf sub-topics. Iterate:
- If sub-topic duration ≥ 60s → push to clips list.
- If < 60s → buffer; merge with NEXT sub-topic that has SAME `parent`.
- Never merge across different parents (preserves topic hierarchy).
- Hard cap: if a clip > 240s → flag warning, do not auto-split (Stage B already validated coherence).

Edge case: if last sub-topic < 60s and no next sibling → merge backward into previous clip (mark warning).

### 7.4 Snap cut points (`core/snapper.py`)

For each `(start_sec, end_sec)` in clips:

1. **Pause boundary** — find `(words[i].end, words[i+1].start)` gap ≥ 0.3s within ±2s of original cut. Move cut to middle of pause.
2. **Shot boundary** — run PySceneDetect once on whole video; cache result. Find shot change within ±2s of (already pause-snapped) cut. Move cut if found.

Priority: pause > shot. If conflict, pause wins (audio cut hurts more than visual cut).

```python
def snap_cuts(
    clips: list[Clip],
    transcript: Transcript,
    video_path: Path,
    pause_threshold: float = 0.3,
    snap_window: float = 2.0,
) -> list[SnappedClip]:
    ...
```

### 7.5 Compose (`core/compositor.py`)

Generate `.ass` per clip with **word-highlight (CapCut-style)** subtitles.

```python
def generate_ass(words: list[WordToken], config: SubConfig, clip_start: float) -> str:
    """
    Returns ASS file content.
    Each word becomes a Dialogue line with karaoke-style timing.
    Active word: yellow (#FFEB3B), others: white.
    Outline: 4px black.
    Position: bottom of black bar (Alignment=2 + MarginV=200).
    Words timestamps shifted: word.start - clip_start.
    """
```

ASS format example for word-by-word highlight:

```
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.40,Default,,0,0,200,,{\\c&HFFFFFF&}word1{\\c&H35EBFF&\\b1}word2{\\c&HFFFFFF&\\b0} word3
```

Implementation pattern: for each chunk of 5 words, render the chunk N times (once per word being active). Or simpler: per-word fade-in highlight using `\t` transition.

**Simpler reliable approach (recommended):**
Build ASS with one Dialogue per word, fully overlapping:
- Word N visible from `start_N` to `end_N` in yellow
- Other words in chunk visible in white throughout chunk

Or use libass features. Use `pysubs2` lib + manual override tags.

Render command:

```python
def render_clip(
    source_mp4: Path,
    start: float,
    end: float,
    ass_path: Path,
    title_text: str,
    output_path: Path,
    config: RenderConfig,
):
    """
    ffmpeg filter chain:
      -ss start -to end (input or output level)
      scale=1080:608
      pad=1080:1920:0:360:black
      drawtext (top — title)
      ass=clip.ass (bottom — word highlight)
    Output: 1080x1920, H.264, AAC, MP4 faststart.
    """
```

Filter chain:

```
[0:v]scale=1080:608,pad=1080:1920:0:360:black[v1];
[v1]drawtext=fontfile=Inter-Bold.ttf:text='{title}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=120:line_spacing=10[v2];
[v2]ass={ass_path}[vout]
```

For multi-line title (>30 chars wraps), pre-compute line breaks in Python and emit multiple `drawtext` filters with `y=120,200,...`.

Re-encode (don't stream-copy) so cut points are frame-accurate:
- `-c:v libx264 -preset medium -crf 18`
- `-c:a aac -b:a 128k`
- `-movflags +faststart`

### 7.6 Pipeline orchestrator (`core/pipeline.py`)

```python
def run_pipeline(
    job_id: int,
    progress_callback: Callable[[str, int, str], None],
):
    """
    Loads job from DB, runs all stages, updates DB.
    progress_callback(step: str, pct: int, message: str)
    Steps emit pct: download 0-15, transcribe 15-40, segment 40-60,
                  snap 60-65, render 65-95, cleanup 95-100.
    """
```

Self-check (auto, no user input):

After Stage C + snap, verify:
1. All clips ≥ 60s (fail → flag)
2. All sentences[start].text first-word not in {But, And, So, Then, However, Therefore, Which, That} (fail → shift back 1 sentence)
3. Coverage ≥ 95% of (duration - intro_strip - outro_strip)
4. Avg coherence_score ≥ 4.0

If any fail: re-run Stage A with feedback in prompt, max 2 retries. After 2 retries still fail: proceed but add warning to manifest.

### 7.7 Cleanup

After all clips rendered, delete `workdir/{job_id}/source.mp4` and `source.wav`. Keep `transcript.json` + `topic-tree.json` + `manifest.json` in output_dir.

## 8. The 15 segmentation rules (in LLM prompts)

Embed verbatim in Stage A and Stage B prompts.

### Group A — Detect boundaries

**R1. Tutorial markers as boundary signals.**
Sentences containing these patterns → prefer as **opening** of a new sub-topic:
`"Let's start with..."`, `"First..."`, `"Now let's look at..."`, `"Moving on to..."`, `"Another..."`, `"Next..."`, `"Finally..."`, `"Before we...", "But first..."`, `"Step 1/2/3..."`

**R2. Conclusion markers.**
Sentences containing these patterns → prefer as **closing** of a sub-topic:
`"So that's how X works"`, `"In summary"`, `"To recap"`, `"That's the basic idea"`, `"And that's it for..."`

**R3. Long pauses are boundary candidates.**
Pauses ≥ 1.0s flagged as candidates; ≥ 2.0s strong signal.

### Group B — Don't break wrong things

**R4. Q→A integrity.** Question + answer is one atomic unit.
**R5. Definition→Explanation integrity.** "X is Y. It works by..." is atomic.
**R6. Numbered sequence integrity.** "There are 3 components: First/Second/Third..." stays in one sub-topic.
**R7. Example chains.** "For example...", "Let me show you...", "Imagine..." continues previous sentence.
**R8. Visual references.** "As you can see...", "Watch this..." → keep with whatever the visual is showing.

### Group C — Clean opening / closing of clips

**R9. First-sentence rule.** Clip's first sentence cannot start with continuation words: `But, And, So, Then, However, Therefore, Which, That`. Else shift boundary back 1 sentence.
**R10. Last-sentence rule.** Clip's last sentence ends with `.` or `!` (not `,`). Not a dependent clause.
**R11. Anti-cliffhanger.** Last sentence cannot promise next content: "But there's another problem..." Shift forward.
**R12. Hook preservation.** Educational hooks ("But how does X really work?") must stay inside the clip, not at boundary.

### Group D — Sanity check

**R13. Title hint.** Use video title to anchor main topic outline.
**R14. Length distribution.** All clips same ±5s = over-merge, alert. Single clip > 4 min = under-split, suggest split.
**R15. Coverage.** Total clip duration ≈ video duration minus intro/outro strip. Mismatch > 5% → flag.

## 9. LLM prompts

### 9.1 Stage A prompt

```
SYSTEM:
You are an expert video editor analyzing transcripts of educational
"explainer" videos (how-things-work content). Your job: produce a
hierarchical topic tree where each leaf sub-topic is a self-contained
unit suitable for a 60-180 second short-form video clip.

USER:
Video title: "{title}"
Total sentences: {n}
Total duration: {duration_sec} sec

Transcript (sentence-numbered):
S001: ...
S002: ...
...
S{N:03d}: ...

Apply these 15 rules strictly:

[R1-R15 inline, full text from §8]

Use the `submit_topic_tree` tool to return your analysis.
The tool's schema enforces sentence IDs must exist in the transcript.
```

Anthropic tool definition for `submit_topic_tree`:

```python
{
    "name": "submit_topic_tree",
    "description": "Submit the analyzed topic tree.",
    "input_schema": {
        "type": "object",
        "properties": {
            "main_topic": {"type": "string"},
            "intro_strip": {
                "type": "object",
                "nullable": True,
                "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            },
            "outro_strip": {...},
            "sub_topics": {
                "type": "array",
                "items": {"$ref": "#/$defs/subTopic"},
            },
        },
        "$defs": {
            "subTopic": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "start_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                    "end_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                    "parent": {"type": "string", "nullable": True},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/subTopic"}},
                },
                "required": ["name", "start_sentence", "end_sentence"],
            }
        },
        "required": ["main_topic", "sub_topics"],
    },
}
```

### 9.2 Stage B prompt

```
SYSTEM: You verify whether a transcript segment is a complete,
self-contained sub-topic suitable for a short clip.

USER:
Sub-topic claimed name: "{name}"
Parent topic: "{parent}"

Segment:
S{start}: ...
...
S{end}: ...

Rules to verify (15 rules from §8 inline).

Use the `submit_validation` tool.
```

### 9.3 Retry prompt (Stage A v2)

If self-check fails:

```
Your previous attempt had these issues:
- {issue 1: e.g. "Clip 3 starts with 'But' (R9 violation)"}
- {issue 2: e.g. "Coverage was 87% (R15 violation)"}

Re-attempt the topic tree with these corrections.

[Re-include full transcript + 15 rules]
```

## 10. License system

### 10.1 Key format

```
YOUTOK-<base32(payload)>-<base32(signature)>
```

**Payload** (JSON, then base32-encoded):

```json
{
  "v": 1,
  "kid": "uuid4 hex",
  "email": "user@example.com",
  "iat": "2026-05-04T12:00:00Z",
  "exp": "2026-12-31T00:00:00Z" | null,
  "max_jobs_per_day": 100 | null,
  "features": ["base"]
}
```

**Signature**: RSA-PSS SHA-256 of payload. Private key kept by admin only. Public key embedded in `assets/keys/public_key.pem`.

### 10.2 Activation flow

1. User opens `http://localhost:8000` for first time.
2. Server checks `data/license.json` — if absent, redirect to `/activate`.
3. User pastes key → POST `/activate`.
4. Server:
   - Decode key, verify signature with embedded public key.
   - Check `exp` not passed.
   - Compute machine_id (see §10.3).
   - Insert into `licenses` table: `key_hash, email, machine_id, expires_at, max_jobs_per_day, features_json, status='active'`.
   - Write `data/license.json`: `{license_id, machine_id, activated_at}`.
   - Redirect to `/dashboard`.
5. Subsequent requests: middleware reads `data/license.json`, queries DB, validates machine_id matches current machine_id, checks not expired. If any check fails → redirect `/activate` with error.

### 10.3 Machine ID

Cross-platform HWID:

- **Mac**: `system_profiler SPHardwareDataType | grep "Hardware UUID"` (or `IOPlatformUUID` via `ioreg`)
- **Windows**: `wmic csproduct get UUID` or `(Get-WmiObject Win32_ComputerSystemProduct).UUID`

Hash with SHA256, take first 16 hex chars. Cache in `data/license.json` to avoid re-running on every request.

### 10.4 Admin keygen

```bash
python -m youtok.license.keygen \
  --private-key ./private_key.pem \
  --email user@example.com \
  --expires 2026-12-31 \
  --max-jobs-per-day 100 \
  --features base

# Prints:
# YOUTOK-XXXX-XXXX...
```

`private_key.pem` lives outside the repo, only on admin's machine. Generate once:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out private_key.pem
openssl rsa -in private_key.pem -pubout -out assets/keys/public_key.pem
```

`public_key.pem` is committed to the repo (so end-users can verify).

### 10.5 Reset (admin only)

```bash
python scripts/reset-license.py
# deletes data/license.json + DELETE FROM licenses;
```

User must re-activate after reset (e.g. moving to a new machine).

## 11. UI design (glass-morphism)

Reuse design tokens from veo-farm: dark navy + purple/pink accent, glass cards, Inter font.

### 11.1 Tailwind palette (CDN-loaded, set in `<head>`)

```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    theme: {
      extend: {
        colors: {
          'bg-deep': '#0A0E27',
          'bg-mid': '#151B3D',
          'accent-purple': '#A855F7',
          'accent-pink': '#EC4899',
          'glass': 'rgba(255,255,255,0.05)',
          'glass-border': 'rgba(255,255,255,0.1)',
        },
        fontFamily: {
          sans: ['Inter', 'system-ui', 'sans-serif'],
        },
      },
    },
  };
</script>
```

### 11.2 Background effect

Floating orbs + backdrop blur. Add to `base.html`:

```html
<body class="bg-bg-deep text-white min-h-screen relative overflow-hidden">
  <!-- floating orbs -->
  <div class="fixed inset-0 -z-10 overflow-hidden">
    <div class="absolute -top-40 -left-40 w-96 h-96 bg-accent-purple/30 rounded-full blur-3xl"></div>
    <div class="absolute top-1/2 right-0 w-96 h-96 bg-accent-pink/20 rounded-full blur-3xl"></div>
    <div class="absolute -bottom-40 left-1/3 w-96 h-96 bg-accent-purple/20 rounded-full blur-3xl"></div>
  </div>
  
  {% block content %}{% endblock %}
</body>
```

### 11.3 Glass card pattern

```html
<div class="bg-glass border border-glass-border backdrop-blur-xl rounded-2xl p-6 shadow-2xl">
  ...
</div>
```

### 11.4 Pages

- `/activate` — single glass card centered, license input + Activate button (gradient purple→pink).
- `/dashboard` — glass header with stats (jobs total / running / done / failed), then job table (glass rows). Sidebar nav.
- `/jobs/new` — 3 tabs (Single / Bulk / Channel) inside one big glass card.
- `/jobs/{id}` — split layout: left column = progress (glass card with WebSocket-driven progress bar + step badges + live log); right column = clips grid (one glass card per clip with thumbnail, topic name, duration, "Open folder" button).

### 11.5 HTMX patterns

- Job table auto-refresh every 5s with `hx-get="/jobs?partial=1" hx-trigger="every 5s" hx-swap="outerHTML"`.
- New job submit: HTMX POST → returns redirect via `HX-Redirect: /jobs/{id}` header.
- Channel preview: HTMX POST `/channels/preview` returns rendered partial with checkboxes, no page reload.

### 11.6 WebSocket live progress

Connect on job detail page:

```javascript
const ws = new WebSocket(`ws://localhost:8000/ws/jobs/${jobId}`);
ws.onmessage = (ev) => {
  const { step, pct, message } = JSON.parse(ev.data);
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('progress-step').textContent = step;
  document.getElementById('progress-message').textContent = message;
};
```

Server side: a background task polls jobs table every 1s for given id; on change, push to all connected sockets for that job.

## 12. API endpoints

```
HTML pages (Jinja):
  GET  /                     → redirect to /activate or /dashboard
  GET  /activate
  POST /activate              (form)
  GET  /dashboard
  GET  /jobs/new
  GET  /jobs/{id}

JSON / HTMX partials:
  GET  /jobs?partial=1                → table partial
  POST /jobs                          → create single
  POST /jobs/bulk                     → create many from textarea (newline-separated)
  POST /jobs/channel                  → create from channel URL (spawn N child jobs)
  POST /channels/preview              → return list of videos in channel (no jobs created)
  GET  /jobs/{id}/clips               → clip cards partial
  GET  /jobs/{id}/manifest            → manifest.json file
  DELETE /jobs/{id}
  GET  /api/stats                     → dashboard stats partial

WebSocket:
  WS   /ws/jobs/{id}                  → live progress
```

## 13. Channel scraping

```python
def enumerate_channel(url: str, filters: ChannelFilters) -> list[VideoMeta]:
    """
    Uses yt-dlp --flat-playlist --dump-json.
    Returns list of {video_id, url, title, duration_sec, upload_date}.
    Apply filters: min_duration_sec, max_duration_sec, limit, date_range.
    """
```

CLI: `yt-dlp --flat-playlist --dump-json -I 1:100 "<channel_url>"` (limit to first 100 videos).

UI flow:
1. User enters channel URL → POST `/channels/preview` → server runs `enumerate_channel` → returns list.
2. UI shows table with checkboxes + filter controls (slider for duration min/max).
3. User submits → POST `/jobs/channel` with selected video URLs → server creates parent job + child jobs.

## 14. Cross-platform notes

### 14.1 Path handling

Always `pathlib.Path`. Never hardcode `/` or `\`. Output path validation:
- Mac: any absolute path under `/Users/...` or `/Volumes/...`
- Windows: `[A-Z]:\...`

### 14.2 Binary discovery

`assets/bin/{platform}/{ffmpeg|ffprobe|yt-dlp}` (`.exe` on Windows). Probe with `which` (mac) / `where` (win) only as fallback.

### 14.3 Subprocess

```python
subprocess.run(
    [str(binary_path), *args],
    check=True,
    capture_output=True,
    text=True,
    encoding="utf-8",  # critical on Windows
)
```

Never `shell=True`. Always pass list of args.

### 14.4 Encoding

All file open: `encoding="utf-8"`. Set `PYTHONIOENCODING=utf-8` in run scripts.

### 14.5 Filename sanitization

```python
INVALID = '<>:"/\\|?*'
def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    for c in INVALID:
        s = s.replace(c, "")
    s = re.sub(r"\s+", "-", s).strip("-").lower()
    return s[:80]
```

### 14.6 ffmpeg/yt-dlp binaries

Bundle in `assets/bin/`:
- `mac/ffmpeg`, `mac/ffprobe`, `mac/yt-dlp` (mac-arm64; chmod +x in install script)
- `win/ffmpeg.exe`, `win/ffprobe.exe`, `win/yt-dlp.exe`

`install-mac.sh` and `install-win.ps1` download these on first run if missing.

## 15. WhisperX device selection

```python
def detect_device() -> tuple[str, str]:
    """Returns (device, compute_type, model_name)."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16", "large-v3"
    except ImportError:
        pass
    return "cpu", "int8", "base"
```

CPU mode: warn user that 15min video → 5-10min transcribe.

GPU mode: install `nvidia-cublas-cu12 nvidia-cudnn-cu12` via pip.

## 16. Error handling + retries

Per stage:

| Stage | Failure | Recovery |
|---|---|---|
| Download | YouTube link dead | mark job failed, log reason |
| Download | Disk full | abort + alert |
| Transcribe | model load OOM | fallback to smaller model + retry once |
| Segment | LLM timeout | retry 2x with exp backoff |
| Segment | LLM returns invalid sentence ID | retry once; if persistent, fallback to time-based split + warning |
| Snap | shot detection crash | skip shot snap, only pause snap |
| Cut | ffmpeg crash | retry once with `-err_detect ignore_err`; if still fail, mark clip failed but continue others |

Logging: `loguru` with file rotation `data/logs/youtok-{time}.log`, 7-day retention.

## 17. Self-check rules (after pipeline)

Implemented in `pipeline.py` after Stage C + snap. If any fail, retry Stage A with feedback (max 2 retries):

```python
def self_check(clips: list[Clip], transcript: Transcript, video_duration: float) -> list[str]:
    issues = []
    for c in clips:
        if c.duration_sec < 60:
            issues.append(f"Clip {c.part_number} duration {c.duration_sec:.1f}s < 60s")
        first_sent = transcript.find_sentence(c.sentence_range_start)
        first_word = first_sent.text.split()[0].rstrip(",.")
        if first_word in {"But", "And", "So", "Then", "However", "Therefore", "Which", "That"}:
            issues.append(f"Clip {c.part_number} starts with continuation word '{first_word}' (R9)")
    coverage = sum(c.duration_sec for c in clips) / video_duration
    if coverage < 0.95:
        issues.append(f"Coverage {coverage:.1%} < 95% (R15)")
    avg_coh = mean(c.coherence_score for c in clips)
    if avg_coh < 4.0:
        issues.append(f"Avg coherence {avg_coh:.2f} < 4.0")
    return issues
```

## 18. Cost estimate per video

15min video, ~2500 words transcript:

- Stage A: ~5K input + 2K output tokens
- Stage B: ~8 calls × (1K input + 500 output)
- Total: ~15K input + 6K output / video

Sonnet 4.6 pricing (current): ~$3/M input, $15/M output.
Per video: $0.045 + $0.090 = **~$0.13 per video**.

Acceptable for industrial use. 100 videos ≈ $13.

## 19. Build session breakdown

See `sessions/01-foundation.md` ... `sessions/06-polish.md` for self-contained Claude Code prompts.

Dependency graph:

```
01-foundation (sequential, must run first)
       │
       ├──→ 02-pipeline ─┐
       ├──→ 03-web-ui ──┤
       └──→ 04-worker ──┤
                         │
                         ▼
                  05-integration (sequential)
                         │
                         ▼
                  06-polish (sequential)
```

Sessions 02, 03, 04 are independent — run in parallel Claude Code windows.

## 20. Acceptance criteria for the whole tool

Tool is "shipped" when:

1. Mac dev: install script runs clean, both server and worker start, license activates, can submit a 5min YouTube test video, get N clips out, all clips ≥ 60s, all play in QuickTime with subtitle visible.
2. Windows production: same E2E test passes.
3. Channel mode: can paste a small channel URL, preview lists videos, submitting 3 videos creates 3 child jobs, all complete sequentially.
4. Bulk mode: paste 5 newline-separated URLs, 5 jobs created.
5. License lock: copying `data/license.json` to another machine does NOT bypass — opens to /activate.
6. Reset script: clears license, tool falls back to /activate.

## 21. Risks + mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Anthropic API rate limit | Med | exponential backoff, cache by transcript hash for re-runs |
| WhisperX OOM on long video | Med | chunk audio at 30min, transcribe in pieces, merge |
| YouTube anti-bot blocks yt-dlp | High | pin yt-dlp version, document update procedure, allow user-agent override |
| ffmpeg crashes on weird codec | Low | `-err_detect ignore_err`, fall back to re-encode source first |
| LLM hallucinates sentence IDs | Med | tool-use schema with regex pattern; pydantic validate; retry once |
| Long video > 30min | Low | hard cap in config, reject in UI |
| User on Apple Silicon Rosetta | Low | check arch in install script, fail loud |
| License key leak | Low (single-machine bind) | accept, rotate keys quarterly |

## 22. Future (out of MVP)

- Multi-language source (auto-detect, translate subs to English)
- Auto-publish to TikTok / Reels / Shorts (Phase 2)
- Web admin UI for license generation (current MVP: CLI only)
- Online license verification (current MVP: offline)
- GPU pool / distributed worker

---

End of SPEC.

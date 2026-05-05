# Session 02 — Pipeline (download + transcribe + segment + snap + render)

> Run **after Session 01**. Independent of 03, 04 — can run in parallel with them.

## Goal

Implement the entire video pipeline as a callable function:
`run_pipeline(job_id, progress_callback)` that downloads a YouTube URL, transcribes, segments by topic with the 15 LLM rules, snaps cuts, renders 9:16 clips with title + word-highlight subs, and writes manifest.

This is the **core IP** of the tool. Take time on the LLM prompts and self-check loop.

## Read first

- `../SPEC.md` sections 7 (pipeline detail), 8 (15 rules), 9 (LLM prompts), 14 (cross-platform), 15 (WhisperX device), 16 (errors), 17 (self-check)
- Code skeleton already in place from Session 01 (`src/youtok/core/`, `src/youtok/llm/`, models, config)

## Deliverables

### 1. `core/downloader.py`

```python
from pathlib import Path
import json, subprocess
from pydantic import BaseModel
from youtok.config import settings


class DownloadResult(BaseModel):
    video_path: Path
    audio_path: Path
    title: str
    video_id: str
    duration_sec: float
    channel_name: str | None


def download_video(url: str, work_dir: Path) -> DownloadResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Get metadata
    meta = subprocess.run(
        [str(settings.ytdlp), "--dump-json", "--no-playlist", url],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    info = json.loads(meta.stdout)
    video_id = info["id"]
    title = info["title"]
    duration = float(info.get("duration", 0))
    channel = info.get("channel")
    
    # Download
    output_template = str(work_dir / f"{video_id}.%(ext)s")
    subprocess.run([
        str(settings.ytdlp),
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        url,
    ], check=True)
    
    video_path = work_dir / f"{video_id}.mp4"
    audio_path = work_dir / f"{video_id}.wav"
    
    # Extract 16kHz mono wav
    subprocess.run([
        str(settings.ffmpeg), "-y",
        "-i", str(video_path),
        "-ar", "16000", "-ac", "1", "-vn",
        str(audio_path),
    ], check=True, capture_output=True)
    
    return DownloadResult(
        video_path=video_path, audio_path=audio_path,
        title=title, video_id=video_id, duration_sec=duration,
        channel_name=channel,
    )
```

Pre-check disk space (≥5GB) — raise `InsufficientDiskSpace` if not.

### 2. `core/transcriber.py`

WhisperX with auto device + word-level timestamps.

```python
def detect_device() -> tuple[str, str, str]:
    """Returns (device, compute_type, model_name)."""
    if settings.whisper_device == "cuda" or (
        settings.whisper_device == "auto" and _cuda_available()
    ):
        model = settings.whisper_model if settings.whisper_model != "auto" else "large-v3"
        return "cuda", "float16", model
    model = settings.whisper_model if settings.whisper_model != "auto" else "base"
    return "cpu", "int8", model
```

Then load `faster_whisper.WhisperModel`, transcribe, then `whisperx.load_align_model` + `whisperx.align` for word-level timestamps.

After alignment, run sentence segmentation:

```python
def split_sentences(words: list[WordToken]) -> list[Sentence]:
    """
    Reconstruct text from words, run nltk.sent_tokenize, then map sentences
    back to word ranges by character position. Each sentence gets:
    - id: f"S{i+1:03d}"
    - text, start (first word.start), end (last word.end), words list.
    """
```

Cache nltk's `punkt_tab` data path in settings; if missing, run `nltk.download('punkt_tab')` once on first call.

Output: `Transcript` per SPEC §7.2.

### 3. `llm/schemas.py`

Pydantic models matching SPEC §7.3:

```python
class SentenceRange(BaseModel):
    start: str = Field(pattern=r"^S\d{3}$")
    end: str = Field(pattern=r"^S\d{3}$")

class SubTopic(BaseModel):
    name: str
    start_sentence: str = Field(pattern=r"^S\d{3}$")
    end_sentence: str = Field(pattern=r"^S\d{3}$")
    parent: str | None = None
    children: list["SubTopic"] = []

class StageAOutput(BaseModel):
    main_topic: str
    intro_strip: SentenceRange | None = None
    outro_strip: SentenceRange | None = None
    sub_topics: list[SubTopic]

class StageBOutput(BaseModel):
    coherence_score: int = Field(ge=1, le=5)
    start_adjust: int = 0
    end_adjust: int = 0
    internal_break: SentenceRange | None = None
    notes: str = ""
```

### 4. `llm/prompts.py`

Two functions: `build_stage_a(transcript, title)` and `build_stage_b(sub_topic_text, claim)`. Embed all 15 rules verbatim from SPEC §8. Include sentence-numbered transcript.

### 5. `llm/client.py`

```python
import anthropic
from youtok.config import settings

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

def call_with_tool(messages, tool_schema, model="claude-sonnet-4-6", max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8000,
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": tool_schema["name"]},
                messages=messages,
            )
            tool_use = next(b for b in resp.content if b.type == "tool_use")
            return tool_use.input
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
            if attempt == max_retries:
                raise
            import time; time.sleep(2 ** attempt)
```

### 6. `core/segmenter.py`

```python
def segment_topics(transcript: Transcript, video_title: str) -> list[ClipPlan]:
    """
    1. Stage A: outline tree
    2. Stage B: validate each leaf in parallel
    3. Apply adjustments + auto-split on internal_break
    4. Length normalize: merge < 60s with same parent
    5. Return list of ClipPlan with sentence ranges + topic name + parent + coherence
    """
```

`ClipPlan` schema:

```python
class ClipPlan(BaseModel):
    topic_name: str
    parent_topic: str | None
    sentence_range_start: str
    sentence_range_end: str
    start_sec: float
    end_sec: float
    duration_sec: float
    coherence_score: float
    warnings: list[str] = []
```

Implementation notes:

- Stage B parallel: use `asyncio` or `ThreadPoolExecutor` (max 8 concurrent calls to respect Anthropic rate limits).
- Auto-split: if Stage B returns `internal_break`, split sub-topic into 2 with the break sentence as boundary; re-run Stage B on the 2 halves (depth 1 only, no infinite recursion).
- Length normalize:

```python
def normalize_length(sub_topics: list[SubTopic], transcript: Transcript) -> list[ClipPlan]:
    clips = []
    buffer = []
    for st in flatten_leaves(sub_topics):
        if buffer and buffer[-1].parent != st.parent:
            # Different parent — flush buffer (might be < 60s, mark warning)
            clips.append(merge(buffer))
            buffer = []
        buffer.append(st)
        if total_duration(buffer) >= settings.min_clip_duration_sec:
            clips.append(merge(buffer))
            buffer = []
    if buffer:
        # Trailing buffer — merge backward into last clip
        if clips:
            clips[-1] = merge_into(clips[-1], buffer)
        else:
            clips.append(merge(buffer))  # whole video < 60s; rare
    return clips
```

### 7. `core/snapper.py`

PySceneDetect once per video (cache on disk); pause snap from word timestamps.

```python
def snap_cuts(
    clips: list[ClipPlan],
    transcript: Transcript,
    video_path: Path,
) -> list[ClipPlan]:
    shot_boundaries = detect_shots(video_path)  # cached
    for c in clips:
        c.start_sec = snap_to_pause(c.start_sec, transcript, settings.pause_threshold_sec, settings.snap_window_sec)
        c.end_sec = snap_to_pause(c.end_sec, transcript, settings.pause_threshold_sec, settings.snap_window_sec)
        c.start_sec = snap_to_shot(c.start_sec, shot_boundaries, settings.snap_window_sec)
        c.end_sec = snap_to_shot(c.end_sec, shot_boundaries, settings.snap_window_sec)
    return clips
```

`detect_shots`: `scenedetect.detect(video_path, ContentDetector(threshold=27))` → returns list of `(start_seconds, end_seconds)` tuples.

### 8. `core/compositor.py`

#### 8a. ASS generation (word-highlight)

```python
def generate_ass(words: list[WordToken], clip_start: float, config: SubConfig) -> str:
    """
    Group words into chunks of 5. For each chunk, emit one Dialogue line per word,
    where the active word is yellow + bold and others are white.
    Time-shifted by clip_start.
    """
```

ASS skeleton:

```
[Script Info]
Title: youtok
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, ...
Style: Default,Inter Bold,64,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,4,0,2,0,0,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,{\\c&H35EBFF&}word1{\\c&HFFFFFF&} word2 word3
Dialogue: 0,0:00:00.50,0:00:01.00,Default,,0,0,0,,word1 {\\c&H35EBFF&}word2{\\c&HFFFFFF&} word3
...
```

Use `pysubs2` to build, or just emit raw ASS strings. Test with `ffmpeg -f lavfi -i color -vf "ass=test.ass" out.mp4`.

Color reference (ASS uses BGR not RGB): yellow `#FFEB3B` → `&H003BEBFF` (with leading 00 alpha).

#### 8b. Title wrapping + drawtext

```python
def wrap_title(title: str, max_width_px: int = 1000, font_size: int = 52, font_path: Path = ...) -> list[str]:
    """
    Use PIL.ImageFont to measure text width, greedy wrap by words.
    Return list of lines (max 2 lines; ellipsis if more).
    """
```

#### 8c. Render

```python
def render_clip(
    source_mp4: Path,
    start: float,
    end: float,
    ass_path: Path,
    title_lines: list[str],
    output_path: Path,
):
    drawtext_filters = []
    for i, line in enumerate(title_lines):
        y = 120 + i * 70
        # Escape colons, single quotes for ffmpeg
        escaped = line.replace(":", r"\:").replace("'", r"\'")
        drawtext_filters.append(
            f"drawtext=fontfile={settings.fonts_dir / 'Inter-Bold.ttf'}:"
            f"text='{escaped}':fontcolor=white:fontsize=52:"
            f"x=(w-text_w)/2:y={y}"
        )
    
    vf = (
        f"scale=1080:608,"
        f"pad=1080:1920:0:360:black,"
        + ",".join(drawtext_filters)
        + f",ass='{ass_path}'"
    )
    
    subprocess.run([
        str(settings.ffmpeg), "-y",
        "-ss", str(start), "-to", str(end),
        "-i", str(source_mp4),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ], check=True, capture_output=True)
```

### 9. `core/pipeline.py`

```python
def run_pipeline(job_id: int, progress_callback: Callable[[str, int, str], None]):
    db = SessionLocal()
    job = db.get(Job, job_id)
    
    progress_callback("downloading", 0, "Starting download")
    work = settings.workdir / f"job-{job_id}"
    dl = download_video(job.source_url, work)
    job.video_title = dl.title
    job.video_duration_sec = dl.duration_sec
    db.commit()
    progress_callback("downloading", 15, f"Downloaded: {dl.title}")
    
    progress_callback("transcribing", 20, "Loading WhisperX")
    transcript = transcribe(dl.audio_path)
    save_json(work / "transcript.json", transcript.model_dump())
    progress_callback("transcribing", 40, f"{len(transcript.sentences)} sentences")
    
    progress_callback("segmenting", 45, "Stage A — outline")
    plan = segment_topics(transcript, dl.title)
    progress_callback("segmenting", 60, f"{len(plan)} clips planned")
    
    progress_callback("snapping", 62, "Detecting shot boundaries")
    plan = snap_cuts(plan, transcript, dl.video_path)
    progress_callback("snapping", 65, "Cuts snapped")
    
    # Self-check + retry up to 2x
    for attempt in range(3):
        issues = self_check(plan, transcript, dl.duration_sec)
        if not issues:
            break
        if attempt < 2:
            progress_callback("segmenting", 60, f"Self-check failed, retry {attempt+1}/2")
            plan = re_segment_with_feedback(transcript, dl.title, issues)
            plan = snap_cuts(plan, transcript, dl.video_path)
    
    save_json(work / "topic-tree.json", [c.model_dump() for c in plan])
    
    # Render
    out_dir = Path(job.output_dir) / make_folder_name(dl.title, dl.video_id)
    (out_dir / "clips").mkdir(parents=True, exist_ok=True)
    
    manifest = {"video_title": dl.title, "video_id": dl.video_id, "total_clips": len(plan), "clips": []}
    for i, clip in enumerate(plan, 1):
        progress_callback("cutting", 65 + int(30 * i / len(plan)), f"Rendering clip {i}/{len(plan)}")
        clip_words = collect_words(clip.start_sec, clip.end_sec, transcript)
        ass_path = work / f"clip-{i:02d}.ass"
        ass_path.write_text(generate_ass(clip_words, clip.start_sec, sub_config), encoding="utf-8")
        title_lines = wrap_title(f"{dl.title} - Part {i}/{len(plan)}")
        clip_path = out_dir / "clips" / f"{i:02d}_{slug(clip.topic_name)}.mp4"
        render_clip(dl.video_path, clip.start_sec, clip.end_sec, ass_path, title_lines, clip_path)
        # write Clip row
        db.add(Clip(...))
        manifest["clips"].append({
            "part": f"{i}/{len(plan)}", "topic": clip.topic_name, ...
        })
    db.commit()
    
    save_json(out_dir / "manifest.json", manifest)
    save_json(out_dir / "transcript.json", transcript.model_dump())
    
    progress_callback("cleanup", 95, "Removing source files")
    dl.video_path.unlink()
    dl.audio_path.unlink()
    
    job.status = "done"
    job.clips_count = len(plan)
    job.finished_at = datetime.utcnow()
    db.commit()
    progress_callback("done", 100, "Complete")
```

### 10. CLI commands for testing

In `cli.py` add:

```python
@main.command()
@click.option("--url", required=True)
@click.option("--out", required=True, type=click.Path())
def run(url, out):
    """End-to-end pipeline test."""
    from youtok.core.pipeline import run_pipeline
    # create temp Job row, run, print progress
    ...
```

## Acceptance test

Pick a 3-minute educational YouTube video for testing (e.g. a Khan Academy short).

```bash
uv run python -m youtok.cli run --url <test_url> --out ./tmp_test
ls tmp_test/*/clips/*.mp4
# expected: 2-3 clips, each ≥60s
```

For each clip:
- Plays in QuickTime (mac) / VLC (win)
- 1080x1920 dimensions: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height ...`
- Audio not cut mid-word (manual ear check)
- Subtitle visible at bottom, words highlight in yellow
- Title visible at top

Coverage check:

```bash
python -c "
import json
m = json.load(open('tmp_test/.../manifest.json'))
total_clip = sum(c['duration_sec'] for c in m['clips'])
# Should be ≥ 95% of video duration (minus intro/outro strip if any)
"
```

## Anti-patterns to avoid

- Calling LLM for every sentence — only Stage A (1 call) and Stage B (per-leaf parallel).
- Hardcoding `python3` or `ffmpeg` — always `settings.ffmpeg`.
- Writing files with default encoding — always `encoding="utf-8"`.
- Forgetting `--no-playlist` on yt-dlp — single video URL might be in a playlist.
- Using `time.sleep` in pipeline — let progress callback emit events instead.
- Re-running PySceneDetect per clip — once per video, cache.
- Stream-copy with `-c copy` — cuts will be wrong (only at I-frames). Always re-encode.

## When done

Notify integration session (05) with:
- Path to a test output folder + sample manifest.json
- LLM cost per video (from anthropic logs)

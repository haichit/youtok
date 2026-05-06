"""Transcribe engine with fallback chain: mlx-whisper (Apple Silicon) → faster-whisper (universal).

Audio chunking parallel transcribe via ThreadPoolExecutor (faster-whisper releases GIL during inference).
"""
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger

from youtok.config import settings
from youtok.core.transcriber import WordToken, split_sentences
from youtok.core.transcriber import Transcript


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64")


def _has_mlx_whisper() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_MLX_AVAILABLE: bool | None = None


def detect_engine() -> str:
    """Pick the best available transcribe engine."""
    global _MLX_AVAILABLE
    if _MLX_AVAILABLE is None:
        _MLX_AVAILABLE = _is_apple_silicon() and _has_mlx_whisper()
        logger.info(f"Transcribe engine: mlx-whisper available={_MLX_AVAILABLE}")
    return "mlx" if _MLX_AVAILABLE else "faster-whisper"


# Singleton mlx model handle (mlx-whisper loads model lazily on first transcribe call,
# subsequent calls reuse the loaded weights from the same process).
_mlx_model_loaded: dict[str, bool] = {}


def _transcribe_chunk_mlx(audio_path: Path, language: str, model_repo: str) -> list[WordToken]:
    """Transcribe one audio chunk via mlx-whisper.
    model_repo: HuggingFace model id, e.g. 'mlx-community/distil-large-v3'."""
    import mlx_whisper
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model_repo,
        language=language,
        word_timestamps=True,
        verbose=False,
    )
    _mlx_model_loaded[model_repo] = True
    words: list[WordToken] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append(WordToken(
                word=text,
                start=round(w.get("start", 0.0), 3),
                end=round(w.get("end", 0.0), 3),
            ))
    return words


def _transcribe_chunk_fw(audio_path: Path, language: str) -> list[WordToken]:
    """Transcribe one chunk via faster-whisper (singleton model)."""
    from youtok.core.transcriber import detect_device, _get_whisper_model
    device, compute_type, model_name = detect_device()
    model = _get_whisper_model(model_name, device, compute_type)
    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )
    words: list[WordToken] = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                text = w.word.strip()
                if not text:
                    continue
                words.append(WordToken(
                    word=text,
                    start=round(w.start, 3),
                    end=round(w.end, 3),
                ))
    return words


def _split_audio_chunks(audio_path: Path, work_dir: Path, chunk_sec: int = 300, overlap_sec: int = 5) -> list[tuple[Path, float]]:
    """Split audio into N chunks of `chunk_sec` length with `overlap_sec` overlap between adjacent chunks.
    Returns [(chunk_path, offset_in_original), ...]."""
    work_dir.mkdir(parents=True, exist_ok=True)

    # Get duration via ffprobe
    r = subprocess.run(
        [str(settings.ffprobe), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(audio_path)],
        capture_output=True, text=True, timeout=10,
    )
    try:
        duration = float(r.stdout.strip())
    except Exception:
        duration = 0.0

    # Don't bother chunking short audio — overhead exceeds benefit.
    if duration <= chunk_sec * 1.2:
        return [(audio_path, 0.0)]

    chunks: list[tuple[Path, float]] = []
    t = 0.0
    idx = 0
    while t < duration:
        end = min(t + chunk_sec, duration)
        # Add overlap on the trailing side of every chunk except the last
        actual_end = end + overlap_sec if end < duration else end
        chunk_path = work_dir / f"chunk_{idx:03d}.wav"
        cmd = [
            str(settings.ffmpeg), "-y", "-loglevel", "error",
            "-ss", str(t), "-to", str(actual_end),
            "-i", str(audio_path),
            "-ar", "16000", "-ac", "1",
            str(chunk_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        chunks.append((chunk_path, t))
        t = end
        idx += 1
    return chunks


def _dedupe_overlap(words: list[WordToken], boundary_sec: float, tolerance_sec: float = 5.0) -> list[WordToken]:
    """Remove duplicate words near a chunk boundary (overlap region).
    Strategy: within ±tolerance of boundary_sec, keep words from the EARLIER chunk only
    (i.e., drop words from later chunks whose start is < boundary_sec)."""
    return [w for w in words if not (boundary_sec - tolerance_sec <= w.start < boundary_sec)]


def transcribe_engine(audio_path: Path, language: str = "en", use_chunking: bool = True) -> Transcript:
    """Main entry. Auto-selects engine + chunking strategy."""
    from youtok.core.cache import load_transcript, save_transcript
    cached = load_transcript(audio_path)
    if cached is not None:
        return cached

    engine = detect_engine()
    logger.info(f"Transcribe engine selected: {engine}")

    work_chunks = audio_path.parent / f"_chunks_{audio_path.stem}"

    if use_chunking:
        chunks = _split_audio_chunks(audio_path, work_chunks, chunk_sec=300, overlap_sec=5)
    else:
        chunks = [(audio_path, 0.0)]

    all_words: list[WordToken] = []
    use_threads = len(chunks) > 1

    def _do_chunk(chunk_path: Path, offset: float) -> list[WordToken]:
        if engine == "mlx":
            try:
                model_repo = "mlx-community/distil-large-v3"
                ws = _transcribe_chunk_mlx(chunk_path, language, model_repo)
            except Exception as e:
                logger.warning(f"mlx-whisper chunk failed, fallback to faster-whisper: {e}")
                ws = _transcribe_chunk_fw(chunk_path, language)
        else:
            ws = _transcribe_chunk_fw(chunk_path, language)
        # Apply offset to put timestamps back into original audio's timeline
        return [WordToken(word=w.word, start=w.start + offset, end=w.end + offset) for w in ws]

    if use_threads and len(chunks) > 1:
        # ThreadPool — both engines release GIL during inference (Metal/CTranslate2 calls)
        # but mlx_whisper holds a single Metal context globally; using threads is fine but
        # parallel benefit is mainly on faster-whisper CPU path. Keep workers low to avoid
        # OOM on Whisper model copies if engine internally clones.
        max_workers = min(3, len(chunks))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(lambda c: _do_chunk(c[0], c[1]), chunks))
    else:
        results = [_do_chunk(c[0], c[1]) for c in chunks]

    # Merge + dedupe overlaps
    for i, ws in enumerate(results):
        if i > 0:
            # Boundary at start of chunk i = chunks[i].offset
            boundary = chunks[i][1]
            ws = [w for w in ws if w.start >= boundary - 0.1]  # only keep words from chunk i past boundary
            # Earlier chunk(s) may have words within overlap region — those stay; new chunk drops them
        all_words.extend(ws)

    # Sort and final dedupe by (round(start,2), word)
    all_words.sort(key=lambda w: w.start)
    seen = set()
    deduped = []
    for w in all_words:
        key = (round(w.start, 2), w.word)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(w)
    all_words = deduped

    sentences = split_sentences(all_words)
    duration = all_words[-1].end if all_words else 0.0

    logger.info(
        f"Transcribed (engine={engine}, chunks={len(chunks)}): "
        f"{len(all_words)} words, {len(sentences)} sentences, {duration:.1f}s"
    )

    transcript = Transcript(
        language=language,
        duration_sec=duration,
        sentences=sentences,
    )

    # Cleanup chunk files
    if work_chunks.exists():
        try:
            import shutil
            shutil.rmtree(work_chunks, ignore_errors=True)
        except Exception:
            pass

    save_transcript(audio_path, transcript)
    return transcript

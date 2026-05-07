import platform
import subprocess
import tempfile
import time
from pathlib import Path

from loguru import logger

from youtok.config import settings
from youtok.core.segmenter import ClipPlan
from youtok.core.transcriber import Transcript

_shot_cache: dict[str, list[float]] = {}


def _make_proxy_270p(video_path: Path) -> Path | None:
    """Re-encode the source to 480×270 H.264 for PySceneDetect to chew on.
    PySceneDetect's bottleneck is per-frame decode — at 1080p AV1 it spends
    ~133s on a 5-min video; the same content at 270p H.264 takes ~4s. The
    re-encode itself runs ~12s with CUDA decode (H.264 source) or ~32s
    pure-CPU (AV1 source). Net win: 100s+ on a typical 14-min video.

    Returns None on any failure — caller must fall back to direct decode."""
    try:
        tmpdir = Path(tempfile.gettempdir()) / "youtok_proxies"
        tmpdir.mkdir(parents=True, exist_ok=True)
        # Stamp with mtime so re-runs on the same source can reuse if cached
        proxy = tmpdir / f"{video_path.stem}-{int(video_path.stat().st_mtime)}-270p.mp4"
        if proxy.exists() and proxy.stat().st_size > 0:
            return proxy

        # Try CUDA decode first (works for H.264/HEVC/VP9 on most NVIDIA GPUs;
        # AV1 decode needs Ampere RTX 30+). Fall back to CPU decode if it fails.
        cuda_ok = platform.system() == "Windows"
        for hwaccel_args in ([("-hwaccel", "cuda")] if cuda_ok else []) + [()]:
            cmd = [
                str(settings.ffmpeg), "-y", "-loglevel", "error",
                *hwaccel_args,
                "-i", str(video_path),
                "-vf", "scale=480:270",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
                "-an",
                str(proxy),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode == 0 and proxy.exists() and proxy.stat().st_size > 0:
                return proxy
            # CUDA path failed (e.g. AV1 source on a 1660); strip and retry CPU.
            proxy.unlink(missing_ok=True)
        return None
    except Exception as e:
        logger.warning(f"Proxy 270p prep failed: {e}")
        return None


def _detect_keyframes(video_path: Path) -> list[float]:
    """Fast: extract video keyframe (I-frame) timestamps via ffprobe (~1s for 15min video).
    Keyframes often align with scene cuts (encoders insert keyframes on big content change)."""
    import subprocess
    from youtok.config import settings as _s
    try:
        r = subprocess.run(
            [
                str(_s.ffprobe), "-v", "error",
                "-select_streams", "v:0",
                "-skip_frame", "nokey",
                "-show_frames", "-show_entries", "frame=pts_time",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            logger.warning(f"ffprobe keyframes failed: {r.stderr[:200]}")
            return []
        ts = []
        for line in r.stdout.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            try:
                ts.append(float(line))
            except ValueError:
                continue
        return sorted(set(ts))
    except Exception as e:
        logger.warning(f"ffprobe keyframes error: {e}")
        return []


def detect_shots(video_path: Path) -> list[float]:
    key = str(video_path)
    if key in _shot_cache:
        return _shot_cache[key]

    # Persistent disk cache check
    from youtok.core.cache import load_shots, save_shots
    cached = load_shots(video_path)
    if cached is not None:
        _shot_cache[key] = cached
        return cached

    # Two-source shot detection:
    # 1) PySceneDetect ContentDetector — accurate but slow
    # 2) ffprobe keyframes — fast additional candidates (encoder-inserted at content shifts)
    # Combined and deduped within ±0.5s tolerance.
    boundaries: list[float] = []
    try:
        from scenedetect import detect, ContentDetector
        # Run PySceneDetect on a 270p proxy: timestamps map 1:1 to the original
        # but each frame decodes ~17× faster. If proxy prep fails for any
        # reason, fall back to decoding the full-res source.
        t = time.time()
        proxy = _make_proxy_270p(video_path)
        scan_target = str(proxy) if proxy else str(video_path)
        if proxy:
            logger.info(f"Scene-detect proxy ready in {time.time() - t:.1f}s: {proxy.name}")
        scene_list = detect(scan_target, ContentDetector(threshold=27))
        for start, end in scene_list:
            boundaries.append(start.get_seconds())
            boundaries.append(end.get_seconds())
        logger.info(
            f"PySceneDetect: {len(set(boundaries))} boundaries "
            f"({time.time() - t:.1f}s {'on proxy' if proxy else 'direct'})"
        )
    except Exception as e:
        logger.warning(f"PySceneDetect failed (continuing with keyframes only): {e}")

    keyframes = _detect_keyframes(video_path)
    if keyframes:
        boundaries.extend(keyframes)
        logger.info(f"ffprobe keyframes: {len(keyframes)} boundaries")

    # Dedupe with ±0.5s tolerance
    boundaries = sorted(set(boundaries))
    deduped: list[float] = []
    for b in boundaries:
        if not deduped or abs(b - deduped[-1]) > 0.5:
            deduped.append(b)

    logger.info(f"Total combined shot boundaries: {len(deduped)}")
    _shot_cache[key] = deduped
    save_shots(video_path, deduped)
    return deduped


def _find_pause(
    target_sec: float,
    transcript: Transcript,
    pause_threshold: float,
    window: float,
) -> float | None:
    best_pause: float | None = None
    best_gap: float = 0.0

    all_words = []
    for s in transcript.sentences:
        all_words.extend(s.words)

    for i in range(len(all_words) - 1):
        gap_start = all_words[i].end
        gap_end = all_words[i + 1].start
        gap = gap_end - gap_start

        if gap < pause_threshold:
            continue

        mid = (gap_start + gap_end) / 2
        if abs(mid - target_sec) <= window:
            if gap > best_gap:
                best_gap = gap
                best_pause = mid

    return best_pause


def _find_shot(
    target_sec: float,
    boundaries: list[float],
    window: float,
) -> float | None:
    best: float | None = None
    best_dist = window + 1

    for b in boundaries:
        dist = abs(b - target_sec)
        if dist <= window and dist < best_dist:
            best_dist = dist
            best = b

    return best


def snap_to_pause(
    sec: float,
    transcript: Transcript,
    pause_threshold: float,
    window: float,
) -> float:
    paused = _find_pause(sec, transcript, pause_threshold, window)
    return paused if paused is not None else sec


def snap_to_shot(
    sec: float,
    boundaries: list[float],
    window: float,
) -> float:
    shot = _find_shot(sec, boundaries, window)
    return shot if shot is not None else sec


def snap_cuts(
    clips: list[ClipPlan],
    transcript: Transcript,
    video_path: Path,
) -> list[ClipPlan]:
    shot_boundaries = detect_shots(video_path)

    for c in clips:
        c.start_sec = snap_to_pause(
            c.start_sec, transcript,
            settings.pause_threshold_sec, settings.snap_window_sec,
        )
        c.end_sec = snap_to_pause(
            c.end_sec, transcript,
            settings.pause_threshold_sec, settings.snap_window_sec,
        )
        c.start_sec = snap_to_shot(c.start_sec, shot_boundaries, settings.snap_window_sec)
        c.end_sec = snap_to_shot(c.end_sec, shot_boundaries, settings.snap_window_sec)
        c.duration_sec = c.end_sec - c.start_sec

    return clips

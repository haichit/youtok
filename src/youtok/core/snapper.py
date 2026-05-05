from pathlib import Path

from loguru import logger

from youtok.config import settings
from youtok.core.segmenter import ClipPlan
from youtok.core.transcriber import Transcript

_shot_cache: dict[str, list[float]] = {}


def detect_shots(video_path: Path) -> list[float]:
    key = str(video_path)
    if key in _shot_cache:
        return _shot_cache[key]

    try:
        from scenedetect import detect, ContentDetector
        scene_list = detect(str(video_path), ContentDetector(threshold=27))
        boundaries = []
        for start, end in scene_list:
            boundaries.append(start.get_seconds())
            boundaries.append(end.get_seconds())
        boundaries = sorted(set(boundaries))
        logger.info(f"Detected {len(boundaries)} shot boundaries")
        _shot_cache[key] = boundaries
        return boundaries
    except Exception as e:
        logger.warning(f"Shot detection failed, skipping: {e}")
        _shot_cache[key] = []
        return []


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

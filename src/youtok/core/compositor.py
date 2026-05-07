import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from youtok.config import settings
from youtok.core.transcriber import WordToken


def _safe_copy(src: Path, dst: Path) -> None:
    """Race-free copy: parallel render workers all try to copy fonts/logos
    into the shared work_dir. Without this, two threads see `dst` missing,
    both call shutil.copy2, and the second one hits
    PermissionError [WinError 32] because the first still owns the write
    handle. Copy to a per-worker tmp path then os.replace (atomic on
    Windows when dst doesn't exist; if another worker beat us, swallow
    the error — the file is already in place)."""
    if dst.exists() and dst.stat().st_size > 0:
        return
    tmp = dst.with_suffix(dst.suffix + f".tmp.{os.getpid()}.{id(dst)}")
    try:
        shutil.copy2(src, tmp)
        try:
            os.replace(tmp, dst)
        except PermissionError:
            # Another worker raced ahead and is mid-write; wait briefly
            for _ in range(50):
                time.sleep(0.05)
                if dst.exists() and dst.stat().st_size > 0:
                    break
            tmp.unlink(missing_ok=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _supports_videotoolbox() -> bool:
    """Check if h264_videotoolbox encoder is available (Mac with hardware H.264)."""
    if platform.system() != "Darwin":
        return False
    try:
        r = subprocess.run(
            [str(settings.ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "h264_videotoolbox" in r.stdout
    except Exception:
        return False


_VIDEOTOOLBOX_AVAILABLE: bool | None = None


def has_videotoolbox() -> bool:
    global _VIDEOTOOLBOX_AVAILABLE
    if _VIDEOTOOLBOX_AVAILABLE is None:
        _VIDEOTOOLBOX_AVAILABLE = _supports_videotoolbox()
        logger.info(f"h264_videotoolbox available: {_VIDEOTOOLBOX_AVAILABLE}")
    return _VIDEOTOOLBOX_AVAILABLE


def _supports_nvenc() -> bool:
    """Check h264_nvenc encoder is built in AND a working NVIDIA GPU is present.
    Encoder being listed isn't enough — driver / GPU might be missing."""
    if platform.system() != "Windows":
        return False
    try:
        r = subprocess.run(
            [str(settings.ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        if "h264_nvenc" not in r.stdout:
            return False
        # Real probe: encode 1 frame of black via NVENC. Fails fast (~0.5s) if no GPU.
        probe = subprocess.run(
            [
                str(settings.ffmpeg), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.04",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=10,
        )
        return probe.returncode == 0
    except Exception:
        return False


_NVENC_AVAILABLE: bool | None = None


def has_nvenc() -> bool:
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is None:
        _NVENC_AVAILABLE = _supports_nvenc()
        logger.info(f"h264_nvenc available: {_NVENC_AVAILABLE}")
    return _NVENC_AVAILABLE

FONT_FILE = "NotoSans-Bold.ttf"
FONT_NAME = "Noto Sans Bold"


class SubConfig(BaseModel):
    font_name: str = FONT_NAME
    font_size: int = 56
    highlight_color: str = "&H003BEBFF&"
    normal_color: str = "&H00FFFFFF&"
    outline_color: str = "&H00000000&"
    back_color: str = "&H80000000&"
    outline_width: int = 3
    margin_v: int = 240
    chunk_size: int = 5


def _format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def generate_ass(words: list[WordToken], clip_start: float, config: SubConfig | None = None) -> str:
    if config is None:
        config = SubConfig()

    header = f"""[Script Info]
Title: youtok
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{config.font_name},{config.font_size},{config.normal_color},&H000000FF&,{config.outline_color},{config.back_color},1,0,0,0,100,100,0,0,1,{config.outline_width},1,2,40,40,{config.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []

    for chunk_start in range(0, len(words), config.chunk_size):
        chunk = words[chunk_start:chunk_start + config.chunk_size]
        if not chunk:
            break

        block_start = max(0.0, chunk[0].start - clip_start)
        block_end = max(0.0, chunk[-1].end - clip_start)
        if block_end <= block_start:
            continue

        parts: list[str] = []
        cursor = block_start
        for w in chunk:
            w_start = max(0.0, w.start - clip_start)
            w_end = max(0.0, w.end - clip_start)
            pre_cs = max(0, int(round((w_start - cursor) * 100)))
            dur_cs = max(1, int(round((w_end - w_start) * 100)))
            if pre_cs > 0:
                parts.append(f"{{\\k{pre_cs}}}")
            parts.append(
                f"{{\\k{dur_cs}\\1c{config.highlight_color}\\b1}}{w.word}"
                f"{{\\1c{config.normal_color}\\b0}}"
            )
            parts.append(" ")
            cursor = w_end

        text = "".join(parts).rstrip()
        events.append(
            f"Dialogue: 0,{_format_ass_time(block_start)},{_format_ass_time(block_end)},Default,,0,0,0,,{text}"
        )

    return header + "\n".join(events) + "\n"


def wrap_title(
    title: str,
    max_width_px: int = 980,
    font_size: int = 44,
    font_path: Path | None = None,
) -> list[str]:
    if font_path is None:
        font_path = settings.fonts_dir / FONT_FILE

    try:
        from PIL import ImageFont
        font = ImageFont.truetype(str(font_path), font_size)

        def text_width(text: str) -> int:
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0]
    except Exception:
        def text_width(text: str) -> int:
            return len(text) * int(font_size * 0.6)

    words = title.split()
    lines: list[str] = []
    current_line = ""

    for word in words:
        test = f"{current_line} {word}".strip()
        if text_width(test) <= max_width_px:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    if len(lines) > 2:
        lines = [lines[0], lines[1].rstrip() + "..."]

    return lines


def render_clip(
    source_mp4: Path,
    start: float,
    end: float,
    ass_path: Path,
    title_lines: list[str],
    output_path: Path,
    logo_top_path: Path | None = None,
    logo_bottom_path: Path | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = ass_path.parent
    font_src = settings.fonts_dir / FONT_FILE
    font_local = work_dir / FONT_FILE
    _safe_copy(font_src, font_local)

    drawtext_filters = []
    title_font_size = 44
    line_h = 56
    base_y = 200
    for i, line in enumerate(title_lines):
        y = base_y + i * line_h
        title_txt = work_dir / f"{ass_path.stem}-title-{i}.txt"
        title_txt.write_text(line, encoding="utf-8")
        drawtext_filters.append(
            f"drawtext=fontfile={font_local.name}:"
            f"textfile={title_txt.name}:fontcolor=white:fontsize={title_font_size}:"
            f"borderw=2:bordercolor=black@0.6:"
            f"x=(w-text_w)/2:y={y}"
        )

    source_resolved = source_mp4.resolve()
    work_resolved = work_dir.resolve()
    if source_resolved.parent == work_resolved:
        source_arg = source_mp4.name
    else:
        source_local = work_dir / source_mp4.name
        if not source_local.exists():
            source_local.symlink_to(source_resolved)
        source_arg = source_mp4.name

    # Copy logo files to workdir if provided
    logo_top_local = None
    logo_bot_local = None
    if logo_top_path and logo_top_path.exists():
        logo_top_local = work_dir / f"logo_top.png"
        _safe_copy(logo_top_path, logo_top_local)
    if logo_bottom_path and logo_bottom_path.exists():
        logo_bot_local = work_dir / f"logo_bot.png"
        _safe_copy(logo_bottom_path, logo_bot_local)

    has_logos = logo_top_local or logo_bot_local
    if has_logos:
        # filter_complex: base video chain → overlay logo(s) at fixed positions
        # Top logo: 1080x150 at y=0. Bottom logo: 1080x150 at y=1770.
        base_chain = [
            "scale=1080:1080:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        ]
        base_chain.extend(drawtext_filters)
        base_chain.append(f"subtitles=f={ass_path.name}:fontsdir=.")

        fc_parts = ["[0:v]" + ",".join(base_chain) + "[base]"]
        extra_inputs = []
        input_idx = 1
        current_label = "base"

        if logo_top_local:
            extra_inputs.extend(["-i", logo_top_local.name])
            next_label = "with_top" if logo_bot_local else "vout"
            fc_parts.append(f"[{current_label}][{input_idx}:v]overlay=0:0[{next_label}]")
            current_label = next_label
            input_idx += 1

        if logo_bot_local:
            extra_inputs.extend(["-i", logo_bot_local.name])
            fc_parts.append(f"[{current_label}][{input_idx}:v]overlay=0:1770[vout]")

        filter_complex = ";".join(fc_parts)
        filter_args = ["-filter_complex", filter_complex, "-map", "[vout]", "-map", "0:a?"]
    else:
        vf_parts = [
            "scale=1080:1080:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        ]
        vf_parts.extend(drawtext_filters)
        vf_parts.append(f"subtitles=f={ass_path.name}:fontsdir=.")
        vf = ",".join(vf_parts)
        filter_args = ["-vf", vf]
        extra_inputs = []

    if has_videotoolbox():
        video_codec_args = ["-c:v", "h264_videotoolbox", "-q:v", "55", "-realtime", "0"]
    elif has_nvenc():
        # GPU encode on NVIDIA. p5 = quality-leaning preset (still ~10× faster
        # than libx264 medium on a 1660 SUPER). VBR with CQ22 + spatial/temporal
        # AQ matches libx264 medium CRF18 visually; cap bitrate so files don't
        # balloon on high-motion clips.
        video_codec_args = [
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", "22",
            "-b:v", "4M",
            "-maxrate", "6M",
            "-bufsize", "8M",
            "-spatial_aq", "1",
            "-temporal_aq", "1",
        ]
    else:
        video_codec_args = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]

    audio_args = [
        "-c:a", "aac", "-b:a", "128k",
        "-af", "aresample=async=1:first_pts=0",
    ]

    cmd = [
        str(settings.ffmpeg), "-y",
        "-ss", str(start), "-to", str(end),
        "-i", source_arg,
        *extra_inputs,
        *filter_args,
        *video_codec_args,
        *audio_args,
        "-movflags", "+faststart",
        "-threads", "0",
        str(output_path.resolve()),
    ]

    logger.info(f"Rendering: {output_path.name}")
    logger.debug(f"ffmpeg cwd={work_dir}")
    logger.debug(f"ffmpeg cmd: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=work_dir)
    if result.returncode != 0:
        logger.error(f"ffmpeg returncode={result.returncode}")
        logger.error(f"ffmpeg stderr (last 2000 chars):\n{result.stderr[-2000:]}")
        logger.error(f"ffmpeg stdout (last 500 chars):\n{result.stdout[-500:]}")
        print(f"FFMPEG STDERR:\n{result.stderr}", flush=True)
        result.check_returncode()


def render_clips_parallel(
    jobs: list[dict],
    max_workers: int | None = None,
    progress_callback=None,
) -> None:
    """Render multiple clips in parallel.

    Each job dict: {source_mp4, start, end, ass_path, title_lines, output_path}
    Uses ThreadPoolExecutor — ffmpeg is subprocess (releases GIL automatically).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import os

    if not jobs:
        return

    if max_workers is None:
        # Mac M-series: 2-3 parallel ffmpeg saturate videotoolbox HW encoder + CPU filters
        # Software libx264: scale by core count, but cap at 3 to avoid thrashing
        cpu = os.cpu_count() or 4
        max_workers = min(3, cpu, len(jobs))

    logger.info(f"Rendering {len(jobs)} clips in parallel (workers={max_workers})")

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(render_clip, **job): i for i, job in enumerate(jobs)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                future.result()
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(jobs), idx)
            except Exception as e:
                logger.exception(f"Clip {idx} render failed: {e}")
                raise

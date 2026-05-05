import shutil
import subprocess
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from youtok.config import settings
from youtok.core.transcriber import WordToken

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
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    work_dir = ass_path.parent
    font_src = settings.fonts_dir / FONT_FILE
    font_local = work_dir / FONT_FILE
    if not font_local.exists():
        shutil.copy2(font_src, font_local)

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

    vf_parts = [
        "scale=1080:1080:force_original_aspect_ratio=decrease",
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
    ]
    vf_parts.extend(drawtext_filters)
    vf_parts.append(f"subtitles=f={ass_path.name}:fontsdir=.")
    vf = ",".join(vf_parts)

    source_resolved = source_mp4.resolve()
    work_resolved = work_dir.resolve()
    if source_resolved.parent == work_resolved:
        source_arg = source_mp4.name
    else:
        source_local = work_dir / source_mp4.name
        if not source_local.exists():
            source_local.symlink_to(source_resolved)
        source_arg = source_mp4.name

    cmd = [
        str(settings.ffmpeg), "-y",
        "-ss", str(start), "-to", str(end),
        "-i", source_arg,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
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

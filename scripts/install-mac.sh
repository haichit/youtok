#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

uv venv --python 3.11
uv pip install -e ".[dev]"

mkdir -p assets/bin/mac assets/keys assets/fonts data

# Download ffmpeg/ffprobe (mac arm64) if missing
if [ ! -f assets/bin/mac/ffmpeg ]; then
    echo "Downloading ffmpeg..."
    curl -L -o /tmp/ffmpeg.zip "https://www.osxexperts.net/ffmpeg711arm.zip"
    unzip -o /tmp/ffmpeg.zip -d assets/bin/mac
    chmod +x assets/bin/mac/ffmpeg
fi
if [ ! -f assets/bin/mac/ffprobe ]; then
    echo "Downloading ffprobe..."
    curl -L -o /tmp/ffprobe.zip "https://www.osxexperts.net/ffprobe711arm.zip"
    unzip -o /tmp/ffprobe.zip -d assets/bin/mac
    chmod +x assets/bin/mac/ffprobe
fi
if [ ! -f assets/bin/mac/yt-dlp ]; then
    echo "Downloading yt-dlp..."
    curl -L -o assets/bin/mac/yt-dlp "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
    chmod +x assets/bin/mac/yt-dlp
fi

# Inter font
if [ ! -f assets/fonts/Inter-Bold.ttf ]; then
    curl -L -o /tmp/inter.zip "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip"
    unzip -o -j /tmp/inter.zip "Inter Desktop/Inter-Bold.otf" -d assets/fonts/
    mv assets/fonts/Inter-Bold.otf assets/fonts/Inter-Bold.ttf
fi

uv run alembic upgrade head

echo "Install done. Run: uv run python -m youtok.cli hello"

#!/usr/bin/env bash
# Test script: channel scraping via yt-dlp --flat-playlist
# No worker needed — this tests the enumerate_channel function directly.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONIOENCODING=utf-8

echo "=== Acceptance Test: Channel scraping (5 videos) ==="
echo ""

# Use a small, stable channel — Veritasium (popular, always available)
uv run python -c "
from youtok.core.channel import enumerate_channel, ChannelFilters

filters = ChannelFilters(limit=5)
videos = enumerate_channel('https://youtube.com/@veritasium', filters)

print(f'Got {len(videos)} videos:')
print()
for v in videos:
    dur = f'{v.duration_sec/60:.0f}min' if v.duration_sec else '? min'
    print(f'  [{v.video_id}] {v.title[:60]}  ({dur})')

assert len(videos) > 0, 'Expected at least 1 video'
print()
print('✓ Channel scraping test passed!')
"

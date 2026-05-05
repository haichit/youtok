$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv venv --python 3.11
uv pip install -e ".[dev]"

New-Item -ItemType Directory -Force -Path assets\bin\win, assets\keys, assets\fonts, data

if (-not (Test-Path assets\bin\win\ffmpeg.exe)) {
    Write-Host "Downloading ffmpeg..."
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $env:TEMP\ffmpeg.zip
    Expand-Archive -Path $env:TEMP\ffmpeg.zip -DestinationPath $env:TEMP\ffmpeg-extract -Force
    $bin = Get-ChildItem -Path $env:TEMP\ffmpeg-extract -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    Copy-Item $bin.FullName assets\bin\win\ffmpeg.exe
    Copy-Item ($bin.DirectoryName + "\ffprobe.exe") assets\bin\win\ffprobe.exe
}

if (-not (Test-Path assets\bin\win\yt-dlp.exe)) {
    Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile assets\bin\win\yt-dlp.exe
}

if (-not (Test-Path assets\fonts\Inter-Bold.ttf)) {
    Invoke-WebRequest -Uri "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" -OutFile $env:TEMP\inter.zip
    Expand-Archive -Path $env:TEMP\inter.zip -DestinationPath $env:TEMP\inter-extract -Force
    Copy-Item "$env:TEMP\inter-extract\Inter Desktop\Inter-Bold.otf" assets\fonts\Inter-Bold.ttf
}

uv run alembic upgrade head

Write-Host "Install done. Run: uv run python -m youtok.cli hello"

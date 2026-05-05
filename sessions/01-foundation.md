# Session 01 — Foundation

> Run this **first**. Sessions 02/03/04 depend on it.

## Goal

Set up project skeleton: monorepo deps, DB schema + migrations, config, license module skeleton, cross-platform install scripts. Make `python -m youtok.cli hello` print "ok".

## Prerequisites

- Python 3.11+
- `uv` package manager (`brew install uv` or `pipx install uv`)
- Mac (dev) — same code must run on Windows production

## Read first

- `../SPEC.md` sections 0–6 (identity, architecture, folder, DB)
- `../SPEC.md` section 10 (license)
- `../SPEC.md` section 14 (cross-platform)

## Deliverables

### 1. `pyproject.toml`

```toml
[project]
name = "youtok"
version = "0.1.0"
description = "YouTube → 9:16 short-form clip cutter"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "huey>=2.5",
    "anthropic>=0.39",
    "yt-dlp>=2024.10.7",
    "ffmpeg-python>=0.2",
    "scenedetect[opencv]>=0.6",
    "pysubs2>=1.7",
    "nltk>=3.9",
    "loguru>=0.7",
    "jinja2>=3.1",
    "python-multipart>=0.0.12",
    "httpx>=0.27",
    "cryptography>=43",
    "websockets>=13",
]

[project.optional-dependencies]
gpu = [
    "torch>=2.5",
    "torchaudio>=2.5",
    "faster-whisper>=1.0.3",
    "whisperx>=3.1",
]
cpu = [
    "torch>=2.5",
    "torchaudio>=2.5",
    "faster-whisper>=1.0.3",
    "whisperx>=3.1",
]
dev = [
    "pytest>=8",
    "ruff>=0.7",
    "pyright>=1.1",
]

[project.scripts]
youtok = "youtok.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

### 2. `.env.example`

```
# Anthropic API
ANTHROPIC_API_KEY=

# Server
HOST=127.0.0.1
PORT=8000

# Paths (auto-detected, override only if needed)
# DATA_DIR=./data
# WORKDIR=./data/workdir
# ASSETS_DIR=./assets

# Logging
LOG_LEVEL=INFO

# Pipeline tuning
MIN_CLIP_DURATION_SEC=60
MAX_CLIP_DURATION_SEC=240
PAUSE_THRESHOLD_SEC=0.3
SNAP_WINDOW_SEC=2.0

# WhisperX
WHISPER_DEVICE=auto    # auto | cuda | cpu
WHISPER_MODEL=auto     # auto | base | small | medium | large-v3
```

### 3. `src/youtok/config.py`

```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Anthropic
    anthropic_api_key: str = ""

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # Paths
    base_dir: Path = Path(__file__).parent.parent.parent
    data_dir: Path = Path(__file__).parent.parent.parent / "data"
    workdir: Path = Path(__file__).parent.parent.parent / "data" / "workdir"
    assets_dir: Path = Path(__file__).parent.parent.parent / "assets"

    # Logging
    log_level: str = "INFO"

    # Pipeline
    min_clip_duration_sec: int = 60
    max_clip_duration_sec: int = 240
    pause_threshold_sec: float = 0.3
    snap_window_sec: float = 2.0

    # WhisperX
    whisper_device: str = "auto"
    whisper_model: str = "auto"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def queue_db_path(self) -> Path:
        return self.data_dir / "queue.db"

    @property
    def license_cache_path(self) -> Path:
        return self.data_dir / "license.json"

    @property
    def public_key_path(self) -> Path:
        return self.assets_dir / "keys" / "public_key.pem"

    @property
    def fonts_dir(self) -> Path:
        return self.assets_dir / "fonts"

    @property
    def bin_dir(self) -> Path:
        import platform
        sub = "mac" if platform.system() == "Darwin" else "win"
        return self.assets_dir / "bin" / sub

    @property
    def ffmpeg(self) -> Path:
        ext = ".exe" if self.bin_dir.name == "win" else ""
        return self.bin_dir / f"ffmpeg{ext}"

    @property
    def ffprobe(self) -> Path:
        ext = ".exe" if self.bin_dir.name == "win" else ""
        return self.bin_dir / f"ffprobe{ext}"

    @property
    def ytdlp(self) -> Path:
        ext = ".exe" if self.bin_dir.name == "win" else ""
        return self.bin_dir / f"yt-dlp{ext}"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.workdir.mkdir(parents=True, exist_ok=True)
```

### 4. `src/youtok/db/`

`base.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from youtok.config import settings

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
# Enable WAL for concurrent server + worker access
with engine.connect() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

`models.py` — implement the 3 tables from SPEC §6 (License, Job, Clip) using SQLAlchemy 2.0 Declarative.

`crud.py` — basic create/read functions for each model.

### 5. `alembic/` setup

```bash
uv run alembic init alembic
```

Edit `alembic/env.py`:

```python
from youtok.config import settings
from youtok.db.base import Base
from youtok.db.models import License, Job, Clip  # noqa

config.set_main_option("sqlalchemy.url", settings.db_url)
target_metadata = Base.metadata
```

Generate migration:

```bash
uv run alembic revision --autogenerate -m "init schema"
uv run alembic upgrade head
```

### 6. License module skeleton

`src/youtok/license/machine_id.py`:

```python
import hashlib
import platform
import subprocess


def get_machine_id() -> str:
    sys = platform.system()
    if sys == "Darwin":
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            text=True,
        )
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                uuid = line.split('"')[3]
                return hashlib.sha256(uuid.encode()).hexdigest()[:16]
    elif sys == "Windows":
        out = subprocess.check_output(
            ["wmic", "csproduct", "get", "UUID"],
            text=True,
        )
        uuid = out.split("\n")[1].strip()
        return hashlib.sha256(uuid.encode()).hexdigest()[:16]
    raise RuntimeError(f"Unsupported platform: {sys}")
```

`src/youtok/license/manager.py` — skeleton functions:

```python
def verify_key(key: str) -> dict:
    """Decode + verify signature. Raise InvalidLicense on failure."""
    ...

def activate(key: str, db: Session) -> License:
    """Verify, bind to machine_id, save to DB + license.json."""
    ...

def is_activated() -> bool:
    """Check data/license.json exists + DB row valid + machine matches."""
    ...
```

Implement `verify_key` using `cryptography.hazmat.primitives.asymmetric.padding.PSS` + SHA256.

`src/youtok/license/keygen.py` — admin CLI:

```python
import click
import json, base64, datetime, uuid
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes


@click.command()
@click.option("--private-key", type=click.Path(exists=True), required=True)
@click.option("--email", required=True)
@click.option("--expires", type=str, default=None, help="YYYY-MM-DD or empty for perpetual")
@click.option("--max-jobs-per-day", type=int, default=None)
@click.option("--features", default="base")
def main(private_key, email, expires, max_jobs_per_day, features):
    payload = {
        "v": 1,
        "kid": uuid.uuid4().hex,
        "email": email,
        "iat": datetime.datetime.utcnow().isoformat() + "Z",
        "exp": (expires + "T00:00:00Z") if expires else None,
        "max_jobs_per_day": max_jobs_per_day,
        "features": features.split(","),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    
    with open(private_key, "rb") as f:
        priv = load_pem_private_key(f.read(), password=None)
    
    sig = priv.sign(
        payload_bytes,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    
    p_b32 = base64.b32encode(payload_bytes).decode().rstrip("=")
    s_b32 = base64.b32encode(sig).decode().rstrip("=")
    print(f"YOUTOK-{p_b32}-{s_b32}")


if __name__ == "__main__":
    main()
```

### 7. CLI entry

`src/youtok/cli.py`:

```python
import click


@click.group()
def main():
    pass


@main.command()
def hello():
    """Sanity check."""
    from youtok.config import settings
    print(f"ok | data_dir={settings.data_dir} | bin_dir={settings.bin_dir}")


if __name__ == "__main__":
    main()
```

### 8. Install scripts

`scripts/install-mac.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

uv venv --python 3.11
uv pip install -e ".[cpu,dev]"

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
```

`scripts/install-win.ps1`:

```powershell
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv venv --python 3.11
uv pip install -e ".[cpu,dev]"

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
```

`scripts/run-server.sh`, `run-server.ps1`, `run-worker.sh`, `run-worker.ps1` — minimal stubs that will be implemented in sessions 03+04. For now just print "TODO".

### 9. `.gitignore`

```
__pycache__/
*.pyc
.venv/
.env
data/
!data/.gitkeep
*.db
*.db-shm
*.db-wal
.DS_Store
.pytest_cache/
.ruff_cache/
private_key.pem
*.log
```

## Acceptance test

```bash
cd projects/youtok
./scripts/install-mac.sh
uv run python -m youtok.cli hello
# expected: ok | data_dir=... | bin_dir=.../mac

uv run alembic current
# expected: head migration applied

ls data/app.db
# exists, size > 0

uv run python -c "from youtok.db.models import License, Job, Clip; print('models ok')"
# expected: models ok
```

## Anti-patterns to avoid

- Hardcoding `/` paths — always `pathlib.Path`.
- Putting `private_key.pem` in repo (committed). Only `public_key.pem`.
- Using `os.environ` directly instead of `settings`.
- Forgetting WAL mode → server + worker will deadlock on writes.
- Skipping `encoding="utf-8"` on `subprocess.run` — Windows default is cp1252.

## When done

Notify next session with:

- Path to repo
- `uv run alembic current` output
- DB schema applied successfully

Then sessions 02, 03, 04 can run **in parallel** in separate Claude Code windows.

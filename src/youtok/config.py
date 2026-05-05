from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BASE / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

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

    # LLM
    use_batch_api: bool = False
    batch_min_requests: int = 3
    batch_timeout_sec: int = 600

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

    def _resolve_bin(self, name: str) -> Path:
        ext = ".exe" if self.bin_dir.name == "win" else ""
        bundled = self.bin_dir / f"{name}{ext}"
        if bundled.exists():
            return bundled
        import shutil
        found = shutil.which(name)
        if found:
            return Path(found)
        return bundled

    @property
    def ffmpeg(self) -> Path:
        return self._resolve_bin("ffmpeg")

    @property
    def ffprobe(self) -> Path:
        return self._resolve_bin("ffprobe")

    @property
    def ytdlp(self) -> Path:
        return self._resolve_bin("yt-dlp")


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.workdir.mkdir(parents=True, exist_ok=True)

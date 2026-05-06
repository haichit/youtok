import sys

# Windowed PyInstaller builds (console=False on Windows) leave
# sys.stdout / sys.stderr as None. uvicorn's ColourizedFormatter calls
# sys.stdout.isatty() during config and click also writes to stdout —
# both crash on None. Give them a real (no-op) file object before any
# other import touches them.
if sys.stdout is None or sys.stderr is None:
    import os as _os
    _devnull = open(_os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stderr is None:
        sys.stderr = _devnull

# When the host exe is windowed (console=False), every subprocess we
# spawn — yt-dlp, ffmpeg, ffprobe, wmic, scenedetect, whisper — is a
# console app and Windows pops up a black cmd window for each one.
# Patch Popen.__init__ so calls without an explicit creationflags get
# CREATE_NO_WINDOW. subprocess.run/check_output/check_call all go
# through Popen, so this covers every call site in the codebase.
if sys.platform == "win32":
    import subprocess as _sp
    _orig_popen_init = _sp.Popen.__init__

    def _popen_no_window(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = _sp.CREATE_NO_WINDOW
        return _orig_popen_init(self, *args, **kwargs)

    _sp.Popen.__init__ = _popen_no_window

import click
from loguru import logger


def _setup_crash_log():
    """Write all output to error.log next to the exe so crashes are debuggable."""
    if not getattr(sys, "frozen", False):
        return
    import os
    log_path = os.path.join(os.path.dirname(sys.executable), "error.log")
    logger.add(log_path, rotation="1 MB", retention=3)
    sys.stderr = open(log_path, "a")


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    _setup_crash_log()
    if ctx.invoked_subcommand is None:
        serve()


@main.command()
def hello():
    """Sanity check."""
    from youtok.config import settings
    print(f"ok | data_dir={settings.data_dir} | bin_dir={settings.bin_dir}")


@main.command()
@click.option("--url", required=True, help="YouTube video URL")
@click.option("--out", required=True, type=click.Path(), help="Output directory")
def run(url: str, out: str):
    """End-to-end pipeline test."""
    from pathlib import Path

    from youtok.db.base import Base, SessionLocal, engine
    from youtok.db.models import Job, License

    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        lic = db.query(License).first()
        if not lic:
            from datetime import datetime
            lic = License(
                key_hash="cli-test",
                email="cli@test",
                machine_id="cli-test",
                activated_at=datetime.utcnow(),
                status="active",
            )
            db.add(lic)
            db.commit()
            db.refresh(lic)

        job = Job(
            license_id=lic.id,
            source_type="video",
            source_url=url,
            output_dir=str(Path(out).resolve()),
            status="pending",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    finally:
        db.close()

    def progress(step: str, pct: int, message: str) -> None:
        logger.info(f"[{pct:3d}%] {step}: {message}")

    from youtok.core.pipeline import run_pipeline
    run_pipeline(job_id, progress)

    logger.info(f"Done! Output at: {out}")


@main.command()
@click.option("--url", required=True, help="YouTube video URL")
@click.option("--work-dir", required=True, type=click.Path(), help="Working directory")
def download(url: str, work_dir: str):
    """Download only (test downloader)."""
    from pathlib import Path
    from youtok.core.downloader import download_video

    result = download_video(url, Path(work_dir))
    print(f"Video: {result.video_path}")
    print(f"Audio: {result.audio_path}")
    print(f"Title: {result.title}")
    print(f"Duration: {result.duration_sec:.1f}s")


@main.command()
@click.option("--audio", required=True, type=click.Path(exists=True), help="WAV file path")
def transcribe_cmd(audio: str):
    """Transcribe only (test transcriber)."""
    from pathlib import Path
    from youtok.core.transcriber import transcribe

    result = transcribe(Path(audio))
    print(f"Language: {result.language}")
    print(f"Sentences: {len(result.sentences)}")
    for s in result.sentences[:10]:
        print(f"  {s.id} [{s.start:.1f}-{s.end:.1f}]: {s.text[:80]}")
    if len(result.sentences) > 10:
        print(f"  ... ({len(result.sentences) - 10} more)")


def _wait_for_server(host: str, port: int, timeout: float = 20.0) -> bool:
    """Block until the server accepts TCP connections, or until timeout."""
    import socket
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=17555, type=int)
@click.option("--no-window", is_flag=True, help="Run server only, open in default browser instead of a desktop window")
def serve(host: str = "127.0.0.1", port: int = 17555, no_window: bool = False):
    """Start web server + background worker + desktop window."""
    import subprocess
    import threading
    import traceback

    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        if getattr(sys, "frozen", False):
            worker_cmd = [sys.executable, "worker"]
        else:
            worker_cmd = [sys.executable, "-m", "youtok.cli", "worker"]

        worker_proc = subprocess.Popen(
            worker_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        import uvicorn
        config = uvicorn.Config(
            "youtok.api.main:app", host=host, port=port, log_level="warning"
        )
        server = uvicorn.Server(config)

        if no_window:
            import webbrowser

            def _open_browser():
                if _wait_for_server(host, port):
                    webbrowser.open(f"http://{host}:{port}")

            threading.Thread(target=_open_browser, daemon=True).start()
            try:
                server.run()
            finally:
                worker_proc.terminate()
            return

        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()

        if not _wait_for_server(host, port):
            raise RuntimeError(f"Server did not start on {host}:{port} within timeout")

        import webview

        webview.create_window(
            "Youtok",
            f"http://{host}:{port}",
            width=1280,
            height=800,
            min_size=(900, 600),
        )
        try:
            webview.start()
        finally:
            server.should_exit = True
            worker_proc.terminate()
    except Exception:
        logger.exception("Fatal error in serve")
        if getattr(sys, "frozen", False):
            traceback.print_exc()
        raise


@main.command()
def worker():
    """Run Huey consumer (internal use)."""
    from youtok.queue.huey_app import huey
    consumer = huey.create_consumer(workers=2, worker_type="thread")
    consumer.run()


if __name__ == "__main__":
    main()

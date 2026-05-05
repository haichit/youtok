import click
from loguru import logger


@click.group()
def main():
    pass


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


if __name__ == "__main__":
    main()

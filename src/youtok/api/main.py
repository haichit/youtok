import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from youtok.api.routes import activate, channels, jobs, pages
from youtok.api.ws import progress_watcher, register_ws
from youtok.db.base import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed_mock_data()
    task = asyncio.create_task(progress_watcher())
    yield
    task.cancel()


def _seed_mock_data():
    from youtok.db.base import SessionLocal
    from youtok.db.models import Job, License

    with SessionLocal() as db:
        if db.query(Job).count() > 0:
            return
        lic = db.query(License).filter(License.status == "active").first()
        if not lic:
            return
        from datetime import datetime
        mock_jobs = [
            Job(
                license_id=lic.id,
                source_type="video",
                source_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir="/tmp/youtok/output1",
                status="done",
                progress_pct=100,
                current_step=None,
                video_title="How Neural Networks Actually Work",
                video_duration_sec=842.0,
                clips_count=5,
                created_at=datetime(2026, 5, 4, 10, 30),
                started_at=datetime(2026, 5, 4, 10, 31),
                finished_at=datetime(2026, 5, 4, 10, 45),
            ),
            Job(
                license_id=lic.id,
                source_type="video",
                source_url="https://youtube.com/watch?v=abc123",
                output_dir="/tmp/youtok/output2",
                status="downloading",
                progress_pct=12,
                current_step="downloading",
                video_title="Explaining Transformers in 15 Minutes",
                video_duration_sec=920.0,
                clips_count=0,
                created_at=datetime(2026, 5, 5, 9, 0),
                started_at=datetime(2026, 5, 5, 9, 1),
            ),
            Job(
                license_id=lic.id,
                source_type="video",
                source_url="https://youtube.com/watch?v=xyz789",
                output_dir="/tmp/youtok/output3",
                status="failed",
                progress_pct=42,
                current_step="segmenting",
                error_message="Anthropic API rate limit exceeded. Retry in 60s.",
                video_title="The Hidden Math Behind GPS",
                video_duration_sec=780.0,
                clips_count=0,
                created_at=datetime(2026, 5, 4, 14, 0),
                started_at=datetime(2026, 5, 4, 14, 1),
                finished_at=datetime(2026, 5, 4, 14, 10),
            ),
        ]
        for j in mock_jobs:
            db.add(j)
        db.commit()


def create_app() -> FastAPI:
    app = FastAPI(title="Youtok", lifespan=lifespan)

    app.mount("/static", StaticFiles(directory="src/youtok/web/static"), name="static")

    app.include_router(pages.router)
    app.include_router(activate.router, prefix="/activate")
    app.include_router(jobs.router, prefix="/jobs")
    app.include_router(channels.router, prefix="/channels")
    register_ws(app)

    return app


app = create_app()

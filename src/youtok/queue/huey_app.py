from huey import SqliteHuey

from youtok.config import settings

huey = SqliteHuey(
    name="youtok",
    filename=str(settings.queue_db_path),
    immediate=False,
    results=False,
    store_none=False,
)

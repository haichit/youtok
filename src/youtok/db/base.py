from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from youtok.config import settings

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
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

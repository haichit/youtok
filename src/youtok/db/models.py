from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from youtok.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    provider = Column(String(20), unique=True, nullable=False)
    key = Column(Text, nullable=False)
    stage_a_model = Column(String(100), nullable=True)
    stage_b_model = Column(String(100), nullable=True)
    last_validated = Column(DateTime, nullable=True)
    last_validation_status = Column(String(20), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    key_hash = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False)
    machine_id = Column(String, nullable=False)
    activated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    max_jobs_per_day = Column(Integer, nullable=True)
    features_json = Column(Text, nullable=False, default='["base"]')
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    jobs = relationship("Job", back_populates="license")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False)
    parent_job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    source_type = Column(String, nullable=False, default="video")
    source_url = Column(String, nullable=False)
    output_dir = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    progress_pct = Column(Integer, nullable=False, default=0)
    current_step = Column(String, nullable=True)
    config_json = Column(Text, nullable=False, default="{}")
    error_message = Column(Text, nullable=True)
    video_title = Column(String, nullable=True)
    video_duration_sec = Column(Float, nullable=True)
    clips_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    license = relationship("License", back_populates="jobs")
    parent_job = relationship("Job", remote_side=[id], backref="child_jobs")
    clips = relationship("Clip", back_populates="job")


class Clip(Base):
    __tablename__ = "clips"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    part_number = Column(Integer, nullable=False)
    total_parts = Column(Integer, nullable=False)
    topic_name = Column(String, nullable=False)
    parent_topic = Column(String, nullable=True)
    start_sec = Column(Float, nullable=False)
    end_sec = Column(Float, nullable=False)
    duration_sec = Column(Float, nullable=False)
    output_path = Column(String, nullable=False)
    coherence_score = Column(Float, nullable=False, default=0.0)
    warnings_json = Column(Text, nullable=False, default="[]")
    transcript_text = Column(Text, nullable=False, default="")
    sentence_range_start = Column(String, nullable=False)
    sentence_range_end = Column(String, nullable=False)

    job = relationship("Job", back_populates="clips")


class DriveToken(Base):
    __tablename__ = "drive_tokens"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False)
    token_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class DriveUpload(Base):
    __tablename__ = "drive_uploads"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    progress_pct = Column(Integer, nullable=False, default=0)
    current_file = Column(String, nullable=True)
    drive_folder_id = Column(String, nullable=True)
    drive_folder_url = Column(String, nullable=True)
    files_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    job = relationship("Job")


class Logo(Base):
    __tablename__ = "logos"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    top_file_path = Column(String, nullable=False)
    bottom_file_path = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, JSON, Text, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from .config import settings, VideoStatus

Base = declarative_base()

class Video(Base):
    __tablename__ = "videos"

    id = Column(String, primary_key=True)  # UUID
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    duration = Column(Float)
    fps = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship to processing results
    processing_results = relationship("ProcessingResult", back_populates="video", cascade="all, delete-orphan")

class ProcessingResult(Base):
    __tablename__ = "processing_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String, ForeignKey("videos.id"), nullable=False)
    config_hash = Column(String, nullable=False)
    
    # Configuration Snapshot (embedding model is fixed system-wide)
    speech_model = Column(String, nullable=False)
    vision_model = Column(String, nullable=False)
    frame_interval = Column(Integer, nullable=False)
    
    # Status for this specific config
    status = Column(String, default=VideoStatus.QUEUED.value)
    error_message = Column(Text, nullable=True)
    
    # Artifact Paths
    transcript_path = Column(String, nullable=True)
    captions_path = Column(String, nullable=True)
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Ensure one result per video + config
    __table_args__ = (
        UniqueConstraint('video_id', 'config_hash', name='uix_video_config'),
    )
    
    video = relationship("Video", back_populates="processing_results")

# Database Setup
engine = create_engine(settings.DB_PATH, connect_args={"check_same_thread": False})

# Enable WAL Mode for better concurrency
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

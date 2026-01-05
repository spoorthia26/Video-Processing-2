import time
import shutil
import uuid
import logging
import ffmpeg
from pathlib import Path
from sqlalchemy.orm import Session
from agents.common.config import settings, VideoStatus
from agents.common.database import SessionLocal, Video, ProcessingResult, init_db

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IngestionAgent")

def get_video_metadata(file_path: Path):
    """Extracts metadata using ffmpeg-python."""
    try:
        probe = ffmpeg.probe(str(file_path))
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if not video_stream:
            return None
        
        return {
            "duration": float(probe['format']['duration']),
            "width": int(video_stream['width']),
            "height": int(video_stream['height']),
            "fps": eval(video_stream['r_frame_rate']) if '/' in video_stream['r_frame_rate'] else float(video_stream['r_frame_rate'])
        }
    except Exception as e:
        logger.error(f"Error probing file {file_path}: {e}")
        return None

def process_inbox():
    """Scans inbox, validates, moves files, and updates DB."""
    db: Session = SessionLocal()
    
    # Ensure DB is initialized
    init_db()
    
    for file_path in settings.INBOX_DIR.glob("*"):
        if file_path.is_file() and file_path.suffix.lower() in ['.mp4', '.mkv', '.mov', '.avi']:
            logger.info(f"Found new file: {file_path.name}")
            
            # 1. Validate & Extract Metadata
            metadata = get_video_metadata(file_path)
            if not metadata:
                logger.warning(f"Invalid video file: {file_path.name}. Skipping.")
                continue
            
            # 2. Generate ID and Move File
            # Check if file with same name already exists in DB to avoid duplicates
            existing_video = db.query(Video).filter(Video.filename == file_path.name).first()
            
            if existing_video:
                logger.info(f"Video {file_path.name} already exists. Skipping ingestion.")
                # Optionally move to a 'processed' folder or delete
                # os.remove(file_path) 
                continue

            video_id = str(uuid.uuid4())
            new_filename = f"{video_id}{file_path.suffix}"
            destination = settings.STORAGE_DIR / new_filename
            
            try:
                # ═══════════════════════════════════════════════════════════
                # FIX #1: COMMIT-FIRST PATTERN - Create DB entries BEFORE moving file
                # This prevents "ghost files" if commit fails after move
                # ═══════════════════════════════════════════════════════════
                
                # Step 1: Create Video entry FIRST (file not moved yet)
                video_entry = Video(
                    id=video_id,
                    filename=file_path.name,  # Original name
                    file_path=str(destination),  # Target path (file not there yet)
                    duration=metadata['duration'],
                    fps=metadata['fps'],
                    width=metadata['width'],
                    height=metadata['height']
                )
                db.add(video_entry)
                
                # ═══════════════════════════════════════════════════════════
                # FIX #2: EAGER INIT - Create ProcessingResult during ingestion
                # This ensures processor can pick up jobs without dashboard visit
                # ═══════════════════════════════════════════════════════════
                current_config_hash = settings.get_config_hash()
                processing_result = ProcessingResult(
                    video_id=video_id,
                    config_hash=current_config_hash,
                    speech_model=str(settings.ACTIVE_SPEECH_MODEL.value),
                    vision_model=str(settings.ACTIVE_VISION_MODEL.value),
                    frame_interval=settings.FRAME_INTERVAL,
                    status=VideoStatus.QUEUED.value
                )
                db.add(processing_result)
                
                # Step 2: Commit BOTH records atomically
                db.commit()
                logger.info(f"DB committed for video: {file_path.name} (ID: {video_id})")
                
                # Step 3: Move file AFTER successful commit
                try:
                    shutil.move(str(file_path), str(destination))
                    logger.info(f"Ingested and queued: {file_path.name} (ID: {video_id}, Config: {current_config_hash[:8]}...)")
                except Exception as move_error:
                    # File move failed - rollback DB entries to maintain consistency
                    logger.error(f"File move failed for {file_path.name}: {move_error}")
                    db.delete(processing_result)
                    db.delete(video_entry)
                    db.commit()
                    logger.info(f"Rolled back DB entries for {file_path.name}")
                    continue
                
            except Exception as e:
                logger.error(f"Failed to create DB entry for {file_path.name}: {e}")
                db.rollback()
                # File still in inbox - will be retried on next cycle

    db.close()

if __name__ == "__main__":
    logger.info("Starting Ingestion Agent...")
    while True:
        try:
            process_inbox()
        except Exception as e:
            logger.error(f"Ingestion loop error: {e}")
        time.sleep(5)

import time
import shutil
import uuid
import logging
import ffmpeg
from pathlib import Path
from sqlalchemy.orm import Session
from agents.common.config import settings, VideoStatus
from agents.common.database import SessionLocal, Video, init_db

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
                shutil.move(str(file_path), str(destination))
                logger.info(f"Moved file to: {destination}")
                
                # 3. Create DB Entry
                video_entry = Video(
                    id=video_id,
                    filename=file_path.name, # Original name
                    file_path=str(destination),
                    duration=metadata['duration'],
                    fps=metadata['fps'],
                    width=metadata['width'],
                    height=metadata['height']
                    # Status is now tracked in ProcessingResult, which is created by the Processor
                )
                db.add(video_entry)
                db.commit()
                logger.info(f"Ingested video: {file_path.name} (ID: {video_id})")
                
            except Exception as e:
                logger.error(f"Failed to process {file_path.name}: {e}")
                db.rollback()

    db.close()

if __name__ == "__main__":
    logger.info("Starting Ingestion Agent...")
    while True:
        try:
            process_inbox()
        except Exception as e:
            logger.error(f"Ingestion loop error: {e}")
        time.sleep(5)

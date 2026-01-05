import time
import json
import logging
import cv2
import torch
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import func
from agents.common.config import settings, VideoStatus, Settings
from agents.common.database import SessionLocal, Video, ProcessingResult
from agents.common.model_factory import ModelFactory

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ProcessorAgent")

# Track currently loaded models to enable context switching
_loaded_config_hash = None
_loaded_speech_model = None
_loaded_vision_model = None

def extract_frames(video_path: str, interval: int = 5):
    """Generator that yields (timestamp, frame_image) every `interval` seconds."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    if not fps or fps <= 0:
        logger.error(f"Could not determine FPS for {video_path}")
        return

    frame_step = int(fps * interval)
    current_frame = 0
    
    while cap.isOpened():
        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
        ret, frame = cap.read()
        if not ret:
            break
            
        timestamp = current_frame / fps
        # Convert BGR to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        yield timestamp, frame_rgb
        
        current_frame += frame_step
    
    cap.release()

def process_video(video: Video, result: ProcessingResult, db: Session):
    """
    Process a video for a specific configuration stored in the ProcessingResult.
    Uses the models specified in `result` (not the global settings).
    """
    global _loaded_config_hash, _loaded_speech_model, _loaded_vision_model
    
    logger.info(f"Processing video: {video.filename} ({video.id}) for config {result.config_hash}")
    logger.info(f"  -> Speech: {result.speech_model}, Vision: {result.vision_model}, Embed: {settings.FIXED_EMBEDDING_MODEL} (fixed)")
    
    try:
        # Update status
        result.status = VideoStatus.PROCESSING.value
        db.commit()
        
        # --- 1. Audio Transcription ---
        transcript_data = []
        # Check for reuse: another result with same speech model that's already completed
        existing_transcript = db.query(ProcessingResult).filter(
            ProcessingResult.video_id == video.id,
            ProcessingResult.speech_model == result.speech_model,
            ProcessingResult.status == VideoStatus.COMPLETED.value,
            ProcessingResult.transcript_path.isnot(None)
        ).first()

        if existing_transcript and Path(existing_transcript.transcript_path).exists():
            logger.info(f"Reusing transcript from result {existing_transcript.id}")
            with open(existing_transcript.transcript_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                transcript_data = data.get("transcript", [])
        else:
            logger.info(f"Starting Transcription with model: {result.speech_model}")
            # Load speech model for this config (context switch if needed)
            if _loaded_speech_model != result.speech_model:
                logger.info(f"[Context Switch] Loading speech model: {result.speech_model}")
                _loaded_speech_model = result.speech_model
            
            speech_model = ModelFactory.get_speech_model(result.speech_model)
            segments, _ = speech_model.transcribe(video.file_path, beam_size=5)
            
            for segment in segments:
                transcript_data.append({
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "type": "audio"
                })
            
        # --- 2. Visual Captioning ---
        captions_data = []
        # Check for reuse (Vision model + Frame Interval must match)
        existing_captions = db.query(ProcessingResult).filter(
            ProcessingResult.video_id == video.id,
            ProcessingResult.vision_model == result.vision_model,
            ProcessingResult.frame_interval == result.frame_interval,
            ProcessingResult.status == VideoStatus.COMPLETED.value,
            ProcessingResult.captions_path.isnot(None)
        ).first()
        
        if existing_captions and existing_captions.captions_path and Path(existing_captions.captions_path).exists():
            logger.info(f"Reusing captions from result {existing_captions.id}")
            with open(existing_captions.captions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                captions_data = data.get("captions", [])
        else:
            logger.info(f"Starting Visual Analysis with model: {result.vision_model}")
            # Load vision model for this config (context switch if needed)
            if _loaded_vision_model != result.vision_model:
                logger.info(f"[Context Switch] Loading vision model: {result.vision_model}")
                _loaded_vision_model = result.vision_model
            
            vision_instance = ModelFactory.get_vision_model(result.vision_model)
            processor = vision_instance["processor"]
            model = vision_instance["model"]
            model_type = vision_instance["type"]
            
            for timestamp, frame in extract_frames(video.file_path, interval=settings.FRAME_INTERVAL):
                # Prepare inputs based on model type
                if model_type == "blip":
                    inputs = processor(images=frame, return_tensors="pt").to(model.device)
                    out = model.generate(**inputs)
                    caption = processor.decode(out[0], skip_special_tokens=True)
                elif model_type == "florence":
                    # Florence-2 specific prompt
                    prompt = "<MORE_DETAILED_CAPTION>"
                    inputs = processor(text=prompt, images=frame, return_tensors="pt").to(model.device)
                    generated_ids = model.generate(
                        input_ids=inputs["input_ids"],
                        pixel_values=inputs["pixel_values"],
                        max_new_tokens=1024,
                        do_sample=False,
                        num_beams=3,
                    )
                    generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
                    parsed_answer = processor.post_process_generation(generated_text, task=prompt, image_size=(frame.shape[1], frame.shape[0]))
                    caption = parsed_answer[prompt]
                elif model_type == "qwen":
                    # Simplified Qwen inference
                    text = "Describe this video frame."
                    messages = [
                        {"role": "user", "content": [{"type": "image", "image": frame}, {"type": "text", "text": text}]}
                    ]
                    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                    inputs = processor(text=[text], images=[frame], padding=True, return_tensors="pt").to(model.device)
                    generated_ids = model.generate(**inputs, max_new_tokens=128)
                    caption = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
                else:
                    caption = "Model not supported"

                captions_data.append({
                    "timestamp": timestamp,
                    "caption": caption,
                    "type": "visual"
                })
            
        # --- 3. Save Results ---
        full_result = {
            "video_id": video.id,
            "config_hash": result.config_hash,
            "transcript": transcript_data,
            "captions": captions_data
        }
        
        # Unique filename for this config result
        output_filename = f"{video.id}_{result.config_hash}.json"
        output_path = settings.STORAGE_DIR / output_filename
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(full_result, f, indent=2)
            
        result.transcript_path = str(output_path)
        result.captions_path = str(output_path)  # Storing same path for now as they are combined
        result.status = VideoStatus.COMPLETED.value  # COMPLETED signals ready for embedding
        db.commit()
        logger.info(f"DB COMMITTED: Status set to COMPLETED for video_id={video.id}, config_hash={result.config_hash}")
        logger.info(f"Finished processing {video.filename} for config {result.config_hash}")
        
    except Exception as e:
        logger.error(f"Processing failed for {video.filename}: {e}")
        result.status = VideoStatus.FAILED.value
        result.error_message = str(e)
        db.commit()

def run_processor():
    """
    Main processor loop that picks up ANY queued job from ANY configuration.
    
    **Multi-Config Support:**
    - Queries ProcessingResult for ANY row where status == 'queued'
    - Groups by config_hash to show what's pending
    - Processes jobs, loading appropriate models based on the job's stored config
    - Supports context switching between different model configurations
    """
    global _loaded_config_hash
    
    db: Session = SessionLocal()
    
    try:
        # 1. Log status of all queued jobs across ALL configurations
        queued_counts = db.query(
            ProcessingResult.config_hash,
            func.count(ProcessingResult.id).label('count')
        ).filter(
            ProcessingResult.status == VideoStatus.QUEUED.value
        ).group_by(ProcessingResult.config_hash).all()
        
        if queued_counts:
            logger.info("=== Queued Jobs by Configuration ===")
            for config_hash, count in queued_counts:
                logger.info(f"  Config {config_hash[:8]}...: {count} job(s)")
        
        # 2. Find next queued result (ANY configuration, oldest first)
        result_to_process = db.query(ProcessingResult).filter(
            ProcessingResult.status == VideoStatus.QUEUED.value
        ).order_by(ProcessingResult.id).first()  # FIFO order
        
        if result_to_process:
            video = db.query(Video).filter(Video.id == result_to_process.video_id).first()
            
            if video:
                # Check if we need to switch model context
                if _loaded_config_hash and _loaded_config_hash != result_to_process.config_hash:
                    logger.info(f"[Context Switch] Switching from config {_loaded_config_hash[:8]}... to {result_to_process.config_hash[:8]}...")
                    # Note: Models will be reloaded in process_video as needed
                
                _loaded_config_hash = result_to_process.config_hash
                process_video(video, result_to_process, db)
            else:
                logger.error(f"Video {result_to_process.video_id} not found for result {result_to_process.id}")
                result_to_process.status = VideoStatus.FAILED.value
                result_to_process.error_message = "Video record not found"
                db.commit()
        else:
            # No queued jobs anywhere, sleep
            logger.debug("No queued jobs. Sleeping...")
            time.sleep(5)
            
    except Exception as e:
        logger.error(f"Error in run_processor: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Starting Processor Agent (Multi-Config Mode)...")
    logger.info(f"DB Path: {settings.DB_PATH}")
    while True:
        try:
            run_processor()
        except Exception as e:
            logger.error(f"Processor loop error: {e}")
            time.sleep(5)

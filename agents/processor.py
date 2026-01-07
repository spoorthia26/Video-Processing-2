import time
import json
import logging
import cv2
import torch
import queue
import threading
import gc
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

# ═══════════════════════════════════════════════════════════
# FIX #5: ASYNC FRAME EXTRACTION - Producer-Consumer Pattern
# Prevents GPU starvation by prefetching frames while GPU processes
# ═══════════════════════════════════════════════════════════

def extract_frames(video_path: str, interval: int = 5):
    """
    Generator that yields (timestamp, frame_image) every `interval` seconds.
    OPTIMIZED: Uses sequential reading instead of seeking for better performance.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    if not fps or fps <= 0:
        logger.error(f"Could not determine FPS for {video_path}")
        cap.release()
        return

    frame_step = int(fps * interval)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    current_frame = 0
    frames_to_extract = []
    
    # Pre-calculate which frames we need
    while current_frame < total_frames:
        frames_to_extract.append(current_frame)
        current_frame += frame_step
    
    logger.info(f"Extracting {len(frames_to_extract)} frames from video (interval={interval}s)")
    
    # Sequential read with skip - much faster than seeking
    frame_idx = 0
    next_target_idx = 0
    
    while cap.isOpened() and next_target_idx < len(frames_to_extract):
        ret, frame = cap.read()
        if not ret:
            break
        
        if frame_idx == frames_to_extract[next_target_idx]:
            timestamp = frame_idx / fps
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            yield timestamp, frame_rgb
            next_target_idx += 1
        
        frame_idx += 1
    
    cap.release()

def extract_frames_async(video_path: str, interval: int = 5, buffer_size: int = 8):
    """
    Async frame extraction with prefetch buffer.
    GPU can process frames while CPU extracts the next batch.
    OPTIMIZED: Uses sequential reading instead of seeking.
    """
    frame_queue = queue.Queue(maxsize=buffer_size)
    stop_event = threading.Event()
    error_holder = [None]  # Mutable container to capture producer errors
    
    def producer():
        try:
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            
            if not fps or fps <= 0:
                error_holder[0] = f"Could not determine FPS for {video_path}"
                return
            
            frame_step = int(fps * interval)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            # Pre-calculate target frames
            frames_to_extract = set()
            current = 0
            while current < total_frames:
                frames_to_extract.add(current)
                current += frame_step
            
            logger.info(f"[Async] Extracting {len(frames_to_extract)} frames (interval={interval}s)")
            
            # Sequential read - MUCH faster than seeking for compressed video
            frame_idx = 0
            extracted_count = 0
            
            while cap.isOpened() and not stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    break
                
                if frame_idx in frames_to_extract:
                    timestamp = frame_idx / fps
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                    try:
                        frame_queue.put((timestamp, frame_rgb), timeout=30)
                        extracted_count += 1
                    except queue.Full:
                        logger.warning("Frame queue full, consumer may be slow")
                        break
                
                frame_idx += 1
            
            cap.release()
            logger.info(f"[Async] Extracted {extracted_count} frames")
        except Exception as e:
            error_holder[0] = str(e)
        finally:
            frame_queue.put(None)  # Sentinel to signal end
    
    # Start producer thread
    thread = threading.Thread(target=producer, daemon=True)
    thread.start()
    
    # Consumer yields from queue
    while True:
        try:
            item = frame_queue.get(timeout=60)
            if item is None:  # Sentinel received
                break
            if error_holder[0]:
                logger.error(f"Producer error: {error_holder[0]}")
                break
            yield item
        except queue.Empty:
            logger.warning("Frame queue timeout - producer may have stalled")
            break
    
    stop_event.set()
    thread.join(timeout=5)

def clear_gpu_memory():
    """Clear GPU memory after model unloading."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

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
            
            # OPTIMIZATION: Use VAD filter and larger chunks for faster transcription
            segments, _ = speech_model.transcribe(
                video.file_path, 
                beam_size=5,
                vad_filter=True,  # Skip silent parts
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            
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
            
            # FIX #5: Use async frame extraction to prevent GPU starvation
            frame_generator = extract_frames_async(video.file_path, interval=result.frame_interval)
            
            # OPTIMIZATION: Batch processing for BLIP model (significant speedup)
            if model_type == "blip":
                # Collect frames in batches for efficient GPU utilization
                BATCH_SIZE = 4  # Process 4 frames at once
                batch_frames = []
                batch_timestamps = []
                
                for timestamp, frame in frame_generator:
                    batch_frames.append(frame)
                    batch_timestamps.append(timestamp)
                    
                    if len(batch_frames) >= BATCH_SIZE:
                        # Process batch
                        inputs = processor(images=batch_frames, return_tensors="pt", padding=True).to(model.device)
                        with torch.no_grad():
                            outputs = model.generate(**inputs, max_new_tokens=50)
                        captions = processor.batch_decode(outputs, skip_special_tokens=True)
                        
                        for ts, cap in zip(batch_timestamps, captions):
                            captions_data.append({
                                "timestamp": ts,
                                "caption": cap,
                                "type": "visual"
                            })
                        
                        batch_frames = []
                        batch_timestamps = []
                
                # Process remaining frames
                if batch_frames:
                    inputs = processor(images=batch_frames, return_tensors="pt", padding=True).to(model.device)
                    with torch.no_grad():
                        outputs = model.generate(**inputs, max_new_tokens=50)
                    captions = processor.batch_decode(outputs, skip_special_tokens=True)
                    
                    for ts, cap in zip(batch_timestamps, captions):
                        captions_data.append({
                            "timestamp": ts,
                            "caption": cap,
                            "type": "visual"
                        })
            else:
                # Non-batched processing for other model types
                for timestamp, frame in frame_generator:
                    if model_type == "florence":
                        # Florence-2 specific prompt
                        prompt = "<MORE_DETAILED_CAPTION>"
                        inputs = processor(text=prompt, images=frame, return_tensors="pt").to(model.device)
                        with torch.no_grad():
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
                        with torch.no_grad():
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
    
    **FIX #3: CONFIG-BATCHED PROCESSING (Anti-Thrashing)**
    - If models are loaded for config X, process ALL config X jobs first
    - Only switch configs when current config queue is empty
    - When switching, prefer config with most queued jobs (efficiency)
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
        ).group_by(ProcessingResult.config_hash).order_by(
            func.count(ProcessingResult.id).desc()  # Most jobs first for efficiency
        ).all()
        
        if queued_counts:
            logger.info("=== Queued Jobs by Configuration ===")
            for config_hash, count in queued_counts:
                logger.info(f"  Config {config_hash[:8]}...: {count} job(s)")
        
        # ═══════════════════════════════════════════════════════════
        # FIX #3: CONFIG-BATCHED QUEUE - Minimize model thrashing
        # Strategy: Sticky sessions - process all jobs for current config first
        # ═══════════════════════════════════════════════════════════
        
        result_to_process = None
        
        # Step 1: If we have a loaded config, prioritize jobs for it (STICKY SESSION)
        if _loaded_config_hash:
            result_to_process = db.query(ProcessingResult).filter(
                ProcessingResult.status == VideoStatus.QUEUED.value,
                ProcessingResult.config_hash == _loaded_config_hash  # Prefer current config
            ).order_by(ProcessingResult.id).first()
            
            if result_to_process:
                logger.info(f"[Sticky Session] Found job for current config {_loaded_config_hash[:8]}...")
        
        # Step 2: No jobs for current config - find best config to switch to
        if not result_to_process and queued_counts:
            # Select config with most queued jobs (batch efficiency)
            target_config = queued_counts[0][0]
            job_count = queued_counts[0][1]
            
            if _loaded_config_hash and _loaded_config_hash != target_config:
                logger.info(f"[Config Switch] No more jobs for {_loaded_config_hash[:8]}...")
                logger.info(f"[Config Switch] Switching to config {target_config[:8]}... ({job_count} jobs queued)")
            
            result_to_process = db.query(ProcessingResult).filter(
                ProcessingResult.status == VideoStatus.QUEUED.value,
                ProcessingResult.config_hash == target_config
            ).order_by(ProcessingResult.id).first()
        
        if result_to_process:
            video = db.query(Video).filter(Video.id == result_to_process.video_id).first()
            
            if video:
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

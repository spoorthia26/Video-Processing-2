import time
import json
import logging
import uuid
import requests
from typing import List, Dict
from sqlalchemy.orm import Session
from agents.common.config import settings, VideoStatus
from agents.common.database import SessionLocal, Video, ProcessingResult
from agents.common.model_factory import ModelFactory

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EmbeddingAgent")

# NOTE: We do NOT initialize QdrantClient here to avoid file locking issues.
# We send vectors to the API instead.

def chunk_text(data: List[Dict], chunk_size: int = 30, overlap: int = 5):
    """
    Simple sliding window chunking for transcript/captions.
    Merges small segments into larger chunks.
    """
    chunks = []
    current_chunk = []
    current_length = 0
    
    for item in data:
        text = item.get("text") or item.get("caption")
        if not text:
            continue
            
        word_count = len(text.split())
        current_chunk.append(item)
        current_length += word_count
        
        if current_length >= chunk_size:
            # Create chunk
            chunk_text = " ".join([x.get("text") or x.get("caption") for x in current_chunk])
            start_time = current_chunk[0].get("start") or current_chunk[0].get("timestamp")
            end_time = current_chunk[-1].get("end") or current_chunk[-1].get("timestamp")
            
            chunks.append({
                "text": chunk_text,
                "start": start_time,
                "end": end_time,
                "source_items": current_chunk
            })
            
            # Overlap: Keep last few items
            # This is a simplified overlap logic
            current_chunk = current_chunk[-2:] if len(current_chunk) > 2 else []
            current_length = sum(len((x.get("text") or x.get("caption")).split()) for x in current_chunk)
            
    return chunks

def index_video(result: ProcessingResult, video: Video, db: Session):
    logger.info(f"Indexing video: {video.filename} for config {result.config_hash}")
    
    try:
        with open(result.transcript_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        transcript = data.get("transcript", [])
        captions = data.get("captions", [])
        
        # Combine and chunk
        chunks_to_index = []
        
        # 1. Chunk Transcript
        transcript_chunks = chunk_text(transcript)
        for chunk in transcript_chunks:
            chunk["type"] = "transcript"
            chunks_to_index.append(chunk)
            
        # 2. Chunk Captions
        caption_chunks = chunk_text(captions)
        for chunk in caption_chunks:
            chunk["type"] = "visual"
            chunks_to_index.append(chunk)
            
        if not chunks_to_index:
            logger.warning("No content to index.")
            result.status = VideoStatus.INDEXED.value
            db.commit()
            return

        # 3. Generate Embeddings (using fixed embedding model)
        texts = [c["text"] for c in chunks_to_index]
        logger.info(f"Using fixed embedding model: {settings.FIXED_EMBEDDING_MODEL}")
        embedding_model = ModelFactory.get_text_embedding_model(settings.FIXED_EMBEDDING_MODEL)
        embeddings = embedding_model.encode(texts)
        
        # 4. Upload to Qdrant via API
        chunks_payload = []
        for i, chunk in enumerate(chunks_to_index):
            chunks_payload.append({
                "id": str(uuid.uuid4()),
                "vector": embeddings[i].tolist(),
                "payload": {
                    "video_id": video.id,
                    "filename": video.filename,
                    "text": chunk["text"],
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "type": chunk["type"],
                    "config_hash": result.config_hash
                }
            })
            
        # Send to API
        api_url = f"http://127.0.0.1:{settings.API_PORT}/index"
        collection_name = f"video_rag_{result.config_hash}"
        
        try:
            response = requests.post(api_url, json={
                "chunks": chunks_payload,
                "collection_name": collection_name
            })
            response.raise_for_status()
            
            result.status = VideoStatus.INDEXED.value
            db.commit()
            logger.info(f"Successfully indexed {len(chunks_payload)} chunks for {video.filename} into {collection_name}")
            
        except requests.exceptions.ConnectionError:
            logger.error(f"Could not connect to API at {api_url}. Is it running?")
            return
        except Exception as e:
            logger.error(f"API Error during indexing: {e}")
            result.status = VideoStatus.FAILED.value
            result.error_message = f"API Indexing Error: {str(e)}"
            db.commit()

    except Exception as e:
        logger.error(f"Indexing failed for {video.filename}: {e}")
        result.status = VideoStatus.FAILED.value
        result.error_message = f"Indexing Error: {str(e)}"
        db.commit()

def run_embedding():
    """
    Main embedding loop - processes ANY completed result from ANY configuration.
    This enables multi-config support where different configs can be processed
    without requiring a restart of the embedding agent.
    """
    db: Session = SessionLocal()
    
    try:
        # Find ANY completed result that needs indexing (not config-specific)
        result = db.query(ProcessingResult).filter(
            ProcessingResult.status == VideoStatus.COMPLETED.value
        ).order_by(ProcessingResult.id).first()  # FIFO order
        
        if result:
            video = db.query(Video).filter(Video.id == result.video_id).first()
            if video:
                logger.info(f"[Multi-Config] Processing result for config_hash={result.config_hash[:8]}...")
                index_video(result, video, db)
            else:
                logger.error(f"Video {result.video_id} not found for result {result.id}")
                result.status = VideoStatus.FAILED.value
                result.error_message = "Video record not found"
                db.commit()
        else:
            time.sleep(5)
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Starting Embedding Agent...")
    while True:
        try:
            run_embedding()
        except Exception as e:
            logger.error(f"Embedding loop error: {e}")
            time.sleep(5)

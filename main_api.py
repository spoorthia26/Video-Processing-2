import uvicorn
import logging
import shutil
import uuid
import os
from fastapi import FastAPI, HTTPException, Depends, Query, UploadFile, File, Form, Response, Request, Header
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.concurrency import run_in_threadpool  # P0 Fix: Async file operations
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http import models
from pathlib import Path
import json

from agents.common.config import settings, VideoStatus, Settings
from agents.common.database import get_db, Video, ProcessingResult, init_db
# Lazy import ModelFactory only when needed for search (heavy ML dependencies)
# from agents.common.model_factory import ModelFactory

# Configure logging - WARNING level to reduce noise
logging.basicConfig(level=logging.WARNING, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger("VideoRAG-API")
logger.setLevel(logging.INFO)  # Keep API logger at INFO for important messages

# Suppress noisy uvicorn access logs (GET /videos polling)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = FastAPI(title="Video RAG API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy Qdrant Client initialization to avoid lock issues
_qdrant_client = None

def get_qdrant():
    """Get or create Qdrant client lazily."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(path=str(settings.QDRANT_PATH))
    return _qdrant_client

# Ensure DB exists on startup
@app.on_event("startup")
def startup_event():
    init_db()
    # Qdrant client will be initialized on first use
    
    # P2 Fix: Pre-load embedding model to eliminate cold-start latency on first search
    try:
        from agents.common.model_factory import ModelFactory
        logger.info("Pre-loading embedding model at startup...")
        ModelFactory.get_text_embedding_model(settings.FIXED_EMBEDDING_MODEL)
        logger.info(f"Embedding model '{settings.FIXED_EMBEDDING_MODEL}' pre-loaded successfully")
    except Exception as e:
        logger.warning(f"Failed to pre-load embedding model: {e}")
        # Non-fatal; model will load on first search request

def ensure_collection(collection_name: str):
    qdrant = get_qdrant()
    collections = qdrant.get_collections()
    exists = any(c.name == collection_name for c in collections.collections)
    if not exists:
        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE)
        )

# --- Pydantic Models ---
class VideoResponse(BaseModel):
    id: str
    filename: str
    status: str
    duration: Optional[float]
    created_at: str
    config_hash: Optional[str] = None

    class Config:
        from_attributes = True

class SearchResult(BaseModel):
    video_id: Optional[str] = None
    filename: Optional[str] = None
    text: Optional[str] = None
    start: Optional[float] = 0.0
    end: Optional[float] = 0.0
    score: float
    type: Optional[str] = None

class ChunkData(BaseModel):
    id: str
    vector: List[float]
    payload: dict

class IndexRequest(BaseModel):
    chunks: List[ChunkData]
    collection_name: str

class DebugDBResponse(BaseModel):
    video_count: int
    processing_result_count: int
    db_path: str
    queued_jobs: int
    processing_jobs: int
    completed_jobs: int

# --- Endpoints ---

@app.get("/debug/db", response_model=DebugDBResponse)
def debug_database(db: Session = Depends(get_db)):
    """
    Debug endpoint to verify database connectivity and row counts.
    Use this to diagnose "Ghost Data" issues.
    """
    video_count = db.query(func.count(Video.id)).scalar()
    result_count = db.query(func.count(ProcessingResult.id)).scalar()
    queued = db.query(func.count(ProcessingResult.id)).filter(ProcessingResult.status == VideoStatus.QUEUED.value).scalar()
    processing = db.query(func.count(ProcessingResult.id)).filter(ProcessingResult.status == VideoStatus.PROCESSING.value).scalar()
    completed = db.query(func.count(ProcessingResult.id)).filter(ProcessingResult.status == VideoStatus.COMPLETED.value).scalar()
    indexed = db.query(func.count(ProcessingResult.id)).filter(ProcessingResult.status == VideoStatus.INDEXED.value).scalar()
    
    logger.info(f"[DEBUG] DB Path: {settings.DB_PATH}")
    logger.info(f"[DEBUG] Videos: {video_count}, Results: {result_count}")
    logger.info(f"[DEBUG] Status: queued={queued}, processing={processing}, completed={completed}, indexed={indexed}")
    
    return DebugDBResponse(
        video_count=video_count,
        processing_result_count=result_count,
        db_path=settings.DB_PATH,
        queued_jobs=queued,
        processing_jobs=processing,
        completed_jobs=completed
    )

@app.get("/debug/video/{video_id}")
def debug_video_status(video_id: str, db: Session = Depends(get_db)):
    """Debug endpoint to check status of a specific video across all configs."""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    results = db.query(ProcessingResult).filter(ProcessingResult.video_id == video_id).all()
    
    return {
        "video": {
            "id": video.id,
            "filename": video.filename,
            "duration": video.duration
        },
        "results": [
            {
                "config_hash": r.config_hash,
                "status": r.status,
                "speech_model": r.speech_model,
                "vision_model": r.vision_model,
                "error_message": r.error_message
            }
            for r in results
        ]
    }

@app.post("/index")
def index_chunks(request: IndexRequest):
    """Internal endpoint for Embedding Process to push vectors."""
    if not request.chunks:
        return {"status": "empty"}
    
    ensure_collection(request.collection_name)
        
    points = [
        models.PointStruct(id=c.id, vector=c.vector, payload=c.payload)
        for c in request.chunks
    ]
    get_qdrant().upsert(collection_name=request.collection_name, points=points)
    return {"status": "ok", "count": len(points)}

@app.get("/videos")
def list_videos(
    response: Response,
    vision_model: Optional[str] = Query(None, description="Vision model name (e.g., 'Salesforce/blip-image-captioning-base')"),
    speech_model: Optional[str] = Query(None, description="Speech model name (e.g., 'base', 'large-v3')"),
    frame_interval: int = Query(5, description="Frame extraction interval in seconds"),
    skip: int = Query(0, description="Number of records to skip (for pagination)"),
    limit: int = Query(20, description="Maximum number of records to return (for pagination)"),
    db: Session = Depends(get_db)
):
    """
    List videos that have been explicitly processed for the specified configuration.
    
    **Strict Filtering:** Only returns videos with explicit ProcessingResult records 
    matching the selected configuration. No auto-creation of queued jobs.
    
    **P0 Fix:** Uses a single JOIN query for optimal performance.
    **P1 Fix:** Supports pagination via skip/limit parameters.
    
    Config hash depends on: speech_model + vision_model + frame_interval
    """
    # Determine target config hash
    if vision_model and speech_model:
        # Compute hash from Frontend-provided parameters (ensures exact match)
        target_config = Settings._compute_config_hash(speech_model, vision_model, frame_interval)
        logger.debug(f"[GET /videos] Using Frontend config: vision={vision_model}, speech={speech_model}, frame_interval={frame_interval}")
        logger.debug(f"[GET /videos] Computed config_hash: {target_config}")
    else:
        # Fallback to active .env configuration
        target_config = settings.get_config_hash()
        vision_model = str(settings.ACTIVE_VISION_MODEL.value)
        speech_model = str(settings.ACTIVE_SPEECH_MODEL.value)
        frame_interval = settings.FRAME_INTERVAL
        logger.debug(f"[GET /videos] Using default .env config_hash: {target_config}")
    
    # Strict filtering: Query ProcessingResult first, filter by config_hash, then join Video
    # This ensures we only return videos that have explicit ProcessingResult records
    total_count = db.query(func.count(ProcessingResult.id)).filter(
        ProcessingResult.config_hash == target_config
    ).scalar()
    
    # Add debug headers so frontend can verify hash synchronization
    response.headers["X-Debug-Config-Hash"] = target_config
    response.headers["X-Debug-Vision-Model"] = vision_model
    response.headers["X-Debug-Speech-Model"] = speech_model
    response.headers["X-Debug-Embedding-Model"] = settings.FIXED_EMBEDDING_MODEL
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Access-Control-Expose-Headers"] = "X-Debug-Config-Hash, X-Debug-Vision-Model, X-Debug-Speech-Model, X-Debug-Embedding-Model, X-Total-Count"
    
    # Strict filtering: Only return videos with explicit ProcessingResult for this config
    # Query ProcessingResult first and join with Video to get metadata
    results = db.query(ProcessingResult, Video).join(
        Video,
        Video.id == ProcessingResult.video_id
    ).filter(
        ProcessingResult.config_hash == target_config
    ).order_by(ProcessingResult.updated_at.desc()).offset(skip).limit(limit).all()
    
    video_responses = []
    for result, video in results:
        # Log the actual status being returned for debugging
        logger.debug(f"[GET /videos] Video {video.id[:8]}... status={result.status} for hash={target_config[:8]}...")
        
        video_responses.append(VideoResponse(
            id=video.id,
            filename=video.filename,
            status=result.status,
            duration=video.duration,
            created_at=video.created_at.isoformat(),
            config_hash=target_config
        ))
        
    return video_responses

@app.get("/search", response_model=List[SearchResult])
def search(
    q: str,
    response: Response,
    limit: int = 10,
    video_id: Optional[str] = Query(None, description="Filter results to specific video"),
    vision_model: Optional[str] = Query(None, description="Vision model"),
    speech_model: Optional[str] = Query(None, description="Speech model"),
    frame_interval: int = Query(5, description="Frame interval"),
    config_hash: Optional[str] = Query(None, description="Direct config hash override")
):
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    
    try:
        # Compute config hash from parameters (same logic as /videos)
        if config_hash:
            target_config = config_hash
        elif vision_model and speech_model:
            target_config = Settings._compute_config_hash(speech_model, vision_model, frame_interval)
        else:
            target_config = settings.get_config_hash()
        
        collection_name = f"video_rag_{target_config}"
        
        # Add debug headers
        response.headers["X-Debug-Config-Hash"] = target_config
        response.headers["X-Debug-Collection-Name"] = collection_name
        response.headers["Access-Control-Expose-Headers"] = "X-Debug-Config-Hash, X-Debug-Collection-Name"
        
        logger.info(f"[SEARCH] Query: '{q}' | Collection: {collection_name}" + (f" | Video filter: {video_id}" if video_id else ""))
        
        # Check if collection exists
        qdrant = get_qdrant()
        collections = qdrant.get_collections()
        collection_names = [c.name for c in collections.collections]
        logger.info(f"[SEARCH] Available collections: {collection_names}")
        
        if collection_name not in collection_names:
            logger.warning(f"[SEARCH] Collection {collection_name} not found! Videos may not be indexed yet.")
            return []

        # Use fixed embedding model (simplified architecture)
        target_embedding_model = settings.FIXED_EMBEDDING_MODEL
        logger.info(f"[SEARCH] Using fixed embedding model: {target_embedding_model}")
        
        # Lazy import ModelFactory to avoid slow startup from ML libraries
        from agents.common.model_factory import ModelFactory
        embed_model = ModelFactory.get_text_embedding_model(target_embedding_model)
        query_vector = embed_model.encode([q])[0].tolist()
        
        # Build query filter if video_id is specified
        query_filter = None
        if video_id:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="video_id",
                        match=models.MatchValue(value=video_id)
                    )
                ]
            )
            logger.info(f"[SEARCH] Filtering results to video: {video_id}")
        
        hits = qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=limit
        )
        
        logger.info(f"[SEARCH] Found {len(hits)} results" + (f" for video {video_id}" if video_id else ""))
        
        results = []
        for hit in hits:
            payload = hit.payload
            results.append(SearchResult(
                video_id=payload.get("video_id"),
                filename=payload.get("filename"),
                text=payload.get("text"),
                start=payload.get("start"),
                end=payload.get("end"),
                score=hit.score,
                type=payload.get("type")
            ))
        
        return results
    
    except Exception as e:
        logger.error(f"[SEARCH] Error during search: {str(e)}")
        logger.exception(e)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get("/debug/qdrant")
def debug_qdrant():
    """Debug endpoint to check Qdrant collections."""
    qdrant = get_qdrant()
    collections = qdrant.get_collections()
    result = []
    for c in collections.collections:
        info = qdrant.get_collection(c.name)
        result.append({
            "name": c.name,
            "points_count": info.points_count,
            "vectors_count": info.vectors_count
        })
    return {"collections": result}

# --- Configuration Management API ---
# Store configurations in a JSON file for persistence across sessions
CONFIG_FILE = settings.BASE_DIR / "saved_configs.json"

class SavedConfig(BaseModel):
    id: str
    name: str
    vision_model: str
    speech_model: str
    enable_vision: bool = True
    frame_interval: int = 5

class SavedConfigList(BaseModel):
    configurations: List[SavedConfig]

def load_saved_configs() -> dict:
    """Load saved configurations from disk."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading configs: {e}")
    
    # Return default configurations if file doesn't exist
    return {
        "configurations": [
            {
                "id": "c1",
                "name": "BLIP + Whisper Base (Fast)",
                "vision_model": "Salesforce/blip-image-captioning-base",
                "speech_model": "base",
                "enable_vision": True,
                "frame_interval": 5
            },
            {
                "id": "c2",
                "name": "BLIP + Whisper Large",
                "vision_model": "Salesforce/blip-image-captioning-base",
                "speech_model": "large-v3",
                "enable_vision": True,
                "frame_interval": 5
            },
            {
                "id": "c3",
                "name": "Florence-2 + Distil-Whisper",
                "vision_model": "microsoft/Florence-2-large",
                "speech_model": "distil-large-v3",
                "enable_vision": True,
                "frame_interval": 5
            },
            {
                "id": "c4",
                "name": "Qwen VL + Whisper Base",
                "vision_model": "Qwen/Qwen2.5-VL-7B-Instruct",
                "speech_model": "base",
                "enable_vision": True,
                "frame_interval": 5
            }
        ]
    }

def save_configs_to_disk(configs: dict):
    """Persist configurations to disk."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2)

@app.get("/configs", response_model=SavedConfigList)
def get_configurations():
    """
    Get all saved configurations.
    These configurations persist across sessions.
    """
    data = load_saved_configs()
    return SavedConfigList(configurations=[SavedConfig(**c) for c in data["configurations"]])

@app.post("/configs", response_model=SavedConfig)
def create_configuration(config: SavedConfig):
    """
    Create a new named configuration.
    """
    data = load_saved_configs()
    
    # Check for duplicate ID
    existing_ids = [c["id"] for c in data["configurations"]]
    if config.id in existing_ids:
        raise HTTPException(status_code=400, detail=f"Configuration with ID '{config.id}' already exists")
    
    # Add new configuration
    data["configurations"].append(config.dict())
    save_configs_to_disk(data)
    
    logger.info(f"[CONFIGS] Created new configuration: {config.name} (id={config.id})")
    return config

@app.put("/configs/{config_id}", response_model=SavedConfig)
def update_configuration(config_id: str, config: SavedConfig):
    """
    Update an existing configuration.
    """
    data = load_saved_configs()
    
    # Find and update
    found = False
    for i, c in enumerate(data["configurations"]):
        if c["id"] == config_id:
            data["configurations"][i] = config.dict()
            found = True
            break
    
    if not found:
        raise HTTPException(status_code=404, detail=f"Configuration '{config_id}' not found")
    
    save_configs_to_disk(data)
    logger.info(f"[CONFIGS] Updated configuration: {config.name} (id={config_id})")
    return config

@app.delete("/configs/{config_id}")
def delete_configuration(config_id: str):
    """
    Delete a configuration by ID.
    """
    data = load_saved_configs()
    original_count = len(data["configurations"])
    
    data["configurations"] = [c for c in data["configurations"] if c["id"] != config_id]
    
    if len(data["configurations"]) == original_count:
        raise HTTPException(status_code=404, detail=f"Configuration '{config_id}' not found")
    
    save_configs_to_disk(data)
    logger.info(f"[CONFIGS] Deleted configuration: {config_id}")
    return {"status": "deleted", "id": config_id}

@app.get("/models")
def get_available_models():
    """
    Get all available models for each category.
    Embedding model is fixed system-wide (MiniLM-L6-v2) for simplicity.
    """
    return {
        "vision_models": [
            {"value": "Salesforce/blip-image-captioning-base", "name": "BLIP Base", "description": "Fast & lightweight captioning"},
            {"value": "microsoft/Florence-2-large", "name": "Florence-2 Large", "description": "Detailed visual understanding"},
            {"value": "Qwen/Qwen2.5-VL-7B-Instruct", "name": "Qwen 2.5 VL", "description": "Advanced multimodal LLM"}
        ],
        "visual_embedding_models": [
            {"value": "google/siglip-so400m-patch14-384", "name": "SigLIP", "description": "High-quality visual embeddings"},
            {"value": "openai/clip-vit-base-patch32", "name": "CLIP", "description": "Classic visual-text alignment"}
        ],
        "speech_models": [
            {"value": "base", "name": "Whisper Base", "description": "Fast transcription"},
            {"value": "large-v3", "name": "Whisper Large V3", "description": "High accuracy transcription"},
            {"value": "distil-large-v3", "name": "Distil-Whisper Large V3", "description": "Balanced speed & accuracy"}
        ],
        "fixed_embedding_model": {
            "value": "all-MiniLM-L6-v2",
            "name": "MiniLM-L6-v2 (Fixed)",
            "description": "Optimized for speed and performance - used for all configurations"
        }
    }

# --- Upload Endpoint ---
class UploadResponse(BaseModel):
    id: str
    filename: str
    status: str
    message: str

@app.post("/upload", response_model=UploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    config: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Upload a video file for processing.
    
    - Saves the file to the storage directory
    - Creates a Video record in the database
    - Optionally creates a ProcessingResult for the specified config
    """
    # Validate file type
    allowed_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
        )
    
    try:
        # Generate unique ID
        video_id = str(uuid.uuid4())
        
        # Save file to storage
        safe_filename = file.filename.replace(" ", "_")
        file_path = settings.STORAGE_DIR / f"{video_id}_{safe_filename}"
        
        # P0 Fix: Offload blocking file I/O to thread pool to prevent event loop freeze
        # This allows the API to remain responsive during large file uploads
        def save_file_blocking():
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        
        await run_in_threadpool(save_file_blocking)
        
        logger.info(f"[UPLOAD] Saved file: {file_path}")
        
        # Get video metadata (duration, etc.) - basic implementation
        duration = None
        try:
            import cv2
            cap = cv2.VideoCapture(str(file_path))
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if fps > 0:
                    duration = frame_count / fps
                cap.release()
        except Exception as e:
            logger.warning(f"Could not extract video metadata: {e}")
        
        # Create Video record
        video = Video(
            id=video_id,
            filename=file.filename,
            file_path=str(file_path),
            duration=duration
        )
        db.add(video)
        db.commit()
        
        logger.info(f"[UPLOAD] Created Video record: {video_id}")
        
        # Parse config and create ProcessingResult if provided
        if config:
            try:
                config_data = json.loads(config)
                vision_model = config_data.get('vision_model', str(settings.ACTIVE_VISION_MODEL.value))
                speech_model = config_data.get('speech_model', str(settings.ACTIVE_SPEECH_MODEL.value))
                frame_interval = config_data.get('frame_interval', settings.FRAME_INTERVAL)
                
                # Use the frame_interval from config to ensure hash matches frontend
                config_hash = Settings._compute_config_hash(speech_model, vision_model, frame_interval)
                
                logger.info(f"[UPLOAD] Config received: vision={vision_model}, speech={speech_model}, frame_interval={frame_interval}")
                logger.info(f"[UPLOAD] Computed config_hash: {config_hash}")
                
                # Create ProcessingResult for this config
                result = ProcessingResult(
                    video_id=video_id,
                    config_hash=config_hash,
                    speech_model=speech_model,
                    vision_model=vision_model,
                    frame_interval=frame_interval,
                    status=VideoStatus.QUEUED.value
                )
                db.add(result)
                db.commit()
                
                logger.info(f"[UPLOAD] Created ProcessingResult with config_hash: {config_hash}")
            except json.JSONDecodeError:
                logger.warning(f"[UPLOAD] Invalid config JSON, skipping ProcessingResult creation")
        
        return UploadResponse(
            id=video_id,
            filename=file.filename,
            status="queued",
            message="Video uploaded successfully"
        )
        
    except Exception as e:
        logger.error(f"[UPLOAD] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Video Streaming Endpoint with Range Support ---
@app.get("/stream/{video_id}")
async def stream_video(
    video_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Stream video file with HTTP Range support for seeking.
    This enables the video player to jump to specific timestamps.
    """
    # Find video in database
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video_path = Path(video.file_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    
    file_size = video_path.stat().st_size
    
    # Determine content type based on extension
    extension = video_path.suffix.lower()
    content_type_map = {
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.mkv': 'video/x-matroska',
        '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime',
    }
    content_type = content_type_map.get(extension, 'video/mp4')
    
    # Handle Range header for seeking
    range_header = request.headers.get('range')
    
    if range_header:
        # Parse range header: "bytes=start-end"
        range_match = range_header.replace('bytes=', '').split('-')
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else file_size - 1
        
        # Ensure valid range
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        
        end = min(end, file_size - 1)
        content_length = end - start + 1
        
        def iterfile():
            with open(video_path, 'rb') as f:
                f.seek(start)
                remaining = content_length
                chunk_size = 1024 * 1024  # 1MB chunks
                while remaining > 0:
                    read_size = min(chunk_size, remaining)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        
        return StreamingResponse(
            iterfile(),
            status_code=206,  # Partial Content
            media_type=content_type,
            headers={
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(content_length),
                'Content-Disposition': f'inline; filename="{video.filename}"',
            }
        )
    else:
        # No range header - return full file
        def iterfile():
            with open(video_path, 'rb') as f:
                chunk_size = 1024 * 1024  # 1MB chunks
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    yield data
        
        return StreamingResponse(
            iterfile(),
            media_type=content_type,
            headers={
                'Accept-Ranges': 'bytes',
                'Content-Length': str(file_size),
                'Content-Disposition': f'inline; filename="{video.filename}"',
            }
        )

# --- Video Thumbnail Endpoint ---
@app.get("/thumbnail/{video_id}")
async def get_thumbnail(
    video_id: str,
    t: float = Query(0, description="Timestamp in seconds to extract thumbnail from"),
    db: Session = Depends(get_db)
):
    """
    Extract and return a thumbnail image from the video at the specified timestamp.
    Uses OpenCV to extract a single frame and returns it as JPEG.
    """
    import cv2
    import io
    
    # Find video in database
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video_path = Path(video.file_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    
    try:
        # Open video with OpenCV
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise HTTPException(status_code=500, detail="Could not open video file")
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30  # Default fallback
        
        # Calculate frame number from timestamp
        frame_number = int(t * fps)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # Ensure frame number is within bounds
        frame_number = max(0, min(frame_number, total_frames - 1))
        
        # Seek to the frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ret, frame = cap.read()
        cap.release()
        
        if not ret or frame is None:
            raise HTTPException(status_code=500, detail="Could not extract frame from video")
        
        # Resize thumbnail for efficiency (max 320px width)
        height, width = frame.shape[:2]
        max_width = 320
        if width > max_width:
            scale = max_width / width
            new_width = max_width
            new_height = int(height * scale)
            frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
        
        # Encode as JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
        _, buffer = cv2.imencode('.jpg', frame, encode_param)
        
        # Return as response
        return Response(
            content=buffer.tobytes(),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                "Content-Disposition": f'inline; filename="thumb_{video_id}_{int(t)}.jpg"'
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating thumbnail: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating thumbnail: {str(e)}")

@app.get("/video-info/{video_id}")
def get_video_info(video_id: str, db: Session = Depends(get_db)):
    """Get video metadata for the player."""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    return {
        "id": video.id,
        "filename": video.filename,
        "duration": video.duration,
        "width": video.width,
        "height": video.height,
        "fps": video.fps,
        "stream_url": f"/stream/{video.id}"
    }

# Mount Static Files (Frontend & Video Storage)
# Mount videos so the frontend can play them
app.mount("/videos", StaticFiles(directory=str(settings.STORAGE_DIR)), name="videos")
# Mount frontend
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    # Note: reload=False to prevent Qdrant lock conflicts from multiprocessing
    uvicorn.run("main_api:app", host=settings.API_HOST, port=settings.API_PORT, reload=False)

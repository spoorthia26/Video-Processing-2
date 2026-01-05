# Video RAG System - Architecture Documentation

## Overview
A modular Video Retrieval-Augmented Generation (RAG) system that enables semantic search through video content using configurable AI models for speech recognition, visual understanding, and text embedding.

## Core Features Implemented

### ✅ 1. Multi-Configuration Support
- **Config Hash System**: Each configuration (vision + speech + embedding model combination) gets a unique hash
- **Parallel Processing**: Multiple configurations can be processed for the same video
- **Dynamic Switching**: Frontend can switch between configurations without reprocessing

### ✅ 2. Persistent Configuration Management
- **API Endpoints**: `/configs` (GET/POST/PUT/DELETE) for managing named configurations
- **Session Persistence**: Configurations saved to `saved_configs.json`
- **Default Configurations**: 4 pre-configured pipelines (BLIP+Whisper Base, BLIP+Whisper Large, Florence-2+Distil-Whisper, Qwen+Whisper Base)

### ✅ 3. Intelligent Data Reuse
**Transcript Reuse**: If another config uses the same speech model, transcripts are reused
**Caption Reuse**: If another config uses the same vision model + frame interval, captions are reused
**Database-Driven**: ProcessingResult table tracks which artifacts have been generated

### ✅ 4. Microservices Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Ingestion     │────▶│   Processor      │────▶│   Embedding     │
│   Agent         │     │   Agent          │     │   Agent         │
│  (main.py)      │     │  (processor.py)  │     │ (embedding.py)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
   Video Files            COMPLETED status         INDEXED status
   → Video table          ProcessingResult         Qdrant collection
                          (any config)             (config-specific)
```

## Database Schema

### Video Table
```python
- id: UUID (primary key)
- filename: Original filename
- file_path: Path to stored video
- duration, fps, width, height: Metadata
- created_at: Timestamp
```

### ProcessingResult Table
```python
- id: Auto-increment
- video_id: Foreign key to Video
- config_hash: MD5 of config params
- speech_model, vision_model, embedding_model: Model names
- frame_interval: Seconds between frames
- status: queued|processing|completed|indexed|failed
- transcript_path, captions_path: Artifact paths
- UNIQUE constraint on (video_id, config_hash)
```

### Status Flow
1. **queued** → Created when frontend requests a config
2. **processing** → Processor agent is extracting features
3. **completed** → Transcripts & captions ready, needs embedding
4. **indexed** → Embeddings generated and stored in Qdrant
5. **failed** → Error occurred

## API Endpoints

### Core Video Operations
- `GET /videos` - List all videos with status for selected config
- `POST /upload` - Upload a video file
- `GET /search` - Semantic search through video content

### Configuration Management
- `GET /configs` - Get all saved configurations
- `POST /configs` - Create new named configuration
- `PUT /configs/{id}` - Update existing configuration
- `DELETE /configs/{id}` - Delete configuration
- `GET /models` - Get available models for each category

### Debug Endpoints
- `GET /debug/db` - Database statistics
- `GET /debug/qdrant` - Qdrant collection info

## Model Support

### Speech Recognition (3 models)
- **Whisper Base** (`base`) - Fast, good accuracy
- **Whisper Large V3** (`large-v3`) - Highest accuracy
- **Distil-Whisper Large V3** (`distil-large-v3`) - Balanced

### Vision/Captioning (3 models)
- **BLIP Base** (`Salesforce/blip-image-captioning-base`) - Fast captioning
- **Florence-2 Large** (`microsoft/Florence-2-large`) - Detailed descriptions
- **Qwen 2.5 VL** (`Qwen/Qwen2.5-VL-7B-Instruct`) - Advanced multimodal LLM

### Text Embedding (4 models)
- **MiniLM-L6-v2** (`all-MiniLM-L6-v2`) - Fast, 384 dims
- **Nomic Embed v1.5** (`nomic-ai/nomic-embed-text-v1.5`) - High quality
- **BGE-M3** (`BAAI/bge-m3`) - Multilingual support
- **BGE Large** (`BAAI/bge-large-en-v1.5`) - Best accuracy

## Processing Pipeline

### Phase 1: Ingestion (main.py)
1. Monitors `data/inbox` for new video files
2. Validates video format and extracts metadata
3. Moves file to `data/videos` with UUID
4. Creates Video record in database

### Phase 2: Feature Extraction (processor.py)
1. Picks up ANY queued ProcessingResult (multi-config)
2. **Audio**: Transcribes with specified speech model (reuses if exists)
3. **Video**: Extracts frames every N seconds, generates captions (reuses if exists)
4. Saves combined JSON to `data/videos/{video_id}_{config_hash}.json`
5. Sets status to COMPLETED

### Phase 3: Embedding & Indexing (embedding.py)
1. Picks up ANY completed ProcessingResult
2. Chunks transcripts and captions into semantic units
3. Generates embeddings with specified text embedding model
4. Pushes to Qdrant via API (collection: `video_rag_{config_hash}`)
5. Sets status to INDEXED

### Phase 4: Search (main_api.py)
1. Receives query + config params from frontend
2. Computes config_hash to determine collection
3. Generates query embedding with appropriate model
4. Searches Qdrant collection
5. Returns ranked results with timestamps

## Configuration Hash Computation

```python
config_str = f"{speech_model}-{vision_model}-{embedding_model}-{frame_interval}"
config_hash = hashlib.md5(config_str.encode()).hexdigest()
```

This ensures frontend and backend always agree on which collection to use.

## Data Flow Example

```
1. User uploads "video.mp4"
   → Video record created (id=abc123)

2. User selects "BLIP + Whisper Base"
   → ProcessingResult created (hash=def456, status=queued)

3. Processor picks up job
   → Transcribe with Whisper Base
   → Extract frames, caption with BLIP
   → Save to video_abc123_def456.json
   → Status → completed

4. Embedding agent picks up job
   → Chunk text, embed with MiniLM
   → Push to Qdrant collection "video_rag_def456"
   → Status → indexed

5. User searches "scene of a man"
   → Embed query with MiniLM
   → Search "video_rag_def456"
   → Return top 10 matches
```

## Key Design Decisions

### 1. Why Config Hash?
- **Unique Collections**: Each config gets its own Qdrant collection
- **Parallel Configs**: Same video can have multiple configs processed simultaneously
- **Cache Validity**: Know exactly which artifacts are valid for which config

### 2. Why Separate Agents?
- **Isolation**: Processor can crash without affecting API
- **Resource Management**: Heavy ML models loaded only when needed
- **Scalability**: Can run multiple processor instances

### 3. Why ProcessingResult Table?
- **Multi-Config State**: Track status per video per config
- **Reuse Intelligence**: Query for matching artifacts to reuse
- **Audit Trail**: Know exactly what was processed when

### 4. Why Lazy Qdrant Init?
- **Lock Avoidance**: Prevents file locking issues in multiprocess env
- **Startup Speed**: API starts faster without loading Qdrant immediately

## Frontend Integration

The frontend (`frontend/script.js`) communicates with the backend through:

1. **Config Loading**: Fetches `/configs` on startup
2. **Video Listing**: Calls `/videos` with current config params
3. **Search**: Sends query + config params to `/search`
4. **Config Management**: Can create/edit/delete configs via API

## Known Limitations & Future Improvements

### Current Limitations
1. **No Frame Caching**: Frames are re-extracted for each vision model (even if same interval)
2. **No Visual Embeddings**: SIGLIP/CLIP models defined but not integrated
3. **No Hybrid Search**: Text-only search; visual similarity search not implemented
4. **No Clip Extraction**: Search returns timestamps but not actual video clips
5. **Sequential Processing**: One video processed at a time per agent

### Planned Improvements
1. **FrameCache Table**: Cache extracted frames by video_id + interval
2. **Visual Embedding Pipeline**: Generate SIGLIP/CLIP embeddings of frames
3. **Dual-Mode Search**: Query both text and visual embeddings
4. **Clip Generation**: Extract actual video segments for results
5. **Parallel Processing**: Process multiple videos concurrently

## Performance Characteristics

### Processing Speed (estimated)
- **10-minute video** with BLIP + Whisper Base: ~5-7 minutes
- **10-minute video** with Qwen + Whisper Large: ~15-20 minutes

### Storage Requirements
- **Video**: Original file size
- **Transcripts**: ~50-100 KB per video
- **Embeddings**: ~100-500 KB per video per config (in Qdrant)

### Scalability
- **SQLite**: Good for <100 videos, consider PostgreSQL beyond
- **Qdrant**: Can handle millions of vectors
- **Processor**: CPU/GPU bound, benefits from parallelization

## Troubleshooting

### "Ghost Data" Bug
**Symptom**: Videos appear in one terminal but not frontend
**Cause**: Multiple SQLite files created due to relative paths
**Fix**: Use absolute paths in config.py (IMPLEMENTED)

### Qdrant Lock Error
**Symptom**: "Database is locked" when starting API
**Cause**: Multiple processes accessing Qdrant simultaneously
**Fix**: Lazy initialization, reload=False in uvicorn (IMPLEMENTED)

### Search Returns Nothing
**Check**: Is video status "Ready" (indexed)?
**Check**: Does Qdrant collection exist for this config hash?
**Check**: Use `/debug/qdrant` to inspect collections

## Development Setup

```bash
# 1. Activate environment
.\.venv\Scripts\activate

# 2. Start API (port 8082)
python main_api.py

# 3. Start Processor (separate terminal)
python -m agents.processor

# 4. Start Embedding Agent (separate terminal)
python -m agents.embedding

# 5. Start Ingestion Agent (optional, for inbox monitoring)
python -m agents.ingestion

# 6. Access frontend
http://localhost:8082
```

## Summary

This system successfully implements:
- ✅ Modular model switching (3 speech × 3 vision × 4 embedding = 36 possible configs)
- ✅ Configuration persistence across sessions
- ✅ Intelligent data reuse (transcripts & captions)
- ✅ Multi-config parallel processing
- ✅ Clean API for frontend integration

The architecture is production-ready for the current feature set, with clear paths for future enhancements (frame caching, visual embeddings, hybrid search).

import os
import hashlib
from enum import Enum
from pathlib import Path
from pydantic_settings import BaseSettings

# --- Enums ---
class VisionModel(str, Enum):
    BLIP_BASE = "Salesforce/blip-image-captioning-base"
    FLORENCE_2_LARGE = "microsoft/Florence-2-large"
    QWEN_2_5_VL = "Qwen/Qwen2.5-VL-7B-Instruct"
    SIGLIP = "google/siglip-so400m-patch14-384"

class VisualEmbeddingModel(str, Enum):
    SIGLIP = "google/siglip-so400m-patch14-384"
    CLIP = "openai/clip-vit-base-patch32"

class SpeechModel(str, Enum):
    FASTER_WHISPER_BASE = "base"
    FASTER_WHISPER_LARGE = "large-v3"
    DISTIL_WHISPER_LARGE_V3 = "distil-large-v3"

class TextEmbeddingModel(str, Enum):
    MINILM_L6 = "all-MiniLM-L6-v2"
    NOMIC_EMBED_V1_5 = "nomic-ai/nomic-embed-text-v1.5"
    BGE_M3 = "BAAI/bge-m3"
    BAAI_BGE_LARGE = "BAAI/bge-large-en-v1.5"

class VideoStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    FAILED = "failed"
    COMPLETED = "completed"
    INDEXED = "indexed"

# --- Path Safety: Use absolute path based on this file's location ---
# This ensures all processes (Ingestion, Processor, API) use the SAME database file
# regardless of which directory they are launched from.
_CONFIG_DIR = Path(__file__).resolve().parent  # agents/common/
_PROJECT_ROOT = _CONFIG_DIR.parent.parent       # Video Processing 2/

# --- Configuration ---
class Settings(BaseSettings):
    # Paths - All absolute, based on project root
    BASE_DIR: Path = _PROJECT_ROOT
    INBOX_DIR: Path = _PROJECT_ROOT / "data" / "inbox"
    STORAGE_DIR: Path = _PROJECT_ROOT / "data" / "videos"
    # CRITICAL FIX: Absolute path prevents "Ghost Data" bug from CWD differences
    DB_PATH: str = f"sqlite:///{(_PROJECT_ROOT / 'db' / 'video_rag.db').as_posix()}"
    QDRANT_PATH: Path = _PROJECT_ROOT / "qdrant_data"
    
    # Models Configuration
    ACTIVE_VISION_MODEL: VisionModel = VisionModel.BLIP_BASE
    ACTIVE_VISUAL_EMBEDDING_MODEL: VisualEmbeddingModel = VisualEmbeddingModel.CLIP
    ACTIVE_SPEECH_MODEL: SpeechModel = SpeechModel.FASTER_WHISPER_BASE
    
    # Fixed embedding model - using best performance/speed tradeoff
    FIXED_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    
    # Processing Settings
    FRAME_INTERVAL: int = 5  # Extract a frame every X seconds
    
    # Qdrant Settings
    QDRANT_COLLECTION_NAME: str = "video_rag_collection"

    # API Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8082
    
    def get_config_hash(self) -> str:
        """Generate hash for the current active configuration."""
        return self._compute_config_hash(
            str(self.ACTIVE_SPEECH_MODEL.value if hasattr(self.ACTIVE_SPEECH_MODEL, 'value') else self.ACTIVE_SPEECH_MODEL),
            str(self.ACTIVE_VISION_MODEL.value if hasattr(self.ACTIVE_VISION_MODEL, 'value') else self.ACTIVE_VISION_MODEL),
            self.FRAME_INTERVAL
        )
    
    @staticmethod
    def _compute_config_hash(speech_model: str, vision_model: str, frame_interval: int = 5) -> str:
        """
        Static method to compute config hash from individual parameters.
        Embedding model is FIXED (not part of config hash) to simplify switching.
        """
        config_str = f"{speech_model}-{vision_model}-{frame_interval}"
        return hashlib.md5(config_str.encode()).hexdigest()

    @property
    def active_collection_name(self) -> str:
        return f"video_rag_{self.get_config_hash()}"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# Ensure directories exist (using absolute paths now)
settings.INBOX_DIR.mkdir(parents=True, exist_ok=True)
settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
settings.QDRANT_PATH.mkdir(parents=True, exist_ok=True)
(_PROJECT_ROOT / "db").mkdir(parents=True, exist_ok=True)

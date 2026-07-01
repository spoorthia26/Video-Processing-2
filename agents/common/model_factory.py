import torch
import logging
import warnings

# Suppress noisy library logs
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("faster_whisper").setLevel(logging.WARNING)
warnings.filterwarnings("ignore", message=".*torch.classes.*")

from transformers import (
    BlipProcessor, BlipForConditionalGeneration,
    AutoProcessor, AutoModelForCausalLM,
    AutoTokenizer, AutoModel
)
from sentence_transformers import SentenceTransformer
from faster_whisper import WhisperModel
from .config import settings, VisionModel, SpeechModel, TextEmbeddingModel, VisualEmbeddingModel
from typing import Union

logger = logging.getLogger(__name__)

class ModelFactory:
    _instances = {}
    _current_vision_model = None
    _current_speech_model = None
    _current_text_embedding_model = None

    @staticmethod
    def _get_enum_value(model_input: Union[str, VisionModel, SpeechModel, TextEmbeddingModel]) -> str:
        """Extract string value from either Enum or string input."""
        if hasattr(model_input, 'value'):
            return model_input.value
        return str(model_input)

    @staticmethod
    def get_vision_model(model_input: Union[str, VisionModel]):
        """Load vision model. Accepts either Enum or string model name."""
        model_value = ModelFactory._get_enum_value(model_input)
        
        # Check if we need to switch models
        if "vision" in ModelFactory._instances and ModelFactory._current_vision_model == model_value:
            return ModelFactory._instances["vision"]
        
        # Unload previous vision model if different
        if "vision" in ModelFactory._instances:
            logger.info(f"Unloading previous vision model: {ModelFactory._current_vision_model}")
            del ModelFactory._instances["vision"]
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        logger.info(f"Loading Vision Model: {model_value}")
        device = "cuda" if torch.cuda.is_available() else "cpu"

        if model_value == VisionModel.BLIP_BASE.value:
            processor = BlipProcessor.from_pretrained(model_value)
            model = BlipForConditionalGeneration.from_pretrained(model_value).to(device)
            instance = {"processor": processor, "model": model, "type": "blip"}
        
        elif model_value == VisionModel.FLORENCE_2_LARGE.value:
            processor = AutoProcessor.from_pretrained(model_value, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                model_value, 
                trust_remote_code=True,
                attn_implementation="eager"  # Fix for _supports_sdpa error
            ).to(device)
            instance = {"processor": processor, "model": model, "type": "florence"}
            
        elif model_value == VisionModel.QWEN_2_5_VL.value:
            # Qwen 2.5 VL requires specific model class
            from transformers import Qwen2_5_VLForConditionalGeneration
            model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                model_value, torch_dtype="auto", device_map="auto"
            )
            processor = AutoProcessor.from_pretrained(model_value)
            instance = {"processor": processor, "model": model, "type": "qwen"}
        
        elif model_value == VisionModel.SIGLIP.value:
            # SigLIP for zero-shot classification
            processor = AutoProcessor.from_pretrained(model_value)
            model = AutoModel.from_pretrained(model_value).to(device)
            instance = {"processor": processor, "model": model, "type": "siglip"}
        
        else:
            raise ValueError(f"Unsupported Vision Model: {model_value}")

        ModelFactory._instances["vision"] = instance
        ModelFactory._current_vision_model = model_value
        return instance

    @staticmethod
    def get_speech_model(model_input: Union[str, SpeechModel]):
        """Load speech model. Accepts either Enum or string model name."""
        model_value = ModelFactory._get_enum_value(model_input)
        
        # Check if we need to switch models
        if "speech" in ModelFactory._instances and ModelFactory._current_speech_model == model_value:
            return ModelFactory._instances["speech"]
        
        # Unload previous speech model if different
        if "speech" in ModelFactory._instances:
            logger.info(f"Unloading previous speech model: {ModelFactory._current_speech_model}")
            del ModelFactory._instances["speech"]
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        logger.info(f"Loading Speech Model: {model_value}")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        
        model = WhisperModel(model_value, device=device, compute_type=compute_type)
        
        ModelFactory._instances["speech"] = model
        ModelFactory._current_speech_model = model_value
        return model

    @staticmethod
    def get_text_embedding_model(model_input: Union[str, TextEmbeddingModel]):
        """Load text embedding model. Accepts either Enum or string model name."""
        model_value = ModelFactory._get_enum_value(model_input)
        
        # Check if we need to switch models
        if "text_embedding" in ModelFactory._instances and ModelFactory._current_text_embedding_model == model_value:
            return ModelFactory._instances["text_embedding"]
        
        # Unload previous embedding model if different
        if "text_embedding" in ModelFactory._instances:
            logger.info(f"Unloading previous embedding model: {ModelFactory._current_text_embedding_model}")
            del ModelFactory._instances["text_embedding"]
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        logger.info(f"Loading Text Embedding Model: {model_value}")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = SentenceTransformer(model_value, device=device)
        
        ModelFactory._instances["text_embedding"] = model
        ModelFactory._current_text_embedding_model = model_value
        return model

    @staticmethod
    def unload_models():
        """Free up VRAM by deleting models."""
        logger.info("Unloading all models...")
        ModelFactory._instances.clear()
        ModelFactory._current_vision_model = None
        ModelFactory._current_speech_model = None
        ModelFactory._current_text_embedding_model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

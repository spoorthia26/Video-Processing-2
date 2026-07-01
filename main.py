import multiprocessing
import time
import sys
import os
import shutil
from pathlib import Path
import uvicorn
from agents.common.config import settings

# Ensure the current directory is in the path
sys.path.append(os.getcwd())

def setup_ffmpeg():
    """Adds local FFmpeg to PATH if not found globally."""
    if not shutil.which("ffmpeg"):
        # Look for local tools folder
        ffmpeg_path = Path("tools/ffmpeg/ffmpeg-7.0-full_build/bin")
        if ffmpeg_path.exists():
            print(f"Found local FFmpeg at: {ffmpeg_path}")
            os.environ["PATH"] = str(ffmpeg_path.resolve()) + os.pathsep + os.environ["PATH"]
        else:
            print("Warning: FFmpeg not found in PATH or tools/ directory. Ingestion may fail.")

def start_api():
    """Runs the FastAPI server."""
    print(f"Starting API on {settings.API_HOST}:{settings.API_PORT}...")
    uvicorn.run("main_api:app", host=settings.API_HOST, port=settings.API_PORT, reload=False)

def start_ingestion():
    """Runs the Ingestion Process."""
    print("Starting Ingestion Process...")
    from agents.ingestion import process_inbox
    while True:
        try:
            process_inbox()
        except Exception as e:
            print(f"Ingestion Error: {e}")
        time.sleep(5)

def start_processor():
    """Runs the Processor Process."""
    print("Starting Processor Process...")
    from agents.processor import run_processor
    while True:
        try:
            run_processor()
        except Exception as e:
            print(f"Processor Error: {e}")
            time.sleep(5)

def start_embedding():
    """Runs the Embedding Process."""
    print("Starting Embedding Process...")
    from agents.embedding import run_embedding
    while True:
        try:
            run_embedding()
        except Exception as e:
            print(f"Embedding Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    if sys.platform == 'win32':
        multiprocessing.set_executable(sys.executable)

    print(f"Running with Python: {sys.executable}")
    
    # Setup Environment
    setup_ffmpeg()

    # Create processes
    p_api = multiprocessing.Process(target=start_api, name="API")
    p_ingest = multiprocessing.Process(target=start_ingestion, name="Ingestion")
    p_process = multiprocessing.Process(target=start_processor, name="Processor")
    p_embed = multiprocessing.Process(target=start_embedding, name="Embedding")

    processes = [p_api, p_ingest, p_process, p_embed]

    try:
        # Start all processes
        for p in processes:
            p.start()
            time.sleep(1)

        print("\n" + "="*50)
        print(f"🚀 System is running!")
        print(f"🌐 Open the UI at: http://localhost:{settings.API_PORT}")
        print("="*50 + "\n")
        
        # Keep main process alive
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("Stopping...")
        for p in processes:
            p.terminate()
            p.join()

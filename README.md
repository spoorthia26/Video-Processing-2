# Video Retrieval POC

A powerful, local-first video search engine that allows you to search through your video library using natural language. This Proof of Concept (POC) leverages state-of-the-art AI models for speech recognition, visual analysis, and semantic search to make video content searchable.

![UI Screenshot](https://via.placeholder.com/800x450?text=Video+Retrieval+UI)

## 🚀 Features

*   **Multi-Modal Ingestion**: Automatically processes video files to extract both audio (speech) and visual information.
*   **Advanced Transcription**: Uses **Faster-Whisper** for high-accuracy, efficient speech-to-text conversion.
*   **Visual Understanding**: Uses **BLIP (Bootstrapping Language-Image Pre-training)** to generate captions for visual frames, allowing you to search for visual events (e.g., "person running", "white car").
*   **Semantic Search**: Powered by **Sentence-Transformers** and **Qdrant** vector database, enabling "meaning-based" search rather than just keyword matching.
*   **Modern UI**: A clean, dark-themed web interface with a chat-like search experience and real-time processing logs.
*   **Configurable Pipelines**: Switch between different AI models (Vision, Speech, Embeddings) on the fly via the settings modal.
*   **Local Processing**: All processing happens locally on your machine. No data is sent to the cloud.

## 🏗️ Architecture

The application follows a micro-service-like architecture composed of independent "Agents" orchestrated by a central `main.py` script.

### Core Components

1.  **Frontend (`frontend/`)**:
    *   A lightweight, vanilla HTML/CSS/JS single-page application.
    *   Communicates with the backend via REST API.
    *   Features a responsive Sidebar, Chat Interface, and Video Player with timestamp navigation.

2.  **Backend API (`agents/api/`)**:
    *   Built with **FastAPI**.
    *   Handles file uploads, search queries, and serves the frontend.
    *   Manages the SQLite database for metadata.

3.  **Ingestion Agent (`agents/ingestion_agent/`)**:
    *   Watches the `data/inbox` folder.
    *   Validates video files and extracts metadata (duration, resolution, fps) using **FFmpeg**.
    *   Moves files to `data/videos` for processing.

4.  **Transcription Agent (`agents/transcription_agent/`)**:
    *   Monitors for new videos.
    *   **Speech**: Transcribes audio using `faster-whisper`.
    *   **Vision**: Extracts frames at regular intervals and generates captions using `BLIP`.
    *   Produces a unified transcript containing both spoken words and visual descriptions.

5.  **Embedding Agent (`agents/embedding_agent/`)**:
    *   Chunks the transcripts into semantic segments.
    *   Generates vector embeddings using `Sentence-Transformers` (e.g., `all-MiniLM-L6-v2`).
    *   Stores vectors in a local **Qdrant** database for fast retrieval.

## 📂 Detailed File Structure

```text
Video Processing 2/
├── agents/                             # Core backend logic modules
│   ├── api/                            # REST API Service
│   │   └── api_service.py              # FastAPI application entry point & endpoints
│   ├── common/                         # Shared utilities and configurations
│   │   ├── config.py                   # Global settings (env vars, paths)
│   │   ├── database.py                 # Database connection & session management
│   │   ├── interfaces.py               # Abstract base classes/interfaces
│   │   └── models.py                   # SQLAlchemy ORM models (Video, Transcript)
│   ├── embedding_agent/                # Semantic Search Logic
│   │   ├── embedding_entry.py          # Entry point for the embedding process
│   │   ├── embedding_processor.py      # Logic for chunking & generating vectors
│   │   └── models.py                   # Embedding-specific data models
│   ├── ingestion_agent/                # File Ingestion Logic
│   │   ├── ingestion_entry.py          # Entry point for the ingestion process
│   │   └── ingestion_processor.py      # FFmpeg metadata extraction & file moving
│   └── transcription_agent/            # AI Processing Logic
│       ├── models.py                   # Transcription-specific data models
│       ├── transcription_entry.py      # Entry point for transcription process
│       └── transcription_processor.py  # Whisper (Audio) & BLIP (Vision) implementation
├── data/                               # Local data storage (GitIgnored)
│   ├── inbox/                          # Watch folder for new video uploads
│   ├── transcripts/                    # Output folder for JSON/VTT transcripts
│   └── videos/                         # Storage for processed video files
├── db/                                 # Database storage
│   └── video_retrieval.db              # SQLite database file (created on runtime)
├── frontend/                           # Single Page Application
│   ├── index.html                      # Main UI structure
│   ├── script.js                       # Frontend logic (API calls, UI updates)
│   └── style.css                       # Styling (Dark mode, Layouts)
├── tools/                              # External binaries
│   └── ffmpeg/                         # Local FFmpeg installation
├── main.py                             # Master entry point (Orchestrator)
├── migrate_db.py                       # Database migration utility
├── setup_database.py                   # Initial database setup script
├── requirements.txt                    # Python package dependencies
└── README.md                           # Project documentation
```

## 🛠️ Setup & Installation

### Prerequisites
*   **Python 3.10+**
*   **CUDA-capable GPU** (Highly recommended for reasonable processing speeds). The system will fallback to CPU but will be significantly slower.

### Installation Steps

1.  **Clone the Repository**
    ```bash
    git clone <repository-url>
    cd "Video Processing 2"
    ```

2.  **Create a Virtual Environment**
    ```bash
    python -m venv .venv
    # Windows
    .\.venv\Scripts\activate
    # Linux/Mac
    source .venv/bin/activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```
    *Note: You may need to install the specific PyTorch version for your CUDA version manually if the default installation doesn't detect your GPU.*

4.  **FFmpeg Setup**
    The project includes a local build of FFmpeg in `tools/ffmpeg/`. The application is configured to look for it there automatically.

## 🏃 Usage

1.  **Start the Application**
    Run the main script to launch the API and all background agents.
    ```bash
    python main.py
    ```
    *The server will start on `http://127.0.0.1:8001` (or the next available port).*

2.  **Access the UI**
    Open your web browser and navigate to the URL displayed in the terminal (usually `http://127.0.0.1:8001`).

3.  **Upload a Video**
    *   Drag and drop a video file (MP4, MKV, AVI) into the "Upload Video" zone in the sidebar.
    *   The **Ingestion Agent** will pick it up.
    *   The **Transcription Agent** will process speech and vision (check the logs in the sidebar for progress).
    *   The **Embedding Agent** will index it.

4.  **Search**
    *   Type a query in the chat bar (e.g., "Show me where they talk about climate change" or "A red car driving down the street").
    *   The system will return relevant video clips. Click a result to jump to that specific timestamp.

## ⚙️ Configuration

You can customize the processing pipeline by clicking the **Edit (Pencil)** icon next to the "Active Pipeline" in the sidebar.

*   **Vision Model**: Choose between `Qwen 2.5 VL`, `BLIP Large`, or `Florence-2` for visual captioning.
*   **Speech Model**: Select `Whisper Base` (fast), `Whisper Large` (accurate), or `Distil-Whisper`.
*   **Embedding Model**: Select the vector model (`MiniLM`, `Nomic`, `BGE-M3`).
*   **Enable Vision**: Toggle visual analysis on/off to save resources if you only need speech search.

## 🤝 Contributing

1.  Fork the repository.
2.  Create a feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
#   v i d e o - p r o c e s s i n g - 3  
 
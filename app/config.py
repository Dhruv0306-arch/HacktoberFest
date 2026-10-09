import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Ollama ---------------------------------------------------------------
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")
# The server defaults num_ctx to 4096 which is too small for a long PDF plus a
# structured JSON answer, so we ask for more explicitly.
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))
NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "6000"))
REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "420"))
KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "10m")
# gemma4 can emit reasoning tokens; extraction does not need them and they roughly
# double latency. Set OLLAMA_THINK=auto to let the model decide.
_think_env = os.environ.get("OLLAMA_THINK", "false").strip().lower()
THINK: Any = "auto" if _think_env == "auto" else _think_env in {"1", "true", "yes", "on"}

# --- Storage --------------------------------------------------------------
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", str(BASE_DIR / "data")))
ANALYSES_DIR = DATA_DIR / "analyses"
DIRECTORY_FILE = DATA_DIR / "directory.json"

# --- Ingestion limits -----------------------------------------------------
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", "90000"))
MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "6"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen2.5:7b")

# To enable flash attention, run on server before starting Ollama:
#   export OLLAMA_FLASH_ATTENTION=1
#   ollama serve
# Or add to /etc/environment for persistence.

# Max simultaneous STT/LLM/TTS jobs. Whisper + Supertonic share one GPU, so
# keep this small; set to 1 if the local TTS/STT models are not thread-safe.
MAX_CONCURRENT_CALLS = int(os.environ.get("MAX_CONCURRENT_CALLS", "2"))

# --- MySQL storage (agents, conversations, messages, orders, events) -------
MYSQL_HOST = os.environ.get("MYSQL_HOST", "192.168.11.244")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "voicebot")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DB = os.environ.get("MYSQL_DB", "voicebot")
MYSQL_POOL_SIZE = int(os.environ.get("MYSQL_POOL_SIZE", "4"))

# Copy of the caller recording is kept under RECORDINGS_DIR and registered in
# audio_artifacts when enabled.
SAVE_CALLER_AUDIO = os.environ.get("SAVE_CALLER_AUDIO", "1") in ("1", "true", "True")
RECORDINGS_DIR = os.environ.get("RECORDINGS_DIR", "recordings")

SESSION_TTL = int(os.environ.get("SESSION_TTL", "1800"))

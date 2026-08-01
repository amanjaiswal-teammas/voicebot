OLLAMA_HOST = "http://localhost:11434"
MODEL_NAME = "qwen2.5:7b"

# Max simultaneous STT/LLM/TTS jobs. Whisper + Supertonic share one GPU, so
# keep this small; set to 1 if the local TTS/STT models are not thread-safe.
MAX_CONCURRENT_CALLS = 2

# To enable flash attention, run on server before starting Ollama:
#   export OLLAMA_FLASH_ATTENTION=1
#   ollama serve
# Or add to /etc/environment for persistence.

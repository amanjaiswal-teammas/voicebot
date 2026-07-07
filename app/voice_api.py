import os
import io
import numpy as np
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from .session import create_session
from .conversation import process_call
from .memory import add_message

AUDIO_DIR = "audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

GREETING_TEXT = (
    "Good morning. This is BellaVita. "
    "You added a product to your cart - "
    "we have an exclusive discount for you today. "
    "May I tell you more about it?"
)

_cached_greeting_wav: Optional[bytes] = None

app = FastAPI()


def _resample_to_8k_bytes(input_path: str) -> bytes:
    from scipy import signal
    import soundfile as sf

    data, sr = sf.read(input_path)
    if sr == 8000:
        with open(input_path, "rb") as f:
            return f.read()

    num = int(len(data) * 8000 / sr)
    resampled = signal.resample(data, num).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, resampled, 8000, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _preload_greeting():
    global _cached_greeting_wav
    from .supertonic_engine import speak

    path = f"{AUDIO_DIR}/_greeting.wav"
    if not os.path.exists(path):
        print("PRELOAD: Generating greeting TTS...")
        speak(GREETING_TEXT, path, "en")
    _cached_greeting_wav = _resample_to_8k_bytes(path)
    print("PRELOAD: Greeting cached.")


@app.on_event("startup")
async def startup():
    _preload_greeting()


def resample_to_8k(input_path: str) -> bytes:
    return _resample_to_8k_bytes(input_path)


@app.post("/voice-audio")
async def voice_audio(
    audio: Optional[UploadFile] = File(None),
    call_id: str = Form(None),
    outbound: bool = Form(False),
):
    if not call_id:
        call_id = create_session()

    if outbound:
        add_message(call_id, "assistant", GREETING_TEXT)
        return Response(
            content=_cached_greeting_wav,
            media_type="audio/wav",
        )

    if audio is None:
        raise HTTPException(status_code=400, detail="Audio file missing")

    temp_file = f"{AUDIO_DIR}/{call_id}_in.wav"
    with open(temp_file, "wb") as f:
        f.write(await audio.read())

    result = process_call(call_id, temp_file)

    out_path = result["audio"]
    wav_bytes = resample_to_8k(out_path)
    return Response(content=wav_bytes, media_type="audio/wav")

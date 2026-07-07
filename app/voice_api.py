import os
import io
import wave
import audioop
import numpy as np
import soundfile as sf
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

_cached_greeting_ulaw: Optional[bytes] = None

app = FastAPI()


def _trim_silence(input_path: str, threshold: float = 0.02, padding: float = 0.3) -> str:
    data, sr = sf.read(input_path)
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    mask = np.abs(data) > threshold
    indices = np.where(mask)[0]
    if len(indices) == 0:
        return input_path
    start = max(0, int(indices[0] - padding * sr))
    end = min(len(data), int(indices[-1] + padding * sr))
    trimmed = data[start:end]
    trimmed_path = input_path.replace(".wav", "_trimmed.wav")
    sf.write(trimmed_path, trimmed, sr)
    return trimmed_path


def _audio_to_ulaw(input_path: str, gain: float = 1.5) -> bytes:
    data, sr = sf.read(input_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 8000:
        target_len = int(len(data) * 8000 / sr)
        x_new = np.linspace(0, len(data) - 1, target_len)
        data = np.interp(x_new, np.arange(len(data)), data).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, data, 8000, format="WAV", subtype="PCM_16")
    buf.seek(0)
    with wave.open(buf, "rb") as w:
        pcm = w.readframes(w.getnframes())
    pcm = audioop.mul(pcm, 2, gain)
    return audioop.lin2ulaw(pcm, 2)


def _preload_greeting():
    global _cached_greeting_ulaw
    from .supertonic_engine import speak

    path = f"{AUDIO_DIR}/_greeting.wav"
    if not os.path.exists(path):
        print("PRELOAD: Generating greeting TTS...")
        speak(GREETING_TEXT, path, "en")
    _cached_greeting_ulaw = _audio_to_ulaw(path)
    print("PRELOAD: Greeting cached (µ-law).")


@app.on_event("startup")
async def startup():
    _preload_greeting()


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
            content=_cached_greeting_ulaw,
            media_type="audio/x-mulaw",
        )

    if audio is None:
        raise HTTPException(status_code=400, detail="Audio file missing")

    temp_file = f"{AUDIO_DIR}/{call_id}_in.wav"
    with open(temp_file, "wb") as f:
        f.write(await audio.read())

    try:
        diag_data, diag_sr = sf.read(temp_file)
        if len(diag_data) > 0:
            peak = float(np.abs(diag_data).max())
            rms = float(np.sqrt(np.mean(diag_data ** 2)))
            print(f"AUDIO DIAG: sr={diag_sr} len={len(diag_data)} peak={peak:.5f} rms={rms:.5f}")
        else:
            print(f"AUDIO DIAG: sr={diag_sr} len=0 EMPTY FILE")
    except Exception as e:
        print(f"AUDIO DIAG ERROR: {e}")

    trimmed = _trim_silence(temp_file)
    result = process_call(call_id, trimmed)
    if trimmed != temp_file and os.path.exists(trimmed):
        os.remove(trimmed)
    if os.path.exists(temp_file):
        os.remove(temp_file)

    out_path = result.get("audio")
    if out_path and os.path.exists(out_path):
        ulaw_bytes = _audio_to_ulaw(out_path)
        os.remove(out_path)
        headers = {}
        if result.get("hangup"):
            headers["X-Hangup"] = "true"
            print("HANGUP SIGNALED")
        return Response(content=ulaw_bytes, media_type="audio/x-mulaw", headers=headers)

    return Response(
        content=_cached_greeting_ulaw,
        media_type="audio/x-mulaw",
    )

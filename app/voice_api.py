import os
import io
import json
import wave
import base64
import audioop
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from .session import create_session
from .conversation import process_call
from .memory import add_message
from .supertonic_engine import speak_segments, speak

AUDIO_DIR = "audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

GREETING_TEXT = (
    "Good morning. This is BellaVita. "
    "You added a product to your cart - "
    "we have an exclusive discount for you today. "
    "May I tell you more about it?"
)

_cached_greeting_ulaw: Optional[bytes] = None
_cached_greeting_segments: Optional[str] = None

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


def _audio_to_ulaw(input_path: str) -> bytes:
    data, sr = sf.read(input_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 8000:
        up = 8000
        down = sr
        g = int(np.gcd(up, down))
        data = resample_poly(data, up // g, down // g)
        data = data.astype(np.float32)
    peak = np.abs(data).max()
    if peak > 0.95:
        data = data / peak * 0.95
    buf = io.BytesIO()
    sf.write(buf, data, 8000, format="WAV", subtype="PCM_16")
    buf.seek(0)
    with wave.open(buf, "rb") as w:
        pcm = w.readframes(w.getnframes())
    return audioop.lin2ulaw(pcm, 2)


def _preload_greeting():
    global _cached_greeting_ulaw, _cached_greeting_segments
    from .supertonic_engine import get_tts, speak

    print("PRELOAD: Loading TTS model...")
    get_tts()
    print("PRELOAD: TTS model ready.")

    path = f"{AUDIO_DIR}/_greeting.wav"
    if not os.path.exists(path):
        print("PRELOAD: Generating greeting TTS...")
        speak(GREETING_TEXT, path, "en")
    _cached_greeting_ulaw = _audio_to_ulaw(path)

    print("PRELOAD: Preloading greeting segments...")
    segs = speak_segments(GREETING_TEXT, "en", prefix="greeting")
    segments_json = []
    for text, seg_path in segs:
        ulaw_bytes = _audio_to_ulaw(seg_path)
        os.remove(seg_path)
        segments_json.append({
            "text": text,
            "audio": base64.b64encode(ulaw_bytes).decode(),
        })
    _cached_greeting_segments = json.dumps(
        {"call_id": "", "segments": segments_json, "hangup": False}
    )
    print("PRELOAD: Greeting cached (µ-law + segments).")


@app.on_event("startup")
async def startup():
    _preload_greeting()


@app.post("/check-speech")
async def check_speech(audio: UploadFile = File(...)):
    import uuid
    temp = f"{AUDIO_DIR}/_check_{uuid.uuid4().hex}.wav"
    with open(temp, "wb") as f:
        f.write(await audio.read())
    data, sr = sf.read(temp)
    os.remove(temp)
    if len(data) == 0:
        print("CHECK-SPEECH: empty file")
        return {"speech_detected": False, "rms": 0.0}
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    rms = float(np.sqrt(np.mean(data ** 2)))
    if np.isnan(rms) or np.isinf(rms):
        print(f"CHECK-SPEECH: bad rms={rms} len={len(data)}")
        rms = 0.0
    print(f"CHECK-SPEECH: rms={rms:.5f} len={len(data)}")
    return {"speech_detected": rms > 0.01, "rms": rms}


@app.post("/voice-audio-segmented")
async def voice_audio_segmented(
    audio: Optional[UploadFile] = File(None),
    call_id: str = Form(None),
    outbound: bool = Form(False),
    interrupted_text: Optional[str] = Form(None),
):
    if not call_id:
        call_id = create_session()

    if outbound:
        add_message(call_id, "assistant", GREETING_TEXT)
        data = json.loads(_cached_greeting_segments)
        data["call_id"] = call_id
        return Response(
            content=json.dumps(data),
            media_type="application/json",
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
            print(f"SEG AUDIO DIAG: sr={diag_sr} len={len(diag_data)} peak={peak:.5f} rms={rms:.5f}")
    except Exception as e:
        print(f"SEG AUDIO DIAG ERROR: {e}")
        diag_data = None

    if diag_data is None or len(diag_data) == 0:
        result = process_call(call_id, None, interrupted_text=interrupted_text)
    else:
        trimmed = _trim_silence(temp_file)
        result = process_call(call_id, trimmed, interrupted_text=interrupted_text)
        if trimmed != temp_file and os.path.exists(trimmed):
            os.remove(trimmed)
    if os.path.exists(temp_file):
        os.remove(temp_file)

    bot_text = result.get("bot", "")
    hangup = result.get("hangup", False)

    segs = speak_segments(bot_text, "en", prefix=call_id)
    segments_json = []
    for text, path in segs:
        ulaw_bytes = _audio_to_ulaw(path)
        os.remove(path)
        segments_json.append({
            "text": text,
            "audio": base64.b64encode(ulaw_bytes).decode(),
        })

    resp = {"call_id": call_id, "segments": segments_json, "hangup": hangup}
    return Response(
        content=json.dumps(resp),
        media_type="application/json",
    )


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
        diag_data = None

    if diag_data is None or len(diag_data) == 0:
        result = process_call(call_id, None)
    else:
        trimmed = _trim_silence(temp_file)
        result = process_call(call_id, trimmed)
        if trimmed != temp_file and os.path.exists(trimmed):
            os.remove(trimmed)
    if os.path.exists(temp_file):
        os.remove(temp_file)

    if result.get("hangup"):
        out_path = result.get("audio")
        if out_path and os.path.exists(out_path):
            ulaw_bytes = _audio_to_ulaw(out_path)
            os.remove(out_path)
        else:
            ulaw_bytes = b""
        print("HANGUP SIGNALED")
        return Response(content=ulaw_bytes, media_type="audio/x-mulaw", headers={"X-Hangup": "true"})

    out_path = result.get("audio")
    if out_path and os.path.exists(out_path):
        ulaw_bytes = _audio_to_ulaw(out_path)
        os.remove(out_path)
        return Response(content=ulaw_bytes, media_type="audio/x-mulaw")

    return Response(
        content=_cached_greeting_ulaw,
        media_type="audio/x-mulaw",
    )

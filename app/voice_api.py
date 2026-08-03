import os
import io
import json
import wave
import base64
import audioop
import asyncio
import time
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from .session import create_session, start_conversation, end_conversation
from .conversation import process_call, process_support_call
from .memory import add_message
from .supertonic_engine import speak_segments, speak
from .config import MAX_CONCURRENT_CALLS, SAVE_CALLER_AUDIO
from . import db

AUDIO_DIR = "audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

GREETING_TEXT = (
    "Hello, this is BellaVita. "
    "You left something in your cart and we have a great offer on it today — want to hear about it?"
)

GREETING_TEXT_HI = (
    "हैलो, BellaVita से बोल रही हूँ। "
    "आपने कार्ट में जो प्रोडक्ट रखा था उस पर आज शानदार ऑफ़र है — सुनना चाहेंगे?"
)

SUPPORT_GREETING = (
    "Hello, this is BellaVita customer support. "
    "How can I help you today?"
)

# SUPPORT_GREETING_HI = (
#     "नमस्ते, BellaVita कस्टमर सपोर्ट से बोल रही हूँ। "
#     "बताइए, किस समस्या के लिए कॉल किया है?"
# )

_cached_greeting_ulaw: Optional[bytes] = None
_cached_greeting_segments: Optional[str] = None
_cached_greeting_ulaw_hi: Optional[bytes] = None
_cached_greeting_segments_hi: Optional[str] = None
_cached_support_greeting_segments: Optional[str] = None
# _cached_support_greeting_segments_hi: Optional[str] = None

app = FastAPI()


def _trim_silence(input_path: str, threshold: float = 0.01, padding: float = 0.15) -> str:
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
    global _cached_greeting_ulaw_hi, _cached_greeting_segments_hi
    global _cached_support_greeting_segments, _cached_support_greeting_segments_hi
    from .supertonic_engine import get_tts, speak

    print("PRELOAD: Loading TTS model...")
    get_tts()
    print("PRELOAD: TTS model ready.")

    path_en = f"{AUDIO_DIR}/_greeting_en.wav"
    if not os.path.exists(path_en):
        print("PRELOAD: Generating English greeting TTS...")
        speak(GREETING_TEXT, path_en, "en")
    _cached_greeting_ulaw = _audio_to_ulaw(path_en)

    print("PRELOAD: Preloading English greeting segments...")
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

    path_hi = f"{AUDIO_DIR}/_greeting_hi.wav"
    if not os.path.exists(path_hi):
        print("PRELOAD: Generating Hindi greeting TTS...")
        speak(GREETING_TEXT_HI, path_hi, "hi")
    _cached_greeting_ulaw_hi = _audio_to_ulaw(path_hi)

    print("PRELOAD: Preloading Hindi greeting segments...")
    segs_hi = speak_segments(GREETING_TEXT_HI, "hi", prefix="greeting_hi")
    segments_json_hi = []
    for text, seg_path in segs_hi:
        ulaw_bytes = _audio_to_ulaw(seg_path)
        os.remove(seg_path)
        segments_json_hi.append({
            "text": text,
            "audio": base64.b64encode(ulaw_bytes).decode(),
        })
    _cached_greeting_segments_hi = json.dumps(
        {"call_id": "", "segments": segments_json_hi, "hangup": False}
    )

    print("PRELOAD: Preloading English support greeting...")
    segs_sup = speak_segments(SUPPORT_GREETING, "en", prefix="support_greeting")
    segs_sup_json = []
    for text, seg_path in segs_sup:
        ulaw_bytes = _audio_to_ulaw(seg_path)
        os.remove(seg_path)
        segs_sup_json.append({
            "text": text,
            "audio": base64.b64encode(ulaw_bytes).decode(),
        })
    _cached_support_greeting_segments = json.dumps(
        {"call_id": "", "segments": segs_sup_json, "hangup": False}
    )

    # print("PRELOAD: Preloading Hindi support greeting...")
    # segs_sup_hi = speak_segments(SUPPORT_GREETING_HI, "hi", prefix="support_greeting_hi")
    # segs_sup_hi_json = []
    # for text, seg_path in segs_sup_hi:
    #     ulaw_bytes = _audio_to_ulaw(seg_path)
    #     os.remove(seg_path)
    #     segs_sup_hi_json.append({
    #         "text": text,
    #         "audio": base64.b64encode(ulaw_bytes).decode(),
    #     })
    # _cached_support_greeting_segments_hi = json.dumps(
    #     {"call_id": "", "segments": segs_sup_hi_json, "hangup": False}
    # )

    print("PRELOAD: All greetings cached.")


def _warmup_ollama():
    import requests
    from .config import OLLAMA_HOST, MODEL_NAME
    try:
        requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": "hi"}], "stream": False,
                  "options": {"num_predict": 1, "num_ctx": 1536}},
            timeout=60,
        )
        print("WARMUP: Ollama model loaded into GPU.")
    except Exception as e:
        print(f"WARMUP FAILED: {e}")


def _build_api_response(result: dict, call_id: str) -> Response:
    bot_text = result.get("bot", "")
    hangup = result.get("hangup", False)
    lang = result.get("lang", "en")
    pre_segments = result.get("segments", [])

    if hangup:
        end_conversation(call_id, status="hangup")

    if not bot_text or not bot_text.strip():
        if hangup:
            return Response(
                content=json.dumps({"call_id": call_id, "segments": [], "hangup": True}),
                media_type="application/json",
            )
        return Response(
            content=json.dumps({"call_id": call_id, "segments": [], "hangup": False}),
            media_type="application/json",
        )

    if pre_segments:
        segments_json = []
        for text, path in pre_segments:
            if os.path.exists(path):
                ulaw_bytes = _audio_to_ulaw(path)
                os.remove(path)
                segments_json.append({
                    "text": text,
                    "audio": base64.b64encode(ulaw_bytes).decode(),
                })
        resp = {"call_id": call_id, "segments": segments_json, "hangup": hangup}
        print(f"API RESPONSE (pre-gen): hangup={hangup} bot_text_len={len(bot_text)} segments={len(segments_json)}")
        return Response(content=json.dumps(resp), media_type="application/json")

    from .conversation import _get_tts_lang
    tts_lang = _get_tts_lang(lang, bot_text)
    segs = speak_segments(bot_text, tts_lang, prefix=call_id)
    segments_json = []
    for text, path in segs:
        ulaw_bytes = _audio_to_ulaw(path)
        os.remove(path)
        segments_json.append({
            "text": text,
            "audio": base64.b64encode(ulaw_bytes).decode(),
        })

    resp = {"call_id": call_id, "segments": segments_json, "hangup": hangup}
    print(f"API RESPONSE: hangup={hangup} bot_text_len={len(bot_text)} segments={len(segments_json)}")
    return Response(content=json.dumps(resp), media_type="application/json")


_PROCESS_SEMAPHORE = asyncio.Semaphore(max(1, MAX_CONCURRENT_CALLS))


async def _process_segmented(process_fn, call_id, audio_path, interrupted_text=None):
    """Run STT/LLM/TTS + response building off the event loop, bounded."""
    loop = asyncio.get_running_loop()

    def _run():
        result = process_fn(call_id, audio_path, interrupted_text=interrupted_text)
        return _build_api_response(result, call_id)

    async with _PROCESS_SEMAPHORE:
        return await loop.run_in_executor(None, _run)


async def _process_plain(process_fn, call_id, audio_path):
    loop = asyncio.get_running_loop()
    async with _PROCESS_SEMAPHORE:
        return await loop.run_in_executor(
            None, lambda: process_fn(call_id, audio_path, None)
        )


async def _save_caller_audio(call_id: str, raw: bytes):
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, db.save_caller_audio, call_id, raw)
    except Exception as e:
        print(f"SAVE CALLER AUDIO ERROR: {e}")


@app.on_event("startup")
async def startup():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _preload_greeting)
    await loop.run_in_executor(None, _warmup_ollama)
    await loop.run_in_executor(None, db.init_db)

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(300)
            from .session import cleanup_expired
            await loop.run_in_executor(None, cleanup_expired)

    asyncio.create_task(_cleanup_loop())


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
    active = int(np.sum(np.abs(data) > 0.003))
    active_ms = active / sr * 1000
    speech = rms > 0.008 and active_ms > 60
    print(f"CHECK-SPEECH: rms={rms:.5f} active={active_ms:.0f}ms "
          f"speech={speech} len={len(data)}")
    return {"speech_detected": speech, "rms": rms}


@app.post("/voice-audio-segmented")
async def voice_audio_segmented(
    audio: Optional[UploadFile] = File(None),
    call_id: str = Form(None),
    outbound: bool = Form(False),
    inbound: bool = Form(False),
    interrupted_text: Optional[str] = Form(None),
    lang: Optional[str] = Form(None),
):
    if not call_id:
        call_id = create_session()

    print(f"SEG-API: call_id={call_id} outbound={outbound} inbound={inbound} interrupted_text='{interrupted_text}' lang='{lang}'")

    if outbound:
        greeting_lang = lang if lang in ("hi", "en") else "en"
        greeting_text = GREETING_TEXT_HI if greeting_lang == "hi" else GREETING_TEXT
        t0 = time.monotonic()
        start_conversation(call_id, agent_type="sales", direction="outbound", lang=greeting_lang)
        t1 = time.monotonic()
        add_message(call_id, "assistant", greeting_text)
        t2 = time.monotonic()
        print(f"GREETING (outbound): start_conv={t1-t0:.3f}s add_msg={t2-t1:.3f}s")
        cached = _cached_greeting_segments_hi if greeting_lang == "hi" else _cached_greeting_segments
        data = json.loads(cached)
        data["call_id"] = call_id
        return Response(
            content=json.dumps(data),
            media_type="application/json",
        )

    if inbound and audio is None:
        t0 = time.monotonic()
        start_conversation(call_id, agent_type="support", direction="inbound", lang="en")
        t1 = time.monotonic()
        add_message(call_id, "assistant", SUPPORT_GREETING)
        t2 = time.monotonic()
        print(f"GREETING (inbound): start_conv={t1-t0:.3f}s add_msg={t2-t1:.3f}s")
        data = json.loads(_cached_support_greeting_segments)
        data["call_id"] = call_id
        return Response(
            content=json.dumps(data),
            media_type="application/json",
        )

    # Select conversation flow
    _process = process_support_call if inbound else process_call
    start_conversation(
        call_id,
        agent_type="support" if inbound else "sales",
        direction="inbound" if inbound else "outbound",
        lang=None,
    )

    if audio is None:
        raise HTTPException(status_code=400, detail="Audio file missing")

    temp_file = f"{AUDIO_DIR}/{call_id}_in.wav"
    try:
        raw = await audio.read()
        if not raw or len(raw) < 44:
            print(f"SEG AUDIO: empty or too small ({len(raw)} bytes) — treating as silent")
            return await _process_segmented(_process, call_id, None, interrupted_text)
        with open(temp_file, "wb") as f:
            f.write(raw)
        if SAVE_CALLER_AUDIO:
            await _save_caller_audio(call_id, raw)
    except Exception as e:
        print(f"SEG AUDIO WRITE ERROR: {e}")
        return await _process_segmented(_process, call_id, None, interrupted_text)

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
        result = await _process_segmented(_process, call_id, None, interrupted_text)
    else:
        active_thresh = max(0.005, np.std(diag_data) * 1.5)
        active = int(np.sum(np.abs(diag_data if len(diag_data.shape) == 1 else diag_data.mean(axis=1)) > active_thresh))
        active_ms = active / max(diag_sr, 1) * 1000
        min_active = 50 if interrupted_text else 120
        treat_silent = rms < 0.005 or active_ms < min_active
        print(f"SEG NOISE CHECK: rms={rms:.5f} active={active_ms:.0f}ms active_thresh={active_thresh:.4f} treat_silent={treat_silent}")
        if treat_silent:
            result = await _process_segmented(_process, call_id, None, interrupted_text)
        else:
            trimmed = _trim_silence(temp_file, threshold=0.01, padding=0.15)
            result = await _process_segmented(_process, call_id, trimmed, interrupted_text)
            if trimmed != temp_file and os.path.exists(trimmed):
                os.remove(trimmed)
    if os.path.exists(temp_file):
        os.remove(temp_file)

    return result


@app.post("/voice-audio")
async def voice_audio(
    audio: Optional[UploadFile] = File(None),
    call_id: str = Form(None),
    outbound: bool = Form(False),
):
    if not call_id:
        call_id = create_session()

    if outbound:
        start_conversation(call_id, agent_type="sales", direction="outbound", lang="en")
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
        result = await _process_plain(process_call, call_id, None)
    else:
        trimmed = _trim_silence(temp_file)
        result = await _process_plain(process_call, call_id, trimmed)
        if trimmed != temp_file and os.path.exists(trimmed):
            os.remove(trimmed)
    if os.path.exists(temp_file):
        os.remove(temp_file)

    if result.get("hangup"):
        end_conversation(call_id, status="hangup")
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


@app.get("/agents")
def get_agents():
    from .agents import list_agents
    return {"agents": list_agents()}


@app.get("/conversations")
def get_conversations(limit: int = 50):
    return {"conversations": db.list_conversations(limit)}


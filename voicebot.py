#!/usr/bin/env python3

import sys
import json
import base64
import wave as wavemod
import requests
import traceback
import os
import uuid

API_BASE = "http://127.0.0.1:8000"
RECORD_DIR = "/var/lib/asterisk/sounds/voicebot"
PLAYBACK_DIR = "/usr/share/asterisk/sounds/voicebot"


def log(msg):
    with open(f"{RECORD_DIR}/voicebot.log", "a") as f:
        f.write(str(msg) + "\n")


def agi_cmd(cmd):
    print(cmd)
    sys.stdout.flush()
    return sys.stdin.readline().strip()


def get_segments(call_id, audio_path=None, interrupted_text=None):
    if audio_path:
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/voice-audio-segmented",
                files={"audio": f},
                data={
                    "call_id": call_id,
                    "interrupted_text": interrupted_text or "",
                },
                timeout=120,
            )
    else:
        r = requests.post(
            f"{API_BASE}/voice-audio-segmented",
            data={
                "call_id": call_id,
                "outbound": "true",
            },
            timeout=120,
        )
    if r.status_code != 200:
        log(f"API ERROR: {r.status_code} {r.text}")
        return None
    return r.json()


def check_speech(audio_path):
    with open(audio_path, "rb") as f:
        r = requests.post(
            f"{API_BASE}/check-speech",
            files={"audio": f},
            timeout=10,
        )
    if r.status_code == 200:
        return r.json().get("speech_detected", False)
    return False


def check_early_speech(wav_path, window_ms=200, threshold=0.005):
    """Check if the first window_ms of a WAV file contains speech."""
    try:
        with wavemod.open(wav_path, 'rb') as w:
            if w.getframerate() == 0 or w.getnframes() == 0:
                return False
            n_frames = int(w.getframerate() * window_ms / 1000)
            n_frames = min(n_frames, w.getnframes())
            frames = w.readframes(n_frames)
            if not frames or len(frames) < 4:
                return False
            samples = []
            for i in range(0, len(frames), 2):
                sample = int.from_bytes(frames[i:i+2], 'little', signed=True)
                samples.append(sample)
            if not samples:
                return False
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
            rms /= 32768.0
            return rms > threshold
    except Exception:
        return False


def concat_wavs(paths, output_path):
    frames = []
    rate = 8000
    for p in paths:
        with wavemod.open(p, "rb") as w:
            if w.getframerate() != rate:
                log(f"WARN: mismatched sample rate in {p}")
            frames.append(w.readframes(w.getnframes()))
    with wavemod.open(output_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))


def detect_voice_bargein(call_id):
    check_file = f"{RECORD_DIR}/{call_id}_check"
    result = agi_cmd(f'RECORD FILE {check_file} wav "" 400')
    log(f"CHECK RECORD={result}")

    if "result=-1" in result:
        return "hangup", None

    check_path = f"{check_file}.wav"
    if not os.path.exists(check_path):
        return "ok", None

    speech = check_speech(check_path)
    if speech:
        log("VOICE BARGE-IN CONFIRMED")
        return "bargein", check_path

    os.remove(check_path)
    return "ok", None


def play_segments(data, call_id):
    segments = data.get("segments", [])
    hangup = data.get("hangup", False)

    for i, seg in enumerate(segments):
        seg_audio = base64.b64decode(seg["audio"])
        seg_path = f"{PLAYBACK_DIR}/{call_id}_seg_{i}.ulaw"
        with open(seg_path, "wb") as f:
            f.write(seg_audio)

        result = agi_cmd(f'STREAM FILE voicebot/{call_id}_seg_{i} "#*0-9"')
        os.remove(seg_path)
        log(f"STREAM RESULT seg{i}={result}")

        if "result=-1" in result:
            log("PLAYBACK FAILED (hangup)")
            return "hangup", None, None

        if "digit=" in result:
            log(f"DTMF BARGE-IN on segment {i}")
            return "bargein", seg["text"], None

        if i < len(segments) - 1:
            result_type, check_path = detect_voice_bargein(call_id)
            if result_type == "hangup":
                return "hangup", None, None
            if result_type == "bargein":
                log(f"VOICE BARGE-IN after segment {i}")
                return "bargein", seg["text"], check_path

    last_text = segments[-1]["text"] if segments else None
    if hangup:
        return "hangup", None, None
    return "ok", last_text, None


def is_audio_empty(path):
    try:
        with wavemod.open(path, "rb") as w:
            return w.getnframes() == 0
    except Exception:
        return True


def record_caller(call_id, bargein_check_path=None):
    rec_file = f"{RECORD_DIR}/{call_id}_caller"
    result = agi_cmd(f'RECORD FILE {rec_file} wav "#" 3000')
    log(f"RECORD RESULT={result}")

    if "result=-1" in result:
        log("CALL HUNG UP DURING RECORD")
        return None

    rec_path = f"{rec_file}.wav"
    if not os.path.exists(rec_path) or os.path.getsize(rec_path) < 100:
        log("RECORD FILE MISSING OR EMPTY")
        if bargein_check_path and os.path.exists(bargein_check_path):
            log("FALLING BACK to barge-in check file as main recording")
            return bargein_check_path
        return None

    if bargein_check_path and os.path.exists(bargein_check_path):
        if is_audio_empty(rec_path):
            log("MAIN RECORDING EMPTY, using check file alone")
            os.remove(rec_path)
            return bargein_check_path
        merged_path = f"{rec_file}_merged.wav"
        concat_wavs([bargein_check_path, rec_path], merged_path)
        os.remove(rec_path)
        os.remove(bargein_check_path)
        log("MERGED barge-in check + full recording")
        return merged_path

    return rec_path


try:
    log("START WITH INTERRUPT HANDLING")

    agi_env = {}
    while True:
        line = sys.stdin.readline().strip()
        if not line:
            break
        if ":" in line:
            k, v = line.split(":", 1)
            agi_env[k.strip()] = v.strip()

    call_id = str(uuid.uuid4())
    log(f"CALL_ID={call_id}")

    log("REQUESTING SEGMENTED GREETING")
    greeting_data = get_segments(call_id)
    if greeting_data is None:
        log("FAILED TO GET GREETING")
        raise SystemExit(1)

    status, interrupted_text, check_path = play_segments(greeting_data, call_id)
    if status == "hangup":
        log("HUNG UP DURING GREETING")
        raise SystemExit(0)

    while True:
        log("WAITING FOR SPEECH")

        if status == "bargein":
            log("RECORDING AFTER BARGE-IN")
            rec_path = record_caller(call_id, bargein_check_path=check_path)
        else:
            rec_path = record_caller(call_id)

        if rec_path is None:
            log("NO RECORDING - ending call")
            break

        log(f"SENDING TO API call_id={call_id}")

        if status != "bargein" and interrupted_text and check_early_speech(rec_path):
            log(f"POST-HOC BARGE-IN: early speech in recording")
            status = "bargein"

        if status == "bargein":
            api_data = get_segments(
                call_id,
                audio_path=rec_path,
                interrupted_text=interrupted_text,
            )
        else:
            api_data = get_segments(call_id, audio_path=rec_path)

        os.remove(rec_path)

        if api_data is None:
            log("API FAILED")
            break

        status, interrupted_text, check_path = play_segments(api_data, call_id)

        if status == "hangup":
            log("HANGUP RECEIVED FROM API")
            agi_cmd("HANGUP")
            break

        log("TURN COMPLETE, waiting for next speech")

except Exception:
    log(traceback.format_exc())

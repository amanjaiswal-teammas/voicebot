#!/usr/bin/env python3

import sys
import json
import base64
import wave as wavemod
import requests
import traceback
import os
import uuid
import time

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


def _api_post(url, max_retries=3, retry_delay=2, **kwargs):
    for attempt in range(max_retries):
        try:
            r = requests.post(url, timeout=120, **kwargs)
            return r
        except requests.exceptions.ConnectionError as e:
            log(f"API CONNECTION ERROR (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    return None


def get_segments(call_id, audio_path=None, interrupted_text=None, lang=None, inbound=False):
    if audio_path:
        data = {
            "call_id": call_id,
            "interrupted_text": interrupted_text or "",
        }
        if inbound:
            data["inbound"] = "true"
        with open(audio_path, "rb") as f:
            r = _api_post(
                f"{API_BASE}/voice-audio-segmented",
                files={"audio": f},
                data=data,
            )
    else:
        post_data = {
            "call_id": call_id,
        }
        if inbound:
            post_data["inbound"] = "true"
        else:
            post_data["outbound"] = "true"
        if lang:
            post_data["lang"] = lang
        r = _api_post(
            f"{API_BASE}/voice-audio-segmented",
            data=post_data,
        )
    if r is None:
        return None
    if r.status_code != 200:
        log(f"API ERROR: {r.status_code} {r.text}")
        return None
    return r.json()


def get_segments_stream(call_id, audio_path, interrupted_text=None, inbound=False):
    """POST the recording and get a streaming NDJSON response (one segment per line)."""
    data = {
        "call_id": call_id,
        "interrupted_text": interrupted_text or "",
    }
    if inbound:
        data["inbound"] = "true"
    try:
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/voice-audio-stream",
                files={"audio": f},
                data=data,
                stream=True,
                timeout=120,
            )
    except requests.exceptions.ConnectionError as e:
        log(f"STREAM API CONNECTION ERROR: {e}")
        return None
    if r.status_code != 200:
        log(f"STREAM API ERROR: {r.status_code} {r.text}")
        return None
    return r


def play_stream_response(resp, call_id):
    """Play segments as they arrive over NDJSON.

    Returns (status, interrupted_text, check_path) like play_segments.
    """
    last_text = None
    played = 0
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            segs = data.get("segments", [])
            done = data.get("done", False)

            for i, seg in enumerate(segs):
                text = seg.get("text", "")
                audio_b64 = seg.get("audio", "")
                if not audio_b64:
                    continue
                seg_path = f"{PLAYBACK_DIR}/{call_id}_seg_{played}.ulaw"
                with open(seg_path, "wb") as f:
                    f.write(base64.b64decode(audio_b64))
                result = agi_cmd(f'STREAM FILE voicebot/{call_id}_seg_{played} "#*0-9"')
                try:
                    os.remove(seg_path)
                except Exception:
                    pass
                log(f"STREAM RESULT seg{played}={result}")
                last_text = text
                played += 1

                if "result=-1" in result:
                    return "hangup", None, None
                if "digit=" in result:
                    log(f"DTMF BARGE-IN on segment {played - 1}")
                    return "bargein", text, None

                is_last = done and i == len(segs) - 1
                if not is_last:
                    result_type, check_path = detect_voice_bargein(call_id)
                    if result_type == "hangup":
                        return "hangup", None, None
                    if result_type == "bargein":
                        log(f"VOICE BARGE-IN after segment {played - 1}")
                        return "bargein", text, check_path

            if done:
                hangup = data.get("hangup", False)
                if hangup:
                    return "hangup", None, None
                return "ok", last_text, None
    except Exception as e:
        log(f"STREAM PLAYBACK ERROR: {e}")
        return "ok", last_text, None
    return "ok", last_text, None


def check_speech(audio_path):
    try:
        with open(audio_path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/check-speech",
                files={"audio": f},
                timeout=10,
            )
        if r.status_code == 200:
            return r.json().get("speech_detected", False)
    except requests.exceptions.ConnectionError:
        pass
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
    result = agi_cmd(f'RECORD FILE {check_file} wav "" 250')
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


def wav_rms(path):
    try:
        with wavemod.open(path, "rb") as w:
            frames = w.readframes(w.getnframes())
        if not frames:
            return 0.0
        samples = []
        for i in range(0, len(frames), 2):
            samples.append(int.from_bytes(frames[i:i + 2], "little", signed=True))
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return rms / 32768.0
    except Exception:
        return 0.0


SILENCE_RMS = 0.005


def record_caller(call_id, bargein_check_path=None):
    rec_file = f"{RECORD_DIR}/{call_id}_caller"
    chunk_ms = 300
    max_chunks = 10
    min_chunks = 6
    quiet_streak_max = 2

    chunk_paths = []
    quiet_streak = 0
    any_speech = False

    for i in range(max_chunks):
        part = f"{rec_file}_p{i}"
        result = agi_cmd(f'RECORD FILE {part} wav "#" {chunk_ms}')
        log(f"RECORD CHUNK {i} RESULT={result}")

        if "result=-1" in result:
            log("CALL HUNG UP DURING RECORD")
            break

        p = f"{part}.wav"
        if not os.path.exists(p) or os.path.getsize(p) < 100:
            log(f"RECORD CHUNK {i} MISSING OR EMPTY")
            break

        chunk_paths.append(p)
        rms = wav_rms(p)
        log(f"RECORD CHUNK {i}: rms={rms:.4f}")

        if rms > SILENCE_RMS:
            any_speech = True
            quiet_streak = 0
        else:
            quiet_streak += 1

        if "dtmf" in result.lower():
            log(f"RECORD CHUNK {i} ENDED BY DIGIT")
            break

        if quiet_streak >= quiet_streak_max and (any_speech or i + 1 >= min_chunks):
            log(f"RECORD CHUNK {i}: silence detected, stopping")
            break

    if not chunk_paths:
        log("NO RECORD CHUNKS")
        if bargein_check_path and os.path.exists(bargein_check_path):
            log("FALLING BACK to barge-in check file as main recording")
            return bargein_check_path
        return None

    rec_path = f"{rec_file}.wav"
    concat_wavs(chunk_paths, rec_path)
    for p in chunk_paths:
        try:
            os.remove(p)
        except Exception:
            pass

    try:
        with wavemod.open(rec_path, "rb") as w:
            log(f"RECORD DURATION: {w.getnframes()/w.getframerate():.2f}s")
    except Exception:
        pass

    if not any_speech:
        log("MAIN RECORDING SILENT")

    if bargein_check_path and os.path.exists(bargein_check_path):
        if not any_speech:
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
    call_type = agi_env.get("agi_arg_1", "").strip().lower()
    inbound = call_type == "inbound"
    log(f"CALL_ID={call_id} TYPE={'inbound' if inbound else 'outbound'}")

    greeting_lang = "en"
    log(f"GREETING LANG: {greeting_lang}")

    log("REQUESTING SEGMENTED GREETING")
    greeting_data = get_segments(call_id, lang=greeting_lang, inbound=inbound)
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
            stream_resp = get_segments_stream(
                call_id,
                rec_path,
                interrupted_text=interrupted_text,
                inbound=inbound,
            )
        else:
            stream_resp = get_segments_stream(call_id, rec_path, inbound=inbound)

        os.remove(rec_path)

        if stream_resp is None:
            log("API FAILED")
            break

        status, interrupted_text, check_path = play_stream_response(stream_resp, call_id)
        try:
            stream_resp.close()
        except Exception:
            pass

        if status == "hangup":
            log("HANGUP RECEIVED FROM API")
            agi_cmd("HANGUP")
            break

        log("TURN COMPLETE, waiting for next speech")

except Exception:
    log(traceback.format_exc())

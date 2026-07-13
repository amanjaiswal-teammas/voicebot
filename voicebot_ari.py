#!/usr/bin/env python3
"""
ARI-based voicebot handler with Silero VAD barge-in.

Plays TTS segments, then does a short ARI recording between segments.
Silero VAD checks the recording for customer speech.
If speech detected → barge-in, use that recording for STT.

Required: pip install aiohttp numpy silero-vad onnxruntime

Asterisk config changes (see ari_setup/):
  http.conf     -> enable HTTP server on port 8088
  ari.conf      -> create 'voicebot' user with read=all write=all
  extensions.conf -> route calls to Stasis(voicebot)
"""

import asyncio
import aiohttp
import audioop
import json
import base64
import os
import sys
import time
import uuid
import struct
import wave as _wave
import numpy as np
import logging
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
API_BASE = "http://127.0.0.1:8000"
ARI_BASE = "http://127.0.0.1:8088"
ARI_USER = "voicebot"
ARI_PASS = "voicebot_secret"
ARI_APP = "voicebot"

RECORD_DIR = "/var/spool/asterisk/recording"
PLAYBACK_DIR = "/usr/share/asterisk/sounds/voicebot"
LOG_FILE = "/var/log/voicebot_ari.log"

os.makedirs(RECORD_DIR, exist_ok=True)
os.makedirs(PLAYBACK_DIR, exist_ok=True)

try:
    import stat
    os.chmod(RECORD_DIR, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
             | stat.S_ISGID)
    os.chmod(PLAYBACK_DIR, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
             | stat.S_IROTH | stat.S_IXOTH)
except Exception:
    pass

logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filemode="a",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  Silero VAD
# ═══════════════════════════════════════════════════════════════════════

_silero_model = None
_silero_utils = None


def _load_silero():
    global _silero_model, _silero_utils
    if _silero_model is not None:
        return
    try:
        from silero_vad import load_silero_vad, get_speech_timestamps
        _silero_model = load_silero_vad(onnx=True)
        _silero_utils = get_speech_timestamps
        log.info("Silero VAD loaded (onnx)")
    except Exception as e:
        log.warning(f"Silero VAD load failed: {e}, falling back to RMS")
        _silero_model = False


def _check_speech_silero(wav_path: str) -> bool:
    """Return True if speech is detected in the WAV file.

    Uses Silero VAD (ONNX) with RMS fallback.
    """
    _load_silero()

    if _silero_model is False or _silero_model is None:
        return _check_speech_rms(wav_path)

    try:
        import torch
        get_speech_timestamps = _silero_utils

        with _wave.open(wav_path, "rb") as w:
            rate = w.getframerate()
            nframes = w.getnframes()
            if nframes == 0:
                return False
            raw = w.readframes(nframes)

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if rate != 16000:
            from scipy.signal import resample_poly
            g = int(np.gcd(16000, rate))
            samples = resample_poly(samples, 16000 // g, rate // g).astype(np.float32)

        tensor = torch.from_numpy(samples)
        speech_ts = get_speech_timestamps(
            tensor, _silero_model,
            sampling_rate=16000,
            threshold=0.4,
            min_speech_duration_ms=80,
            min_silence_duration_ms=50,
        )
        total_speech_ms = sum(s["end"] - s["start"] for s in speech_ts) * 1000 // 16000
        log.info(f"Silero: speech_ms={total_speech_ms} segments={len(speech_ts)}")
        return total_speech_ms >= 80
    except Exception as e:
        log.warning(f"Silero VAD error: {e}, falling back to RMS")
        return _check_speech_rms(wav_path)


def _check_speech_rms(wav_path: str) -> bool:
    """Simple RMS-based speech detection (fallback)."""
    try:
        with _wave.open(wav_path, "rb") as w:
            nframes = w.getnframes()
            rate = w.getframerate()
            sw = w.getsampwidth()
            if nframes == 0 or rate == 0:
                return False
            frames = w.readframes(nframes)
        if len(frames) < sw:
            return False
        n = len(frames) // sw
        fmt = "<" + "h" * n
        samples = struct.unpack(fmt, frames[:n * sw])
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0
        active = sum(1 for s in samples if abs(s) > 160)
        active_ms = active / rate * 1000
        log.info(f"RMS fallback: rms={rms:.5f} active_ms={active_ms:.0f}")
        return rms > 0.01 and active_ms > 100
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
#  ARI REST client
# ═══════════════════════════════════════════════════════════════════════

class ARIClient:
    def __init__(self):
        self._auth = aiohttp.BasicAuth(ARI_USER, ARI_PASS)
        self._session = None

    async def __aenter__(self):
        self._session = await aiohttp.ClientSession().__aenter__()
        return self

    async def __aexit__(self, *args):
        await self._session.__aexit__(*args)

    async def _req(self, method, path, **kw):
        url = f"{ARI_BASE}/ari{path}"
        async with self._session.request(method, url, auth=self._auth,
                                         **kw) as r:
            if r.status >= 400:
                text = (await r.read())[:200]
                log.warning(f"ARI {r.status} {method} {path}: {text}")
                return None
            if r.status == 204:
                return {}
            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                return await r.json()
            return await r.read()

    async def get(self, path):
        return await self._req("GET", path)

    async def post(self, path, **kw):
        return await self._req("POST", path, **kw)

    async def delete(self, path):
        return await self._req("DELETE", path)

    # ── High-level ──────────────────────────────────────────────

    async def answer(self, cid):
        return await self.post(f"/channels/{cid}/answer")

    async def hangup(self, cid):
        return await self.delete(f"/channels/{cid}")

    async def play(self, cid, media, pid=None):
        body = {"media": media}
        if pid:
            body["playbackId"] = pid
        return await self.post(f"/channels/{cid}/play", json=body)

    async def stop_playback(self, pid):
        return await self.delete(f"/playbacks/{pid}")

    async def playback_state(self, pid):
        r = await self.get(f"/playbacks/{pid}")
        return r.get("state") if r else None

    async def record(self, cid, name, fmt="wav", max_dur=10, max_sil=2):
        params = {
            "name": name, "format": fmt,
            "maxDurationSeconds": max_dur,
            "maxSilenceSeconds": max_sil,
            "ifExists": "overwrite", "terminateOn": "#",
        }
        return await self.post(f"/channels/{cid}/record", json=params)

    async def stop_recording(self, name):
        return await self.delete(f"/recordings/stored/{name}")

    async def get_recording(self, name):
        return await self._req("GET", f"/recordings/stored/{name}/file")


# ═══════════════════════════════════════════════════════════════════════
#  Call handler
# ═══════════════════════════════════════════════════════════════════════

def _cleanup(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


class CallHandler:
    """Manages the conversation loop for a single call."""

    def __init__(self, ari: ARIClient, channel_id: str, args: list):
        self.ari = ari
        self.channel_id = channel_id
        self.args = args
        self.call_id = str(uuid.uuid4())
        self._http: aiohttp.ClientSession = None

    # ── Backend API ─────────────────────────────────────────────

    async def _api(self, audio_path=None, interrupted_text=None):
        url = f"{API_BASE}/voice-audio-segmented"
        form = aiohttp.FormData()
        form.add_field("call_id", self.call_id)
        if interrupted_text:
            form.add_field("interrupted_text", interrupted_text)
        if audio_path:
            form.add_field("audio", open(audio_path, "rb"),
                           filename="audio.wav", content_type="audio/wav")
        else:
            form.add_field("outbound", "true")
        async with self._http.post(url, data=form) as r:
            if r.status != 200:
                log.warning(f"API {r.status} for {url}")
                return None
            data = await r.json()
            segs = data.get("segments", [])
            log.info(f"API returned {len(segs)} segments")
            return data

    # ── Play one segment ────────────────────────────────────────

    async def _play_segment(self, seg_data: dict, idx: int):
        """Play one segment via ARI. Returns (ok, text, playback_id)."""
        text = seg_data["text"]
        audio_b64 = seg_data["audio"]

        seg_name = f"{self.call_id}_seg_{idx}"
        seg_path = f"{PLAYBACK_DIR}/{seg_name}.wav"

        ulaw_raw = base64.b64decode(audio_b64)
        pcm = audioop.ulaw2lin(ulaw_raw, 2)
        with _wave.open(seg_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(pcm)
        try:
            os.chmod(seg_path, 0o644)
        except Exception:
            pass
        log.info(f"Seg {idx} written: {seg_path} ({os.path.getsize(seg_path)} bytes)")

        await asyncio.sleep(0.05)

        pb_id = f"pb_{seg_name}"
        media_uri = f"sound:voicebot/{seg_name}"
        pb = await self.ari.play(self.channel_id, media_uri, pb_id)
        if pb is None:
            log.warning(f"Seg {idx} play POST returned None")
            _cleanup(seg_path)
            return True, text, None

        log.info(f"Seg {idx} play ok: state={pb.get('state')}")
        return True, text, pb_id

    async def _wait_playback(self, pb_id: str):
        """Poll until playback completes or fails."""
        checks = 0
        while True:
            await asyncio.sleep(0.1)
            state = await self.ari.playback_state(pb_id)
            checks += 1
            if checks <= 3 or checks % 10 == 0:
                log.info(f"Playback {pb_id}: state={state} check={checks}")
            if state in ("done", "failed", None):
                return

    # ── Short recording check (between segments) ────────────────

    async def _record_check(self, max_dur: float = 2.0,
                            max_sil: float = 0.8) -> str:
        """Do a short ARI recording and return the WAV path (or None).

        Used between segments to detect customer speech (barge-in).
        """
        rec_name = f"{self.call_id}_chk_{int(time.time() * 1000)}"
        result = await self.ari.record(
            self.channel_id, rec_name,
            max_dur=max_dur, max_sil=max_sil,
        )
        if result is None:
            return None

        # Wait for recording to finish
        for _ in range(int(max_dur * 10) + 5):
            await asyncio.sleep(0.1)
            r = await self.ari.get(f"/recordings/live/{rec_name}")
            if r is None:
                break
            state = r.get("state", "")
            if state in ("done", "cancelled", "failed"):
                break

        raw = await self.ari.get_recording(rec_name)
        if raw is None or len(raw) < 100:
            return None

        out = f"{RECORD_DIR}/{rec_name}.wav"
        with open(out, "wb") as f:
            f.write(raw)
        log.info(f"Record check: {out} ({len(raw)} bytes)")
        return out

    # ── Play all segments with between-segment barge-in check ───

    async def play_segments(self, segments: list):
        """Play all segments; return (interrupted_text, recording_path).

        Between each segment, a short recording checks for customer speech.
        If speech detected → barge-in: returns the recording for STT.
        """
        interrupted_text = None
        bargein_recording = None

        for i, seg in enumerate(segments):
            ok, text, pb_id = await self._play_segment(seg, i)
            if pb_id is None:
                continue

            await self._wait_playback(pb_id)
            _cleanup(f"{PLAYBACK_DIR}/{self.call_id}_seg_{i}.wav")

            if i == len(segments) - 1:
                interrupted_text = text
                break

            # Between segments: short recording to detect customer speech
            rec_path = await self._record_check(max_dur=2.0, max_sil=0.8)
            if rec_path and _check_speech_silero(rec_path):
                log.info(f"BARGE-IN detected after seg {i}: [{text}]")
                interrupted_text = text
                bargein_recording = rec_path
                break
            if rec_path:
                _cleanup(rec_path)

        return interrupted_text, bargein_recording

    # ── Full caller recording ───────────────────────────────────

    async def record_caller(self) -> str:
        """Record the caller and return the WAV path (or None)."""
        rec_name = f"{self.call_id}_caller"
        result = await self.ari.record(
            self.channel_id, rec_name, max_dur=10, max_sil=2
        )
        if result is None:
            log.warning("record start failed")
            return None

        for _ in range(200):
            await asyncio.sleep(0.1)
            r = await self.ari.get(f"/recordings/live/{rec_name}")
            if r is None:
                break
            state = r.get("state", "")
            if state in ("done", "cancelled", "failed"):
                break

        raw = await self.ari.get_recording(rec_name)
        if raw is None or len(raw) < 100:
            log.warning("recording empty or download failed")
            return None

        out = f"{RECORD_DIR}/{rec_name}_dl.wav"
        with open(out, "wb") as f:
            f.write(raw)
        log.info(f"Caller recording: {out} ({len(raw)} bytes)")
        return out

    # ── Main conversation loop ──────────────────────────────────

    async def run(self):
        log.info(f"CALL={self.call_id} CHAN={self.channel_id} START")
        self._http = aiohttp.ClientSession()
        try:
            await self.ari.answer(self.channel_id)

            # Greeting
            data = await self._api()
            if not data:
                await self.ari.hangup(self.channel_id)
                return

            interrupted_text, bargein_rec = await self.play_segments(
                data.get("segments", [])
            )

            # Conversation loop
            while True:
                # If we have a barge-in recording, use it directly
                if bargein_rec:
                    rec_path = bargein_rec
                    bargein_rec = None
                else:
                    rec_path = await self.record_caller()
                    if rec_path is None:
                        break

                data = await self._api(rec_path, interrupted_text)
                _cleanup(rec_path)

                if not data:
                    break

                interrupted_text, bargein_rec = await self.play_segments(
                    data.get("segments", [])
                )
                if data.get("hangup"):
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception(f"CALL={self.call_id} ERROR: {e}")
        finally:
            try:
                await self._http.close()
            except Exception:
                pass
            try:
                await self.ari.hangup(self.channel_id)
            except Exception:
                pass
            log.info(f"CALL={self.call_id} END")


# ═══════════════════════════════════════════════════════════════════════
#  ARI event listener
# ═══════════════════════════════════════════════════════════════════════

async def _events_loop(ari: ARIClient):
    """Connect to ARI WS and dispatch events, with auto-reconnect."""
    ws_url = (f"ws://127.0.0.1:8088/ari/events"
              f"?app={ARI_APP}&api_key={ARI_USER}:{ARI_PASS}")
    while True:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(ws_url, heartbeat=30) as ws:
                    log.info(f"ARI WS connected (app={ARI_APP})")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                events = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(events, list):
                                events = [events]
                            for ev in events:
                                et = ev.get("type")
                                if et == "StasisStart":
                                    ch = ev.get("channel", {})
                                    cid = ch.get("id")
                                    args = ev.get("args", [])
                                    log.info(f"StasisStart channel={cid}")
                                    h = CallHandler(ari, cid, args)
                                    asyncio.ensure_future(h.run())
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                          aiohttp.WSMsgType.ERROR):
                            break
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning(f"ARI WS disconnected: {e}")
        log.info("ARI WS reconnecting in 2s...")
        await asyncio.sleep(2)


async def main():
    log.info("=" * 50)
    log.info("Starting voicebot ARI handler (Silero VAD)")
    log.info("=" * 50)

    # Pre-load Silero VAD
    _load_silero()

    # Diagnostic: verify playback directory
    test_path = f"{PLAYBACK_DIR}/_probe.wav"
    try:
        with _wave.open(test_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * 800)
        sz = os.path.getsize(test_path)
        log.info(f"Probe file written: {test_path} ({sz} bytes)")
        os.remove(test_path)
    except Exception as e:
        log.error(f"Cannot write to PLAYBACK_DIR {PLAYBACK_DIR}: {e}")

    async with ARIClient() as ari:
        try:
            async with aiohttp.ClientSession() as sess:
                url = f"{ARI_BASE}/ari/ping"
                auth = aiohttp.BasicAuth(ARI_USER, ARI_PASS)
                async with sess.get(url, auth=auth) as r:
                    log.info(f"ARI connected (status={r.status})")
        except Exception as e:
            log.error(f"ARI connection failed: {e}")
            return

        await _events_loop(ari)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
    except Exception as e:
        log.exception("Fatal")
        sys.exit(1)

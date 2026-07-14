#!/usr/bin/env python3
"""
ARI-based voicebot handler with between-segment barge-in detection.

Barge-in detection uses SHORT ARI RECORDINGS between TTS segments
(the same approach as the working AGI voicebot.py). After each segment
finishes playing, a brief recording checks if the caller is speaking.
If speech is detected → stop playing remaining segments → process.

Required: pip install aiohttp numpy silero-vad onnxruntime scipy

Asterisk config changes (see ari_setup/):
  http.conf     -> enable HTTP server on port 8088
  ari.conf      -> create 'voicebot' user with read=all write=all
  extensions.conf -> route calls to Stasis(voicebot)
  manager.conf  -> enable AMI on port 5038 (optional, for MixMonitor)
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
import wave as _wave
import numpy as np
import logging

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
    """Return True if speech is detected in the WAV file."""
    _load_silero()

    if _silero_model is False or _silero_model is None:
        return _check_speech_rms(wav_path)

    try:
        import torch
        get_speech_timestamps = _silero_utils

        try:
            import soundfile as sf
            data, rate = sf.read(wav_path, dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
        except Exception:
            with _wave.open(wav_path, "rb") as w:
                nframes = w.getnframes()
                rate = w.getframerate()
                if nframes == 0 or rate == 0:
                    return False
                raw = w.readframes(nframes)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if data is None or len(data) == 0:
            return False

        if rate != 16000:
            from scipy.signal import resample_poly
            g = int(np.gcd(16000, rate))
            data = resample_poly(data, 16000 // g, rate // g).astype(np.float32)

        tensor = torch.from_numpy(data)
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
        try:
            import soundfile as sf
            data, sr = sf.read(wav_path, dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
        except Exception:
            with _wave.open(wav_path, "rb") as w:
                nframes = w.getnframes()
                sr = w.getframerate()
                if nframes == 0 or sr == 0:
                    return False
                raw = w.readframes(nframes)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if data is None or len(data) == 0:
            return False

        rms = float(np.sqrt(np.mean(data ** 2)))
        active = int(np.sum(np.abs(data) > 0.005))
        active_ms = active / sr * 1000
        log.info(f"RMS fallback: rms={rms:.5f} active_ms={active_ms:.0f}")
        return rms > 0.01 and active_ms > 100
    except Exception:
        return False


def _concat_wavs(paths, output_path):
    """Concatenate multiple WAV files into one."""
    frames = []
    rate = 8000
    for p in paths:
        try:
            with _wave.open(p, "rb") as w:
                frames.append(w.readframes(w.getnframes()))
        except Exception:
            pass
    if not frames:
        return
    with _wave.open(output_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(frames))


# ═══════════════════════════════════════════════════════════════════════
#  ARI REST client
# ═══════════════════════════════════════════════════════════════════════

class ARIClient:
    def __init__(self):
        cred = base64.b64encode(f"{ARI_USER}:{ARI_PASS}".encode()).decode()
        self._auth = {"Authorization": f"Basic {cred}"}
        self._session = None

    async def __aenter__(self):
        self._session = await aiohttp.ClientSession().__aenter__()
        return self

    async def __aexit__(self, *args):
        await self._session.__aexit__(*args)

    async def _req(self, method, path, **kw):
        url = f"{ARI_BASE}/ari{path}"
        async with self._session.request(method, url, headers=self._auth,
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

    async def record(self, cid, name, fmt="wav", max_dur=10, max_sil=2,
                     terminateOn="#"):
        params = {
            "name": name, "format": fmt,
            "maxDurationSeconds": max_dur,
            "maxSilenceSeconds": max_sil,
            "ifExists": "overwrite",
        }
        if terminateOn:
            params["terminateOn"] = terminateOn
        return await self.post(f"/channels/{cid}/record", json=params)

    async def stop_live_recording(self, name):
        return await self._req("PUT", f"/recordings/live/{name}/stop")

    async def stop_recording(self, name):
        return await self.delete(f"/recordings/stored/{name}")

    async def get_recording(self, name):
        return await self._req("GET", f"/recordings/stored/{name}/file")


# ═══════════════════════════════════════════════════════════════════════
#  AMI client (optional, for MixMonitor)
# ═══════════════════════════════════════════════════════════════════════

AMI_HOST = "127.0.0.1"
AMI_PORT = 5038
AMI_USER = "voicebot"
AMI_PASS = "voicebot_secret"


class AMIClient:
    def __init__(self):
        self._reader = None
        self._writer = None
        self._connected = False
        self._lock = asyncio.Lock()

    async def connect(self):
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(AMI_HOST, AMI_PORT), timeout=5
            )
            await self._read_until(b"\r\n\r\n")
            await self._send(
                f"Action: Login\r\n"
                f"Username: {AMI_USER}\r\n"
                f"Secret: {AMI_PASS}\r\n"
                f"Events: off\r\n\r\n"
            )
            resp = await self._read_until(b"\r\n\r\n")
            if b"Success" in resp:
                self._connected = True
                log.info("AMI connected")
            else:
                log.warning(f"AMI login failed: {resp[:200]}")
        except Exception as e:
            log.warning(f"AMI connection failed: {e}")

    async def _send(self, data: str):
        if not self._writer:
            return
        self._writer.write(data.encode())
        await self._writer.drain()

    async def _read_until(self, marker: bytes, timeout: float = 3) -> bytes:
        if not self._reader:
            return b""
        data = b""
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                chunk = await asyncio.wait_for(
                    self._reader.read(4096), timeout=timeout
                )
                if not chunk:
                    break
                data += chunk
                if marker in data:
                    break
        except (asyncio.TimeoutError, Exception):
            pass
        return data

    async def close(self):
        try:
            if self._writer:
                self._writer.close()
        except Exception:
            pass
        self._connected = False


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
    """Manages the conversation loop for a single call.

    Barge-in detection uses SHORT ARI RECORDINGS between TTS segments,
    matching the approach used by the working AGI voicebot.py:

    1. Play a TTS segment via ARI (waits for completion)
    2. After segment finishes, do a brief ARI recording (300ms)
    3. Check the recording for speech via Silero VAD
    4. If speech detected → barge-in → record full utterance → process
    5. If no speech → play next segment
    """

    def __init__(self, ari: ARIClient, channel_id: str, args: list,
                 ami: AMIClient):
        self.ari = ari
        self.ami = ami
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

    async def _wait_playback(self, pb_id: str, timeout: float = 30.0):
        """Poll until playback completes or ARI cleans it up."""
        checks = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.1)
            try:
                state = await self.ari.playback_state(pb_id)
            except Exception:
                return
            checks += 1
            if checks <= 3 or checks % 10 == 0:
                log.info(f"Playback {pb_id}: state={state} check={checks}")
            if state in ("done", "failed", None):
                return
        log.warning(f"Playback {pb_id} timed out after {timeout}s")

    # ── Short recording check (between segments) ────────────────

    async def _short_record_check(self, duration: float = 0.4) -> str:
        """Do a brief ARI recording between segments to detect barge-in.

        Uses maxDurationSeconds=1 so the recording completes naturally.
        After completion, polls GET /recordings/live/{name} until the
        recording disappears (stored), then downloads it.

        After the bot finishes playing a segment, any audio on the
        channel must be from the caller (since the bot is silent).
        If we detect speech, the caller is trying to interrupt.

        Returns WAV path if speech detected, None otherwise.
        """
        rec_name = f"{self.call_id}_chk_{int(time.time() * 1000)}"
        result = await self.ari.record(
            self.channel_id, rec_name,
            max_dur=1, max_sil=10,
        )
        if result is None:
            log.warning(f"Short check: record start failed for {rec_name}")
            return None

        # Recording finishes in ~0.1-0.3s (Asterisk "Took too long" kills it early).
        # Short initial wait + fast poll keeps total overhead ~0.3-0.5s.
        await asyncio.sleep(0.3)

        for _ in range(20):  # up to 2.0s total
            r = await self.ari.get(f"/recordings/live/{rec_name}")
            if r is None:
                break
            state = r.get("state", "")
            if state in ("done", "cancelled", "failed"):
                break
            await asyncio.sleep(0.1)

        # Retrieve the stored recording
        raw = await self.ari.get_recording(rec_name)
        if raw is None or len(raw) < 200:
            log.info(f"Short check: {rec_name} empty ({len(raw) if raw else 0} bytes)")
            return None

        out = f"{RECORD_DIR}/{rec_name}.wav"
        with open(out, "wb") as f:
            f.write(raw)

        # Check for speech
        speech = _check_speech_silero(out)
        if speech:
            log.info(f"Short check: SPEECH DETECTED ({len(raw)} bytes)")
            return out
        else:
            log.info(f"Short check: no speech ({len(raw)} bytes)")
            _cleanup(out)
            return None

    # ── Play all segments with barge-in detection ───────────────

    async def play_segments(self, segments: list, check_bargein: bool = True):
        """Play all segments; return (interrupted_text, bargein_recording_path).

        Barge-in detection: after each segment finishes, do a short ARI
        recording to check if the caller is speaking. This matches the
        AGI version's approach (400ms RECORD between segments).

        If barge-in detected → record the caller's full utterance →
        return the merged recording for STT processing.
        """
        interrupted_text = None
        bargein_recording = None

        for i, seg in enumerate(segments):
            seg_path = f"{PLAYBACK_DIR}/{self.call_id}_seg_{i}.wav"
            ok, text, pb_id = await self._play_segment(seg, i)
            interrupted_text = text

            if pb_id is None:
                continue

            # Wait for playback to finish
            await self._wait_playback(pb_id)
            _cleanup(seg_path)

            # Between-segment barge-in check
            if check_bargein and i < len(segments) - 1:
                check_path = await self._short_record_check()
                if check_path is not None:
                    log.info(
                        f"BARGE-IN after seg {i}/{len(segments)-1}: "
                        f"[{text[:60]}]"
                    )
                    # Clean up remaining segment files
                    for j in range(i + 1, len(segments)):
                        _cleanup(f"{PLAYBACK_DIR}/{self.call_id}_seg_{j}.wav")

                    # Record the caller's full utterance
                    full_path = await self.record_caller()
                    if full_path and os.path.exists(full_path):
                        # Merge the check recording + full recording
                        merged = f"{RECORD_DIR}/{self.call_id}_merged.wav"
                        _concat_wavs([check_path, full_path], merged)
                        _cleanup(check_path)
                        _cleanup(full_path)
                        bargein_recording = merged
                        log.info(f"Merged barge-in: {merged}")
                    else:
                        bargein_recording = check_path
                    break

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

            # ── Greeting ────────────────────────────────────────
            data = await self._api()
            if not data:
                await self.ari.hangup(self.channel_id)
                return

            # Play greeting with barge-in detection.
            # Customer can interrupt the greeting just like in AGI mode.
            interrupted_text, bargein_rec = await self.play_segments(
                data.get("segments", []), check_bargein=True
            )

            # ── Conversation loop ───────────────────────────────
            while True:
                # If we have a barge-in recording, use it directly
                if bargein_rec:
                    rec_path = bargein_rec
                    bargein_rec = None
                else:
                    # No barge-in — record caller normally
                    rec_path = await self.record_caller()
                    if rec_path is None:
                        log.info("No recording - ending call")
                        break

                log.info(f"Sending to API call_id={self.call_id}")

                data = await self._api(rec_path, interrupted_text)
                _cleanup(rec_path)

                if not data:
                    log.warning("API returned no data")
                    break

                interrupted_text, bargein_rec = await self.play_segments(
                    data.get("segments", []), check_bargein=True
                )

                if data.get("hangup"):
                    log.info("Hangup received from API")
                    break

                log.info("Turn complete, waiting for next speech")

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

async def _events_loop(ari: ARIClient, ami: AMIClient):
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
                                    h = CallHandler(ari, cid, args, ami)
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
    log.info("Starting voicebot ARI handler (between-segment barge-in)")
    log.info("=" * 50)

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

    # Connect AMI (optional, kept for future use)
    ami = AMIClient()
    await ami.connect()

    async with ARIClient() as ari:
        try:
            async with aiohttp.ClientSession() as sess:
                url = f"{ARI_BASE}/ari/ping"
                cred = base64.b64encode(f"{ARI_USER}:{ARI_PASS}".encode()).decode()
                async with sess.get(url, headers={"Authorization": f"Basic {cred}"}) as r:
                    log.info(f"ARI connected (status={r.status})")
        except Exception as e:
            log.error(f"ARI connection failed: {e}")
            return

        await _events_loop(ari, ami)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
    except Exception as e:
        log.exception("Fatal")
        sys.exit(1)

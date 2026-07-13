#!/usr/bin/env python3
"""
ARI-based voicebot handler with MixMonitor + Silero VAD barge-in.

MixMonitor (via AMI) captures caller-only audio in the background using
Options 'b|r' (b=read-only direction + r=raw format). After each TTS
segment finishes, we read the captured audio and run Silero VAD. If the
caller spoke → stop playback, process their speech.

IMPORTANT: Asterisk MixMonitor options use PIPE separator (|), not comma.
  ast_app_parse_options() splits on '|'.
  b = read audio only (caller → Asterisk)
  r = raw PCM format (slinear 16-bit LE)
  WRONG: 'ri' or 'r,i' → no match → mixed audio (both directions)
  CORRECT: 'b|r' → caller-only raw PCM

Required: pip install aiohttp numpy silero-vad onnxruntime scipy

Asterisk config changes (see ari_setup/):
  http.conf     -> enable HTTP server on port 8088
  ari.conf      -> create 'voicebot' user with read=all write=all
  extensions.conf -> route calls to Stasis(voicebot)
  manager.conf  -> enable AMI on port 5038 for MixMonitor
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


def _read_wav_samples(wav_path: str):
    """Read WAV file using soundfile, returns (samples_float32, rate) or (None, 0)."""
    try:
        import soundfile as sf
        data, rate = sf.read(wav_path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data, rate
    except Exception:
        pass
    # Fallback: try wave module
    try:
        with _wave.open(wav_path, "rb") as w:
            nframes = w.getnframes()
            rate = w.getframerate()
            sw = w.getsampwidth()
            if nframes == 0 or rate == 0:
                return None, 0
            raw = w.readframes(nframes)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, rate
    except Exception:
        pass
    # Last resort: read raw bytes from end of file (skip WAV header)
    try:
        with open(wav_path, "rb") as f:
            data = f.read()
        if len(data) < 48:
            return None, 0
        raw = data[44:]  # skip standard WAV header
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, 8000
    except Exception:
        return None, 0


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

        samples, rate = _read_wav_samples(wav_path)
        if samples is None or len(samples) == 0:
            return False

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
        samples, rate = _read_wav_samples(wav_path)
        if samples is None or len(samples) == 0 or rate == 0:
            return False
        rms = float(np.sqrt(np.mean(samples ** 2)))
        active = int(np.sum(np.abs(samples) > 0.005))
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

    async def stop_live_recording(self, name):
        return await self._req("PUT", f"/recordings/live/{name}/stop")

    async def stop_recording(self, name):
        return await self.delete(f"/recordings/stored/{name}")

    async def get_recording(self, name):
        return await self._req("GET", f"/recordings/stored/{name}/file")


# ═══════════════════════════════════════════════════════════════════════
#  AMI client (for MixMonitor background caller-only audio capture)
# ═══════════════════════════════════════════════════════════════════════

AMI_HOST = "127.0.0.1"
AMI_PORT = 5038
AMI_USER = "voicebot"
AMI_PASS = "voicebot_secret"


class AMIClient:
    """Async AMI client — just enough to start/stop MixMonitor."""

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
            # Read welcome banner
            await self._read_until(b"\r\n\r\n")
            # Login
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

    async def start_mixmonitor(self, channel_id: str, filepath: str):
        """Start AMI MixMonitor capturing caller-only audio (read direction).

        Options use PIPE separator (not comma): ast_app_parse_options splits on '|'.
          b = read audio only (caller → Asterisk direction)
          r = raw PCM format (slinear 16-bit LE)
        WRONG: 'ri' or 'r,i' → parsed as single token → no match → mixed audio.
        CORRECT: 'b|r' → b=read-only, r=raw → caller-only raw PCM.
        """
        if not self._connected:
            return None
        async with self._lock:
            cmd = (
                f"Action: MixMonitor\r\n"
                f"Channel: {channel_id}\r\n"
                f"File: {filepath}\r\n"
                f"Options: b|r\r\n"
                f"Replace: yes\r\n\r\n"
            )
            await self._send(cmd)
            resp = await self._read_until(b"\r\n\r\n")
            log.info(f"MixMonitor started: {filepath} resp={resp[:200]}")
            return resp

    async def stop_mixmonitor(self, channel_id: str):
        """Stop MixMonitor on the channel."""
        if not self._connected:
            return
        async with self._lock:
            cmd = (
                f"Action: StopMixMonitor\r\n"
                f"Channel: {channel_id}\r\n\r\n"
            )
            await self._send(cmd)
            await self._read_until(b"\r\n\r\n", timeout=1)

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
    """Manages the conversation loop for a single call."""

    def __init__(self, ari: ARIClient, channel_id: str, args: list,
                 ami: AMIClient):
        self.ari = ari
        self.ami = ami
        self.channel_id = channel_id
        self.args = args
        self.call_id = str(uuid.uuid4())
        self._http: aiohttp.ClientSession = None
        # MixMonitor path set by dialplan using UNIQUEID (args[0])
        mix_id = args[0] if args else self.call_id
        self._mix_path = f"/tmp/{mix_id}_caller"
        self._mix_offset = 0
        self._mix_rate = 8000
        self._mix_active = False

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

    # ── Monitor background audio capture ─────────────────────────

    async def _start_mixmonitor(self):
        """Start AMI MixMonitor to capture caller audio in the background."""
        resp = await self.ami.start_mixmonitor(
            self.channel_id, self._mix_path
        )
        self._mix_active = True
        # Give MixMonitor a moment to start writing
        await asyncio.sleep(0.3)
        raw_path = self._mix_path + ".raw"
        if os.path.exists(raw_path):
            log.info(f"MixMonitor file exists: {raw_path} "
                     f"({os.path.getsize(raw_path)} bytes)")
        else:
            log.warning(f"MixMonitor file NOT found: {raw_path}")

    async def _stop_mixmonitor(self):
        """Stop MixMonitor."""
        self._mix_active = False
        await self.ami.stop_mixmonitor(self.channel_id)

    def _read_mixmonitor_chunk(self):
        """Read new audio samples from the MixMonitor raw file since last read.

        MixMonitor with 'r' option writes raw slinear PCM (16-bit LE, mono).
        Returns float32 samples at native rate or None if no new data.
        """
        try:
            raw_path = self._mix_path + ".raw"
            if not os.path.exists(raw_path):
                return None
            size = os.path.getsize(raw_path)
            if size <= self._mix_offset:
                return None
            with open(raw_path, "rb") as f:
                f.seek(self._mix_offset)
                data = f.read()
            self._mix_offset = size
            if len(data) < 160:
                return None
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            return samples
        except Exception as e:
            log.warning(f"MixMonitor read error: {e}")
            return None

    def _check_bargein_mixmonitor(self, min_speech_ms: int = 100) -> bool:
        """Check the MixMonitor file for caller speech (non-blocking).

        Called RIGHT AFTER a playback segment finishes, so the bot is
        silent — any audio in the file must be from the caller.
        MixMonitor Options 'b|r' captures only the read (caller) direction.
        """
        samples = self._read_mixmonitor_chunk()
        if samples is None or len(samples) == 0:
            return False

        rate = self._mix_rate or 8000
        rms = float(np.sqrt(np.mean(samples ** 2)))
        duration_ms = len(samples) / rate * 1000
        log.info(
            f"BARGEIN-CHK: {len(samples)} samples ({duration_ms:.0f}ms @{rate}Hz) rms={rms:.5f}"
        )
        # Quick energy gate: skip if too quiet
        if rms < 0.01:
            log.info(f"BARGEIN-CHK: skipped (rms={rms:.5f} < 0.01)")
            return False

        # Cap the chunk to last 2 seconds only (avoid accumulated bot TTS)
        max_samples = rate * 2
        if len(samples) > max_samples:
            samples = samples[-max_samples:]
            log.info(f"BARGEIN-CHK: capped to last {max_samples} samples ({2000}ms)")

        # Save the chunk for STT if barge-in confirmed
        self._last_bargein_samples = samples
        self._last_bargein_rate = rate

        # Run Silero VAD
        _load_silero()
        if _silero_model is None or _silero_model is False:
            active = int(np.sum(np.abs(samples) > 0.005))
            active_ms = active / rate * 1000
            return active_ms >= min_speech_ms

        try:
            import torch
            min_samples = int(rate * min_speech_ms / 1000)
            if len(samples) < min_samples:
                log.info(f"BARGEIN-CHK: skipped (chunk too short: {len(samples)} < {min_samples})")
                return False
            # Resample to 16kHz for Silero
            from scipy.signal import resample_poly
            g = int(np.gcd(16000, rate))
            samples_16k = resample_poly(samples, 16000 // g, rate // g).astype(np.float32)
            tensor = torch.from_numpy(samples_16k)
            speech_ts = _silero_utils(
                tensor, _silero_model,
                sampling_rate=16000,
                threshold=0.5,
                min_speech_duration_ms=200,
                min_silence_duration_ms=100,
            )
            total_ms = sum(s["end"] - s["start"] for s in speech_ts) * 1000 // 16000
            if total_ms >= min_speech_ms:
                log.info(f"BARGE-IN VAD: speech_ms={total_ms} rms={rms:.4f}")
                return True
            else:
                log.info(f"BARGEIN-CHK: VAD no speech (ms={total_ms} < {min_speech_ms}) rms={rms:.5f}")
        except Exception as e:
            log.warning(f"Silero error in barge-in check: {e}")
            # RMS fallback
            active = int(np.sum(np.abs(samples) > 0.005))
            active_ms = active / rate * 1000
            return active_ms >= min_speech_ms

        return False

    def _save_bargein_audio(self):
        """Save the barge-in audio chunk as a WAV file for STT."""
        samples = getattr(self, "_last_bargein_samples", None)
        rate = getattr(self, "_last_bargein_rate", 8000)
        if samples is None or len(samples) == 0:
            return None
        out = f"{RECORD_DIR}/{self.call_id}_bargein.wav"
        pcm = (samples * 32768).astype(np.int16).tobytes()
        with _wave.open(out, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm)
        log.info(f"Bargein audio saved: {out} ({os.path.getsize(out)} bytes)")
        return out

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
        """Poll until playback completes or fails."""
        checks = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.1)
            state = await self.ari.playback_state(pb_id)
            checks += 1
            if checks <= 3 or checks % 10 == 0:
                log.info(f"Playback {pb_id}: state={state} check={checks}")
            if state in ("done", "failed", None):
                return
        log.warning(f"Playback {pb_id} timed out after {timeout}s (state={state})")

    # ── Short recording check (between segments) ────────────────

    async def _record_check(self, max_dur: float = 2.0,
                            max_sil: float = 0.8) -> str:
        """Do a short ARI recording and return the WAV path (or None).

        Used between segments to detect customer speech (barge-in).
        Starts a recording then stops it early after `max_dur` seconds,
        so we don't block the channel for the full ARI int minimum (1s).
        ALWAYS stops the live recording to free the channel.
        """
        rec_name = f"{self.call_id}_chk_{int(time.time() * 1000)}"
        # ARI maxDurationSeconds is int — use 10 as safety net, we stop early
        result = await self.ari.record(
            self.channel_id, rec_name,
            max_dur=10, max_sil=2,
        )
        if result is None:
            log.warning(f"Record check: record start failed for {rec_name}")
            return None

        # Wait just long enough to capture any immediate caller speech
        await asyncio.sleep(max_dur)

        # Stop the recording immediately to free the channel
        try:
            await self.ari.stop_live_recording(rec_name)
        except Exception:
            pass

        # Brief pause to ensure file is flushed to disk
        await asyncio.sleep(0.2)

        raw = await self.ari.get_recording(rec_name)
        if raw is None or len(raw) < 100:
            log.info(f"Record check: {rec_name} empty/missing ({len(raw) if raw else 0} bytes)")
            return None

        out = f"{RECORD_DIR}/{rec_name}.wav"
        with open(out, "wb") as f:
            f.write(raw)
        log.info(f"Record check: {out} ({len(raw)} bytes)")
        return out

    # ── Play all segments with real-time barge-in detection ──────

    async def play_segments(self, segments: list, check_bargein: bool = True):
        """Play all segments; return (interrupted_text, recording_path).

        When check_bargein is True, after each segment finishes (bot is
        silent), we read the MixMonitor file and run Silero VAD. If the
        caller spoke → barge-in detected, stop playing remaining segments.

        MixMonitor runs from the dialplan (before Stasis), capturing
        caller-only audio ('b' option) with zero gaps between segments.
        """
        interrupted_text = None
        bargein_recording = None

        # Reset offset so we only read audio captured from NOW forward.
        # Discards stale audio from greeting / previous segments.
        if check_bargein and self._mix_active:
            try:
                raw_path = self._mix_path + ".raw"
                if os.path.exists(raw_path):
                    self._mix_offset = os.path.getsize(raw_path)
                    log.info(f"BARGEIN: offset reset to {self._mix_offset}")
            except Exception:
                pass

        for i, seg in enumerate(segments):
            ok, text, pb_id = await self._play_segment(seg, i)

            if pb_id is not None:
                await self._wait_playback(pb_id)
            _cleanup(f"{PLAYBACK_DIR}/{self.call_id}_seg_{i}.wav")

            interrupted_text = text

            # Barge-in check: read MixMonitor file (bot just went silent)
            if check_bargein and self._mix_active:
                # Small delay for audio to flush to disk
                await asyncio.sleep(0.1)
                if self._check_bargein_mixmonitor(min_speech_ms=100):
                    log.info(
                        f"BARGE-IN after seg {i}/{len(segments)-1}: "
                        f"[{text[:60]}]"
                    )
                    # Cancel remaining segments
                    if pb_id is not None:
                        await self.ari.stop_playback(pb_id)
                    bargein_recording = self._save_bargein_audio()
                    # Clean up remaining segment files
                    for j in range(i + 1, len(segments)):
                        _cleanup(f"{PLAYBACK_DIR}/{self.call_id}_seg_{j}.wav")
                    break

        # If no barge-in and MixMonitor available, check after last segment
        # (caller may speak during/after the very last segment)
        if (check_bargein and bargein_recording is None
                and self._mix_active):
            await asyncio.sleep(0.1)
            if self._check_bargein_mixmonitor(min_speech_ms=150):
                log.info(
                    f"BARGE-IN after last seg: [{interrupted_text[:60]}]"
                )
                bargein_recording = self._save_bargein_audio()

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
        log.info(f"CALL={self.call_id} CHAN={self.channel_id} START mix={self._mix_path}")
        self._http = aiohttp.ClientSession()
        try:
            await self.ari.answer(self.channel_id)

            # Dialplan started MixMonitor before Stasis — verify file exists
            await asyncio.sleep(0.3)
            raw_path = self._mix_path + ".raw"
            if os.path.exists(raw_path):
                self._mix_active = True
                log.info(f"MixMonitor file found: {raw_path} "
                         f"({os.path.getsize(raw_path)} bytes)")
            else:
                log.warning(f"MixMonitor file NOT found: {raw_path}")

            # Greeting — play all segments back-to-back, no barge-in check
            data = await self._api()
            if not data:
                await self.ari.hangup(self.channel_id)
                return
            interrupted_text, bargein_rec = await self.play_segments(
                data.get("segments", []), check_bargein=False
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
                    data.get("segments", []), check_bargein=True
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
    log.info("Starting voicebot ARI+AMI handler (record-check barge-in)")
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

    # Connect AMI (for MixMonitor)
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

#!/usr/bin/env python3
"""
ARI-based voicebot handler with real-time voice barge-in.

Stops the bot mid-word when the customer speaks, using MixMonitor (via AMI)
for audio monitoring + RMS energy detection during playback.

Required: pip install aiohttp numpy

Asterisk config changes (see ari_setup/):
  http.conf     → enable HTTP server on port 8088
  ari.conf      → create 'voicebot' user with read=all write=all
  manager.conf  → create 'voicebot' AMI user with read=all write=all
  extensions.conf → route calls to Stasis(voicebot)
"""

import asyncio
import aiohttp
import json
import base64
import os
import sys
import time
import uuid
import struct
import numpy as np
import logging
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
API_BASE = "http://127.0.0.1:8000"
ARI_BASE = "http://127.0.0.1:8088"
ARI_USER = "voicebot"
ARI_PASS = "voicebot_secret"
ARI_APP = "voicebot"
AMI_HOST = "127.0.0.1"
AMI_PORT = 5038
AMI_USER = "voicebot"
AMI_SECRET = "voicebot_secret"

RECORD_DIR = "/var/spool/asterisk/recording"
PLAYBACK_DIR = "/var/lib/asterisk/sounds/voicebot"
LOG_FILE = "/var/log/voicebot_ari.log"

os.makedirs(RECORD_DIR, exist_ok=True)
os.makedirs(PLAYBACK_DIR, exist_ok=True)

VAD_RMS_THRESHOLD = 0.008    # minimum RMS (0-1) to trigger barge-in
VAD_POLL_MS = 50             # how often to check MixMonitor file
VAD_WINDOW_MS = 150          # audio window for each RMS check
VAD_MIN_SPEECH_MS = 200      # minimum sustained speech before barge-in fires

logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filemode="a",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  Audio helpers
# ═══════════════════════════════════════════════════════════════════════

def _pcm_rms(raw: bytes, sample_width: int = 2) -> float:
    """RMS (0-1) of raw PCM samples."""
    if not raw or len(raw) < sample_width:
        return 0.0
    n = len(raw) // sample_width
    fmt = "<" + "h" * n if sample_width == 2 else "<" + "b" * n
    samples = struct.unpack(fmt, raw[: n * sample_width])
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
    return rms / 32768.0


def _tail_rms(wav_path: str, window_ms: int = 150) -> float:
    """Read raw PCM from the trailing tail of a WAV file (safe for
    in-progress recordings since it bypasses the header)."""
    try:
        import wave
        with wave.open(wav_path, "rb") as w:
            rate = w.getframerate()
            sw = w.getsampwidth()
            nframes = w.getnframes()
            if rate == 0 or sw == 0 or nframes == 0:
                return 0.0
            n = int(rate * window_ms / 1000)
            n = min(n, nframes)
            if n < 1:
                return 0.0
            w.setpos(max(0, nframes - n))
            raw = w.readframes(n)
            return _pcm_rms(raw, sw)
    except Exception:
        return 0.0


def _check_early_speech(wav_path: str, window_ms: int = 200,
                        threshold: float = 0.005) -> bool:
    """True if the first `window_ms` of a completed WAV contains speech."""
    try:
        import wave
        with wave.open(wav_path, "rb") as w:
            if w.getframerate() == 0 or w.getnframes() == 0:
                return False
            n = int(w.getframerate() * window_ms / 1000)
            n = min(n, w.getnframes())
            frames = w.readframes(n)
            if not frames or len(frames) < 4:
                return False
            return _pcm_rms(frames) > threshold
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
#  AMI client (for MixMonitor)
# ═══════════════════════════════════════════════════════════════════════

class AMIClient:
    """Minimal async AMI client over plain TCP."""

    def __init__(self):
        self._reader = None
        self._writer = None
        self._lock = asyncio.Lock()

    async def connect(self):
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(AMI_HOST, AMI_PORT), timeout=5
        )
        # Read banner
        banner = await asyncio.wait_for(self._reader.readuntil(b"\r\n\r\n"),
                                        timeout=5)
        # Login
        await self._send(f"Action: Login\r\n"
                         f"Username: {AMI_USER}\r\n"
                         f"Secret: {AMI_SECRET}\r\n\r\n")
        resp = await self._read_response()
        if "Message: Authentication accepted" not in resp:
            raise RuntimeError(f"AMI login failed: {resp[:200]}")

    async def _send(self, cmd: str):
        self._writer.write(cmd.encode())
        await self._writer.drain()

    async def _read_response(self) -> str:
        data = b""
        while True:
            chunk = await asyncio.wait_for(self._reader.read(4096),
                                           timeout=10)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                break
        return data.decode(errors="replace")

    async def action(self, action: str, **params) -> str:
        async with self._lock:
            lines = [f"Action: {action}"]
            for k, v in params.items():
                k2 = k.replace("_", " ")
                lines.append(f"{k2}: {v}")
            lines.append("")
            await self._send("\r\n".join(lines) + "\r\n")
            resp = await self._read_response()
            return resp

    async def mixmonitor_start(self, channel: str, file_path: str):
        return await self.action("MixMonitor", Channel=channel,
                                 File=file_path, Options="b")

    async def mixmonitor_stop(self, channel: str):
        return await self.action("StopMixMonitor", Channel=channel)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()


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
        return await self.post(f"/channels/{cid}/record", params=params)

    async def stop_recording(self, name):
        return await self.delete(f"/recordings/stored/{name}")

    async def get_recording(self, name):
        return await self._req("GET", f"/recordings/stored/{name}/file")


# ═══════════════════════════════════════════════════════════════════════
#  Call handler
# ═══════════════════════════════════════════════════════════════════════

class CallHandler:
    """Manages the conversation loop for a single call."""

    def __init__(self, ari: ARIClient, ami: AMIClient,
                 channel_id: str, args: list):
        self.ari = ari
        self.ami = ami
        self.channel_id = channel_id
        self.args = args
        self.call_id = str(uuid.uuid4())
        self._bargein = asyncio.Event()
        self._cancel_vad = False
        self._mixmon_path = ""
        self._http: aiohttp.ClientSession = None

    # ── Backend API ─────────────────────────────────────────────

    async def _api(self, audio_path=None, interrupted_text=None):
        url = f"{API_BASE}/voice-audio-segmented"
        data = {"call_id": self.call_id}
        if interrupted_text:
            data["interrupted_text"] = interrupted_text
        if audio_path:
            with open(audio_path, "rb") as f:
                async with self._http.post(url, data=data,
                                     files={"audio": f}) as r:
                    if r.status != 200:
                        return None
                    return await r.json()
        else:
            data["outbound"] = "true"
            async with self._http.post(url, data=data) as r:
                if r.status != 200:
                    return None
                return await r.json()

    # ── VAD background task ─────────────────────────────────────

    async def _poll_vad(self):
        """Poll MixMonitor file for customer speech with adaptive baseline."""
        path = self._mixmon_path
        speech_ms = 0
        baseline_rms = 0.0
        baseline_samples = 0
        triggered = False

        while not self._cancel_vad:
            await asyncio.sleep(VAD_POLL_MS / 1000)
            if not os.path.exists(path):
                continue
            try:
                rms = _tail_rms(path, VAD_WINDOW_MS)
            except Exception:
                continue

            # First 5 samples: establish baseline (30 clients != empty)
            if baseline_samples < 5:
                if rms > 0:
                    baseline_rms = (baseline_rms * baseline_samples + rms) / (baseline_samples + 1)
                    baseline_samples += 1
                continue

            # Adaptive threshold: baseline + offset, min VAD_RMS_THRESHOLD
            adaptive_threshold = max(baseline_rms + 0.005, VAD_RMS_THRESHOLD)
            if rms > adaptive_threshold:
                speech_ms += VAD_POLL_MS
                if speech_ms >= VAD_MIN_SPEECH_MS:
                    log.info(f"BARGE-IN trigger: rms={rms:.5f} "
                             f"baseline={baseline_rms:.5f} "
                             f"threshold={adaptive_threshold:.5f} "
                             f"speech_ms={speech_ms}")
                    self._bargein.set()
                    triggered = True
                    break
            else:
                speech_ms = max(0, speech_ms - VAD_POLL_MS)

        if not triggered:
            log.debug(f"VAD ended: baseline={baseline_rms:.5f} "
                      f"speech_ms={speech_ms}")

    # ── Play one segment with VAD ───────────────────────────────

    async def _play_segment(self, seg_data: dict, idx: int):
        """Play one segment with MixMonitor+VAD monitoring."""
        text = seg_data["text"]
        audio_b64 = seg_data["audio"]

        seg_name = f"{self.call_id}_seg_{idx}"
        seg_path = f"{PLAYBACK_DIR}/{seg_name}.ulaw"
        with open(seg_path, "wb") as f:
            f.write(base64.b64decode(audio_b64))

        # Start MixMonitor (try with b option for caller-only audio)
        self._mixmon_path = f"{RECORD_DIR}/{seg_name}_mix.wav"
        mix_ok = False
        try:
            resp = await self.ami.mixmonitor_start(
                self.channel_id, self._mixmon_path
            )
            mix_ok = "Success" in resp
        except Exception as e:
            log.warning(f"MixMonitor start failed: {e}")

        # Start VAD polling if MixMonitor is active
        self._bargein.clear()
        self._cancel_vad = False
        vad_task = None
        if mix_ok:
            # Wait a brief moment for file creation
            await asyncio.sleep(0.1)
            vad_task = asyncio.create_task(self._poll_vad())

        # Start ARI playback
        pb_id = f"pb_{seg_name}"
        pb = await self.ari.play(
            self.channel_id, f"sound:voicebot/{seg_name}", pb_id
        )
        if pb is None:
            self._cancel_vad = True
            if mix_ok:
                await self.ami.mixmonitor_stop(self.channel_id)
            _cleanup(seg_path)
            _cleanup(self._mixmon_path)
            return True, None

        # If MixMonitor failed, fall back: wait for playback without VAD
        if not mix_ok:
            log.info(f"No MixMonitor VAD for seg {idx}, playing without VAD")
            await self._wait_playback(pb_id)
            _cleanup(seg_path)
            return True, text

        # Wait for either playback done or barge-in
        pb_task = asyncio.create_task(self._wait_playback(pb_id))
        barge_task = asyncio.create_task(self._bargein.wait())
        done, pending = await asyncio.wait(
            {pb_task, barge_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()

        # Stop VAD + MixMonitor
        self._cancel_vad = True
        if vad_task and not vad_task.done():
            vad_task.cancel()
        try:
            await self.ami.mixmonitor_stop(self.channel_id)
        except Exception:
            pass

        if self._bargein.is_set():
            log.info(f"BARGE-IN on seg {idx}: [{text}]")
            await self.ari.stop_playback(pb_id)
            await asyncio.sleep(0.05)
            _cleanup(seg_path)
            _cleanup(self._mixmon_path)
            return False, text  # interrupted

        _cleanup(seg_path)
        _cleanup(self._mixmon_path)
        return True, text

    async def _wait_playback(self, pb_id: str):
        """Poll until playback completes or fails."""
        while True:
            await asyncio.sleep(0.1)
            state = await self.ari.playback_state(pb_id)
            if state in ("done", "failed"):
                return
            # state is None (transient ARI error) or "playing" — keep waiting

    # ── Play all segments ───────────────────────────────────────

    async def play_segments(self, segments: list) -> str:
        """Play all segments; return interrupted segment text or None."""
        interrupted_text = None
        for i, seg in enumerate(segments):
            ok, text = await self._play_segment(seg, i)
            if ok is False:  # interrupted
                return text
            if i == len(segments) - 1:
                interrupted_text = text
        return interrupted_text

    # ── Record caller ───────────────────────────────────────────

    async def record_caller(self) -> str:
        """Record the caller and return the WAV path (or None)."""
        rec_name = f"{self.call_id}_caller"
        result = await self.ari.record(
            self.channel_id, rec_name, max_dur=10, max_sil=2
        )
        if result is None:
            log.warning("record start failed")
            return None

        # Wait for recording to complete (live -> stored transition)
        for _ in range(200):
            await asyncio.sleep(0.1)
            r = await self.ari.get(
                f"/recordings/live/{rec_name}"
            )
            if r is None:
                # Recording moved to stored (or never existed) — done
                break
            state = r.get("state", "")
            if state in ("done", "cancelled", "failed"):
                break

        # Download from stored namespace
        raw = await self.ari.get_recording(rec_name)
        if raw is None or len(raw) < 100:
            log.warning("recording empty or download failed")
            return None

        out = f"{RECORD_DIR}/{rec_name}_dl.wav"
        with open(out, "wb") as f:
            f.write(raw)
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
            interrupted_text = await self.play_segments(
                data.get("segments", [])
            )

            # Conversation loop
            while True:
                rec_path = await self.record_caller()
                if rec_path is None:
                    break

                # Post-hoc interruption check
                if interrupted_text and not self._bargein.is_set():
                    if _check_early_speech(rec_path):
                        log.info("POST-HOC barge-in (early speech)")

                data = await self._api(rec_path, interrupted_text)
                _cleanup(rec_path)

                if not data:
                    break

                interrupted_text = await self.play_segments(
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

def _cleanup(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


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
                                    h = CallHandler(ari, ami, cid, args)
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
    log.info("═" * 50)
    log.info("Starting voicebot ARI handler")
    log.info("═" * 50)

    async with ARIClient() as ari, AMIClient() as ami:
        # Verify connectivity
        try:
            ping = await ari.get("/ping")
            if ping is None:
                log.error("ARI unreachable — check http.conf / ari.conf")
                return
            log.info(f"ARI connected: {ping}")
        except Exception as e:
            log.error(f"ARI connection failed: {e}")
            return

        try:
            await ami.connect()
            log.info("AMI connected")
        except Exception as e:
            log.error(f"AMI connection failed: {e}")
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

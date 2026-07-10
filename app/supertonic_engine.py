import os
import sys
import re
import io
import soundfile as sf
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SUPERTONIC_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..", "..", "supertonic")
)

sys.path.insert(0, os.path.join(SUPERTONIC_DIR, "py"))

from helper import (
    AVAILABLE_LANGS,
    load_text_to_speech,
    load_voice_style
)

_tts = None
_style = None


def _warmup():
    global _tts, _style
    try:
        _tts("warmup", "en", _style, total_step=6, speed=1.1)
    except Exception:
        pass


def get_tts():
    global _tts
    global _style

    if _tts is None:

        print("Loading Supertonic...")

        _tts = load_text_to_speech(
            os.path.join(SUPERTONIC_DIR, "assets", "onnx"),
            True
        )

        _style = load_voice_style([
            os.path.join(
                SUPERTONIC_DIR,
                "assets",
                "voice_styles",
                "M4.json"
            )
        ])

        print("Supertonic Ready")
        print("Warming up TTS...")
        _warmup()
        print("TTS ready")

    return _tts, _style


def split_into_segments(text, max_words=50):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    segments = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        words = s.split()
        if len(words) <= max_words:
            segments.append(s)
        else:
            for i in range(0, len(words), max_words):
                segments.append(' '.join(words[i:i + max_words]))
    if not segments:
        segments = [text]
    result = []
    buf = []
    buf_words = 0
    for seg in segments:
        n = len(seg.split())
        if buf_words + n > max_words:
            result.append(' '.join(buf))
            buf = [seg]
            buf_words = n
        else:
            buf.append(seg)
            buf_words += n
    if buf:
        result.append(' '.join(buf))
    return result if result else [text]


def speak_segments(text, lang="en", prefix=""):
    segments = split_into_segments(text)
    paths = []
    for i, seg in enumerate(segments):
        outpath = f"audio/{prefix}_seg_{i}.wav"
        speak(seg, outpath, lang)
        paths.append((seg, outpath))
    return paths


def _normalize_for_tts(text):
    text = re.sub(r'\bRs\b', 'Rupees', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<=\d),(?=\d)', '', text)
    text = text.replace('%', ' percent ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def speak(text, output_file, lang="en"):

    if not text or not text.strip():
        raise Exception(
            "Empty text received for TTS"
        )

    if lang not in AVAILABLE_LANGS:
        lang = "en"

    text = _normalize_for_tts(text)

    tts, style = get_tts()

    wav, duration = tts(
        text,
        lang,
        style,
        total_step=5,
        speed=1.1
    )

    if wav is None:
        raise Exception(
            f"TTS generation failed. Text={text}"
        )
    

    sf.write(
        output_file,
        wav[0],
        tts.sample_rate
    )

    return output_file
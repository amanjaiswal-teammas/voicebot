import os
import sys
import re
import io
import soundfile as sf
import numpy as np

from app.number_to_words import int_to_words_en, int_to_words_hi

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
                "F1.json"
            )
        ])

        print("Supertonic Ready")
        print("Warming up TTS...")
        _warmup()
        print("TTS ready")

    return _tts, _style


def split_into_segments(text, max_words=12):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text]
    result = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= max_words:
            result.append(sentence)
        else:
            for i in range(0, len(words), max_words):
                result.append(' '.join(words[i:i + max_words]))
    return result


def speak_segments(text, lang="en", prefix=""):
    segments = split_into_segments(text)
    paths = []
    for i, seg in enumerate(segments):
        outpath = f"audio/{prefix}_seg_{i}.wav"
        speak(seg, outpath, lang)
        paths.append((seg, outpath))
    return paths


def _has_devanagari(text):
    return bool(re.search(r'[\u0900-\u097F]', text))


def _convert_number(m, lang):
    raw = m.group(0)
    try:
        n = int(raw)
    except ValueError:
        return raw
    if lang == "hi":
        return int_to_words_hi(n)
    return int_to_words_en(n)


def _normalize_for_tts(text, lang="en"):
    # Replace "Rs" with spoken word, keep the number attached for conversion
    text = re.sub(r'\bRs\b', 'Rupees', text, flags=re.IGNORECASE)

    # Strip commas from numbers: 1,599 -> 1599
    text = re.sub(r'(?<=\d),(?=\d)', '', text)

    # Percent sign
    text = text.replace('%', ' percent ')

    # Convert digit sequences (amounts, prices, standalone numbers) to words
    text = re.sub(r'\b\d+\b', lambda m: _convert_number(m, lang), text)

    text = re.sub(r'\s+', ' ', text).strip()
    return text


def speak(text, output_file, lang="en"):

    if not text or not text.strip():
        raise Exception(
            "Empty text received for TTS"
        )

    if lang == "hi" and not _has_devanagari(text):
        lang = "en"

    if lang not in AVAILABLE_LANGS:
        lang = "en"

    text = _normalize_for_tts(text, lang)

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
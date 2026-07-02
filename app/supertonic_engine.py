import os
import sys
import soundfile as sf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SUPERTONIC_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..", "..", "supertonic")
)

sys.path.insert(0, os.path.join(SUPERTONIC_DIR, "py"))

from helper import (
    load_text_to_speech,
    load_voice_style
)

_tts = None
_style = None


def get_tts():
    global _tts
    global _style

    if _tts is None:

        print("Loading Supertonic...")

        _tts = load_text_to_speech(
            os.path.join(SUPERTONIC_DIR, "assets", "onnx"),
            False
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

    return _tts, _style


import re


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

    text = _normalize_for_tts(text)

    tts, style = get_tts()

    wav, duration = tts(
        text,
        lang,
        style,
        total_step=6,
        speed=1.0
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
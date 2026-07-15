from faster_whisper import WhisperModel

model = WhisperModel(
    "medium",
    device="cuda",
    compute_type="int8_float16"
)

HINDI_PROMPT = "नमस्ते, हाँ, नहीं, ठीक है, बताइए, समझ गई, क्या आप ऑर्डर लेना चाहेंगे"


def transcribe(audio_file, language_hint=None):
    print("STT FILE:", audio_file)

    initial_prompt = None
    whisper_lang = None

    if language_hint == "hi":
        whisper_lang = "hi"
        initial_prompt = HINDI_PROMPT
    elif language_hint == "en":
        whisper_lang = "en"

    segments, info = model.transcribe(
        audio_file,
        language=whisper_lang,
        initial_prompt=initial_prompt,
        beam_size=1,
        best_of=1,
        vad_filter=True
    )

    text = " ".join(
        segment.text
        for segment in segments
    )

    print("WHISPER DETECTED:", info.language)
    print("HINT USED:", language_hint)
    print("TRANSCRIPT:", text)

    return {
        "text": text.strip(),
        "language": info.language
    }

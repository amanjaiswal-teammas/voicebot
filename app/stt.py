from faster_whisper import WhisperModel

model = WhisperModel(
    "large-v3",
    device="cuda",
    compute_type="int8_float16"
)

def transcribe(audio_file):
    print("STT FILE:", audio_file)

    segments, info = model.transcribe(
        audio_file,
        language=None,
        beam_size=5,
        vad_filter=True
    )

    text = " ".join(
        segment.text
        for segment in segments
    )

    print("LANGUAGE:", info.language)
    print("TRANSCRIPT:", text)

    return {
        "text": text.strip(),
        "language": info.language
    }

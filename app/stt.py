from faster_whisper import WhisperModel

model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

def transcribe(audio_file):
    print("STT FILE:", audio_file)

    segments, info = model.transcribe(
        audio_file,
        language="en",
        beam_size=5,
        vad_filter=True
    )

    text = " ".join(
        segment.text
        for segment in segments
    )

    print("LANGUAGE:", info.language)
    print("TRANSCRIPT:", text)

    return text.strip()
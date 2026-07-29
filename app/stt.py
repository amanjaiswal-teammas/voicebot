import os
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, sosfilt, butter
from faster_whisper import WhisperModel

model = WhisperModel(
    "large-v3-turbo",
    device="cuda",
    compute_type="float16"
)

HINDI_PROMPT = "नमस्ते, हाँ, नहीं, ठीक है, बताइए, समझ गई, क्या आप ऑर्डर लेना चाहेंगे"

def _preprocess_audio(audio_file: str) -> str:
    data, sr = sf.read(audio_file)

    if len(data.shape) > 1:
        data = data.mean(axis=1)

    if len(data) == 0:
        return audio_file

    # Remove DC offset
    data = data - np.mean(data)

    # High-pass filter to remove telephone line hum / rumble (80 Hz cutoff)
    sos = butter(4, 80 / (sr / 2), btype="high", output="sos")
    data = sosfilt(sos, data).astype(np.float32)

    # Normalize volume to a target RMS level
    target_rms = 0.18
    rms = np.sqrt(np.mean(data ** 2))
    if rms > 1e-6:
        gain = min(target_rms / rms, 4.0)
        data = data * gain

    # Soft-clip to prevent artifacts from extreme gain
    data = np.clip(data, -0.99, 0.99)

    # Upsample to 16000 Hz (Whisper's native sample rate) if needed
    if sr != 16000:
        up = 16000
        down = sr
        g = int(np.gcd(up, down))
        data = resample_poly(data, up // g, down // g).astype(np.float32)
        sr = 16000

    pp_path = audio_file.replace(".wav", "_pp.wav")
    sf.write(pp_path, data, sr)
    return pp_path


def transcribe(audio_file, language_hint=None):
    print("STT FILE:", audio_file)

    pp_file = _preprocess_audio(audio_file)
    use_file = pp_file if os.path.exists(pp_file) else audio_file

    initial_prompt = None
    whisper_lang = None

    if language_hint == "hi":
        whisper_lang = "hi"
        initial_prompt = HINDI_PROMPT
    elif language_hint == "en":
        whisper_lang = "en"

    segments, info = model.transcribe(
        use_file,
        language=whisper_lang,
        initial_prompt=initial_prompt,
        beam_size=5,
        best_of=3,
        vad_filter=False,
        vad_parameters=dict(
            threshold=0.6,
            min_speech_duration_ms=150,
            min_silence_duration_ms=100,
        ),
    )

    text = " ".join(
        segment.text
        for segment in segments
    )

    if pp_file != audio_file and os.path.exists(pp_file):
        os.remove(pp_file)

    print("WHISPER DETECTED:", info.language)
    print("HINT USED:", language_hint)
    print("TRANSCRIPT:", text)

    return {
        "text": text.strip(),
        "language": info.language
    }

import subprocess

SUPERTONIC_DIR = r"C:\Users\User\supertonic\py"

SUPERTONIC_PYTHON = (
    r"C:\Users\User\supertonic\py\.venv\Scripts\python.exe"
)

VOICE_MAP = {
    "F1": "../assets/voice_styles/F1.json",
    "F2": "../assets/voice_styles/F2.json",
    "M1": "../assets/voice_styles/M1.json",
    "M2": "../assets/voice_styles/M2.json",
}


def generate_tts(text, voice="F1", lang="en"):
    cmd = [
        SUPERTONIC_PYTHON,
        "example_onnx.py",
        "--n-test",
        "1",
        "--lang",
        lang,
        "--voice-style",
        VOICE_MAP[voice],
        "--text",
        text,
    ]

    subprocess.run(
        cmd,
        cwd=SUPERTONIC_DIR,
        check=True,
    )
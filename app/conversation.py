from .stt import transcribe
from .llm import ask_llm
from .supertonic_engine import speak
from .memory import (
    get_history,
    add_message
)
import time


def process_call(
    call_id,
    audio_file
):

    print("STEP 1: STT")

    print("INPUT AUDIO:", audio_file)

    start_total = time.time()

    stt_start = time.time()

    stt_result = transcribe(audio_file)

    caller_text = stt_result["text"]

    lang = stt_result["language"]

    print("WHISPER LANG:", lang)

    print(
        "STT:",
        int((time.time() - stt_start) * 1000),
        "ms"
    )

    
    if not caller_text.strip():

        output = f"audio/{call_id}_retry.wav"

        speak(
            "Sorry, I didn't catch that. Could you please repeat?",
            output,
            "en",
        )

        return {
            "call_id": call_id,
            "caller": "",
            "bot": "Sorry, I didn't catch that.",
            "audio": output,
        }

    print("CALLER:", caller_text)

    add_message(
        call_id,
        "user",
        caller_text
    )

    print("STEP 2: LLM")

    history = get_history(call_id)

    # keep last 10 messages
    history = history[-20:]

    llm_start = time.time()

    answer, hangup = ask_llm(history, lang)

    print(
        "LLM:",
        int((time.time() - llm_start) * 1000),
        "ms"
    )

    add_message(
        call_id,
        "assistant",
        answer
    )

    print("BOT:", answer)

    print("STEP 3: TTS")

    output_file = f"audio/{call_id}.wav"

    tts_start = time.time()

    try:
        speak(
            answer,
            output_file,
            lang
        )
    except Exception as e:

        print("TTS ERROR:", e)

        return {
            "caller": caller_text,
            "bot": answer,
            "audio": None
        }

    print(
        "TTS:",
        int((time.time() - tts_start) * 1000),
        "ms"
    )

    print(
        "TOTAL:",
        int((time.time() - start_total) * 1000),
        "ms"
    )

    return {
        "call_id": call_id,
        "caller": caller_text,
        "bot": answer,
        "audio": output_file,
        "hangup": hangup,
    }

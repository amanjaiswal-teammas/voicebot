from .stt import transcribe
from .llm import ask_llm
from .supertonic_engine import speak
from .memory import (
    get_history,
    add_message
)
from .session_store import sessions
import time


def process_call(
    call_id,
    audio_file,
    interrupted_text=None,
):

    print("STEP 1: STT")

    print("INPUT AUDIO:", audio_file)

    start_total = time.time()

    if audio_file is None:
        caller_text = ""
        lang = "en"
    else:
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

        retries = sessions.get(call_id, {}).get("silent_retries", 0) + 1
        if call_id not in sessions:
            sessions[call_id] = {}
        sessions[call_id]["silent_retries"] = retries

        if retries >= 3:
            print(f"SILENT RETRY {retries} — HANGING UP")
            return {
                "call_id": call_id,
                "caller": "",
                "bot": "",
                "audio": None,
                "hangup": True,
                "lang": "en",
            }

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
            "lang": "en",
        }

    print("CALLER:", caller_text)

    if call_id in sessions:
        sessions[call_id]["silent_retries"] = 0

    if interrupted_text:
        context = (
            f"[The customer interrupted you. "
            f"You were saying: \"{interrupted_text}\". "
            f"The customer then said: \"{caller_text}\". "
            f"Respond naturally, acknowledging their interruption "
            f"if appropriate.]"
        )
        print("INTERRUPTED CONTEXT:", context)
        add_message(call_id, "system", context)

    add_message(
        call_id,
        "user",
        caller_text
    )

    print("STEP 2: LLM")

    history = get_history(call_id)

    history = history[-10:]

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

    if not hangup:
        goodbye_words = ["have a great day", "have a nice day", "have a good day"]
        if any(w in answer.lower() for w in goodbye_words):
            hangup = True
            print("HANGUP AUTO-DETECTED (goodbye phrase)")

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
            "audio": None,
            "lang": lang,
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
        "lang": lang,
    }

from .stt import transcribe
from .llm import ask_llm
from .supertonic_engine import speak
from .language import detect_language
from .memory import (
    get_history,
    add_message
)
from .session_store import sessions
import time
import re

SUPERTONIC_LANGS = {"en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id", "it", "lt", "lv", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi", "na"}


def _get_tts_lang(lang, text):
    if lang == "hi" and not re.search(r"[\u0900-\u097F]", text):
        return "en"
    if lang in SUPERTONIC_LANGS:
        return lang
    return "en"


def process_call(
    call_id,
    audio_file,
    interrupted_text=None,
):

    print("STEP 1: STT")

    print("INPUT AUDIO:", audio_file)

    start_total = time.time()

    if call_id not in sessions:
        sessions[call_id] = {}

    prev_lang = sessions[call_id].get("last_lang")

    if audio_file is None:
        caller_text = ""
        lang = prev_lang or "en"
    else:
        stt_start = time.time()

        stt_result = transcribe(audio_file, language_hint=prev_lang)

        caller_text = stt_result["text"]

        whisper_lang = stt_result["language"]

        if whisper_lang in SUPERTONIC_LANGS:
            lang = whisper_lang
        else:
            lang = detect_language(caller_text)

        if lang == "en" and prev_lang == "hi" and caller_text.strip():
            text_lang = detect_language(caller_text)
            if text_lang == "hi":
                lang = "hi"

        print(
            "WHISPER LANG:", whisper_lang,
            "| DETECTED LANG:", lang,
            "| PREV LANG:", prev_lang
        )

        print(
            "STT:",
            int((time.time() - stt_start) * 1000),
            "ms"
        )


    if not caller_text.strip():

        retries = sessions.get(call_id, {}).get("silent_retries", 0) + 1
        sessions[call_id]["silent_retries"] = retries

        if retries >= 3:
            print(f"SILENT RETRY {retries} — HANGING UP")
            return {
                "call_id": call_id,
                "caller": "",
                "bot": "",
                "audio": None,
                "hangup": True,
                "lang": lang,
            }

        silent_lang = lang if lang in SUPERTONIC_LANGS else "en"
        output = f"audio/{call_id}_retry.wav"

        if silent_lang == "hi":
            speak(
                "Sorry, main samajh nahi paayi. Kya aap dobara bol sakti hain?",
                output,
                "en",
            )
        else:
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
            "lang": silent_lang,
        }

    print("CALLER:", caller_text)

    sessions[call_id]["silent_retries"] = 0
    sessions[call_id]["last_lang"] = lang

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
    print(f"LLM_HANGUP={hangup}")

    if not hangup:
        goodbye_words_en = ["have a great day", "have a nice day", "have a good day"]
        goodbye_words_hi = ["aapka din ho", "acha din ho", "din ho achha"]
        all_goodbye = goodbye_words_en + goodbye_words_hi
        if any(w in answer.lower() for w in all_goodbye):
            hangup = True
            print("HANGUP AUTO-DETECTED (goodbye phrase)")

    print("STEP 3: TTS")

    output_file = f"audio/{call_id}.wav"

    tts_start = time.time()

    tts_lang = _get_tts_lang(lang, answer)

    try:
        speak(
            answer,
            output_file,
            tts_lang
        )
    except Exception as e:

        print("TTS ERROR:", e)

        return {
            "call_id": call_id,
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

    result = {
        "call_id": call_id,
        "caller": caller_text,
        "bot": answer,
        "audio": output_file,
        "hangup": hangup,
        "lang": lang,
    }
    print(f"PROCESS_CALL RETURN: hangup={hangup} lang={lang} bot_len={len(answer)}")
    return result

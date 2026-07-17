from .stt import transcribe
from .llm import ask_llm_stream
from .supertonic_engine import speak, split_into_segments
from .language import detect_language, detect_language_switch
from .memory import (
    get_history,
    add_message
)
from .session_store import sessions
import time
import re
import os

SUPERTONIC_LANGS = {"en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id", "it", "lt", "lv", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi", "na"}


def _get_tts_lang(lang, text):
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
        lang = prev_lang or "hi"
    else:
        stt_start = time.time()

        stt_result = transcribe(audio_file, language_hint=prev_lang)

        caller_text = stt_result["text"]

        whisper_lang = stt_result["language"]

        switch_lang = detect_language_switch(caller_text)
        if switch_lang:
            lang = switch_lang
            text_lang = lang
            print(f"LANGUAGE SWITCH DETECTED → {lang}")
        else:
            text_lang = detect_language(caller_text)

            if text_lang == "hi":
                lang = "hi"
            elif text_lang == "en":
                lang = "en"
            elif whisper_lang in ("hi", "en"):
                lang = whisper_lang
            else:
                lang = prev_lang or "hi"

        print(
            "WHISPER LANG:", whisper_lang,
            "| TEXT LANG:", text_lang,
            "| FINAL LANG:", lang,
            "| PREV LANG:", prev_lang
        )

        print(
            "STT:",
            int((time.time() - stt_start) * 1000),
            "ms"
        )


    if not caller_text.strip():

        if interrupted_text:
            print("EMPTY BARGE-IN — skipping sorry, will re-listen")
            return {
                "call_id": call_id,
                "caller": "",
                "bot": "",
                "audio": None,
                "segments": [],
                "hangup": False,
                "lang": lang,
            }

        retries = sessions.get(call_id, {}).get("silent_retries", 0) + 1
        sessions[call_id]["silent_retries"] = retries

        if retries >= 3:
            print(f"SILENT RETRY {retries} — HANGING UP")
            return {
                "call_id": call_id,
                "caller": "",
                "bot": "",
                "audio": None,
                "segments": [],
                "hangup": True,
                "lang": lang,
            }

        silent_lang = lang if lang in SUPERTONIC_LANGS else "en"
        output = f"audio/{call_id}_retry.wav"

        if silent_lang == "hi":
            speak(
                "माफ़ कीजिए, मैं समझ नहीं पाई। क्या आप दोबारा बोल सकती हैं?",
                output,
                "hi",
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
            "segments": [],
            "lang": silent_lang,
        }

    print("CALLER:", caller_text)

    sessions[call_id]["silent_retries"] = 0
    sessions[call_id]["last_lang"] = lang

    text_lower = caller_text.lower().strip()

    is_lang_switch = detect_language_switch(caller_text) is not None

    is_rejection = False
    if not is_lang_switch:
        is_rejection = bool(re.search(
            r"(nahi|nahin|nahi chahiye|mana hai|nahi lena|nahi chahte|nahi mangta|nahi karna|nahi karunga|nahi karungi|matlab nahi|bilkul nahi|ekdum nahi|"
            r"नहीं[\s,।.!]+चाहिए|मना[\s,।.!]+है|नहीं[\s,।.!]+लेना|नहीं[\s,।.!]+चाहते|नहीं[\s,।.!]+मंगता|नहीं[\s,।.!]+करना|"
            r"बिल्कुल[\s,।.!]+नहीं|एकदम[\s,।.!]+नहीं|नहीं[\s,।.!]+समझ|नहीं[\s,।.!]+सुनना|नहीं[\s,।.!]+करूँ|नहीं[\s,।.!]+करूंगा|नहीं[\s,।.!]+करूंगी|"
            r"नहीं[\s,।.!]+चाहे|नहीं[\s,।.!]+चाहत)",
            text_lower
        )) or bool(re.search(
            r"\b(no|skip|not interested|don'?t\s*want)\b",
            text_lower
        ))

    if is_rejection:
        sessions[call_id]["no_count"] = sessions[call_id].get("no_count", 0) + 1
        no_count = sessions[call_id]["no_count"]
        print(f"REJECTION #{no_count}: {caller_text}")
        if no_count >= 2:
            add_message(call_id, "system",
                "[Customer has refused twice. STOP everything. You MUST respond with ONLY a goodbye message like 'शुक्रिया, अच्छा दिन हो!' or 'Thank you, have a great day!'. "
                "Do NOT ask for reasons. Do NOT pitch. Do NOT continue the conversation. ONLY say goodbye.]")
            sessions[call_id]["force_goodbye"] = True
        else:
            if lang == "hi":
                add_message(call_id, "system",
                    "[Customer refused. You MUST ask for the reason in Hindi only. "
                    "Say: 'ठीक है, क्या वजह है?' — nothing else.]")
            else:
                add_message(call_id, "system",
                    "[Customer refused. You MUST ask for the reason in English only. "
                    "Say: 'No problem. May I know why?' — nothing else.]")
    else:
        sessions[call_id]["no_count"] = 0

    if interrupted_text:
        if sessions[call_id].get("force_goodbye"):
            pass
        else:
            context = (
                f"[Customer interrupted. "
                f"Customer said: \"{caller_text}\". "
                f"Respond to what the customer said.]"
            )
            print("INTERRUPTED CONTEXT:", context)
            add_message(call_id, "system", context)

    add_message(
        call_id,
        "user",
        caller_text
    )

    print("STEP 2: LLM (STREAMING)")

    history = get_history(call_id)

    history = history[-6:]

    llm_start = time.time()

    full_answer = ""
    hangup = False
    tts_lang = _get_tts_lang(lang, "")

    segments = []
    pending_text = ""

    for sentence, is_done, seg_hangup in ask_llm_stream(history, lang):
        elapsed = int((time.time() - llm_start) * 1000)

        if is_done:
            full_answer = sentence
            hangup = seg_hangup
            print(f"LLM DONE: {elapsed}ms answer_len={len(full_answer)}")
        else:
            pending_text += (" " if pending_text else "") + sentence
            print(f"LLM SENTENCE ({elapsed}ms): {sentence[:60]}...")

            tts_lang = _get_tts_lang(lang, sentence)
            seg_idx = len(segments)
            seg_path = f"audio/{call_id}_stream_{seg_idx}.wav"
            try:
                speak(sentence, seg_path, tts_lang)
                segments.append((sentence, seg_path))
                print(f"TTS PRE-GEN: {seg_path}")
            except Exception as e:
                print(f"TTS STREAM ERROR: {e}")

    if not full_answer:
        full_answer = pending_text

    if pending_text and not segments:
        tts_lang = _get_tts_lang(lang, pending_text)
        seg_path = f"audio/{call_id}_stream_0.wav"
        try:
            speak(pending_text, seg_path, tts_lang)
            segments.append((pending_text, seg_path))
        except Exception as e:
            print(f"TTS FINAL ERROR: {e}")

    add_message(
        call_id,
        "assistant",
        full_answer
    )

    print("BOT:", full_answer)
    print(f"LLM_HANGUP={hangup}")

    if sessions[call_id].get("force_goodbye"):
        hangup = True
        sessions[call_id]["force_goodbye"] = False
        print("HANGUP FORCED (rejection #2)")

    if not hangup:
        goodbye_words_en = [
            "have a great day", "have a nice day", "have a good day",
            "thank you for your time", "bye", "goodbye",
        ]
        goodbye_words_hi = [
            "aapka din ho", "acha din ho", "din ho achha",
            "aapka time ke liye thank", "thank you for your time",
            "अच्छा दिन हो", "दिन अच्छा हो", "आपका दिन हो",
            "शुक्रिया", "अलविदा",
        ]
        all_goodbye = goodbye_words_en + goodbye_words_hi
        if any(w in caller_text.lower() for w in all_goodbye):
            hangup = True
            print("HANGUP AUTO-DETECTED (customer said goodbye phrase)")
        elif any(w in full_answer.lower() for w in all_goodbye):
            hangup = True
            print("HANGUP AUTO-DETECTED (bot said goodbye phrase)")

    print("STEP 3: TTS")

    output_file = f"audio/{call_id}.wav"

    if segments:
        final_segments = segments
    else:
        tts_lang = _get_tts_lang(lang, full_answer)
        tts_start = time.time()
        try:
            speak(
                full_answer,
                output_file,
                tts_lang
            )
        except Exception as e:
            print("TTS ERROR:", e)
            return {
                "call_id": call_id,
                "caller": caller_text,
                "bot": full_answer,
                "audio": None,
                "segments": [],
                "hangup": hangup,
                "lang": lang,
            }

        print(
            "TTS:",
            int((time.time() - tts_start) * 1000),
            "ms"
        )
        final_segments = [(full_answer, output_file)]

    print(
        "TOTAL:",
        int((time.time() - start_total) * 1000),
        "ms"
    )

    result = {
        "call_id": call_id,
        "caller": caller_text,
        "bot": full_answer,
        "audio": output_file,
        "segments": final_segments,
        "hangup": hangup,
        "lang": lang,
    }
    print(f"PROCESS_CALL RETURN: hangup={hangup} lang={lang} bot_len={len(full_answer)} segments={len(final_segments)}")
    return result

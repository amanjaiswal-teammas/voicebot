"""
Conversation state machine for the voicebot.

Flow:
    STT → intent classification → handler → respond

Intent priority (first match wins):
    1. silent (empty audio)
    2. lang_switch
    3. rejection
    4. awaiting_reason_response  (LLM objection handler)
    5. interest / order_intent   (pre-LLM bypass)
    6. order_collecting          (regex extraction)
    7. garbled                   (ask to repeat)
    8. llm                       (streaming LLM fallback)
"""

from .stt import transcribe
from .llm import ask_llm_stream
from .supertonic_engine import speak, split_into_segments
from .language import detect_language, detect_language_switch
from .memory import get_history, add_message
from .session_store import sessions
from .patterns import (
    SUPERTONIC_LANGS, KNOWN_WORDS, MALE_TO_FEMALE,
    PITCH_HI, PITCH_EN, ORDER_COLLECT_HI, ORDER_COLLECT_EN,
    ORDER_MAX_TURNS, ASK_REASON_HI, ASK_REASON_EN,
    GOODBYE_HI, GOODBYE_EN, ASK_REPEAT_HI, ASK_REPEAT_EN,
    REJECTION_RE, REJECTION_EN_RE, REJECTION_STANDALONE_RE,
    INTEREST_RE, EXPLICIT_REQUEST_RE, ORDER_INTENT_RE,
)
from .order import (
    extract_order_details, order_needs_more,
    build_order_response, build_order_confirmation,
)

import time
import re


# ============================================================================
# TTS language selector
# ============================================================================

def _get_tts_lang(lang: str, text: str) -> str:
    if lang in SUPERTONIC_LANGS:
        return lang
    return "en"


# ============================================================================
# Post-processing helpers
# ============================================================================

def _fix_hindi_gender(text: str) -> str:
    for pattern, replacement in MALE_TO_FEMALE:
        text = re.sub(pattern, replacement, text)
    return text


def _fix_mid_greeting(text: str) -> str:
    text = re.sub(r'^(नमस्ते!\s*)', '', text)
    text = re.sub(r'^(Good morning!\s*)', '', text)
    text = re.sub(r'^(Good evening!\s*)', '', text)
    text = re.sub(r'^(Hello!\s*)', '', text)
    text = re.sub(r'^(नमस्ते\s+)', '', text)
    return text


def _post_process(text: str, lang: str) -> str:
    if lang == "hi":
        text = _fix_hindi_gender(text)
    text = _fix_mid_greeting(text)
    return text.strip()


# ============================================================================
# Garbled detection
# ============================================================================

def _is_garbled(text: str) -> bool:
    if not re.search(r'[\u0900-\u097F]', text):
        return False
    words = re.findall(r'[\u0900-\u097F]+|[a-zA-Z]+', text.lower())
    if not words:
        return True
    known_count = sum(1 for w in words if w in KNOWN_WORDS)
    ratio = known_count / len(words) if words else 0
    if ratio < 0.3 and len(words) >= 2:
        print(f"GARBLED CHECK: {len(words)} words, {known_count} known, ratio={ratio:.2f}")
        return True
    return False


# ============================================================================
# Intent classification
# ============================================================================

def _pitch_given(session: dict) -> bool:
    return any(
        PITCH_HI in m.get("content", "") or PITCH_EN in m.get("content", "")
        for m in session.get("messages", [])
        if m.get("role") == "assistant"
    )


def _classify_intent(caller_text: str, session: dict, lang: str):
    """Return (intent_name, extras_dict) tuple."""
    text_lower = caller_text.lower().strip()

    # Language switch?
    if detect_language_switch(caller_text):
        return ("lang_switch", {})

    # Rejection?
    is_rejection = bool(REJECTION_RE.search(text_lower)) or bool(
        REJECTION_EN_RE.search(text_lower)
    )
    if not is_rejection and not session.get("awaiting_reason"):
        if REJECTION_STANDALONE_RE.search(caller_text):
            is_rejection = True
    if is_rejection:
        return ("rejection", {})

    # Already awaiting a reason → let LLM handle objection
    if session.get("awaiting_reason"):
        return ("awaiting_reason_response", {})

    # Interest (short utterance only)?
    if len(caller_text.split()) <= 12 and INTEREST_RE.search(caller_text):
        explicit = bool(EXPLICIT_REQUEST_RE.search(caller_text))
        return ("interest", {"explicit_request": explicit})

    # Order intent (but not already collecting)?
    if not session.get("order_collecting") and ORDER_INTENT_RE.search(caller_text):
        return ("order_intent", {})

    # Currently collecting order details?
    if session.get("order_collecting"):
        return ("order_collecting", {})

    # Garbled?
    if _is_garbled(caller_text):
        return ("garbled", {})

    return ("llm", {})


# ============================================================================
# Single TTS + return helper — kills the 8× boilerplate
# ============================================================================

def _respond(
    call_id: str,
    caller_text: str,
    full_answer: str,
    lang: str,
    hangup: bool = False,
    segments=None,
):
    """Synthesize *full_answer* to audio, build standard return dict."""
    if segments is None:
        segments = []

    output_file = f"audio/{call_id}.wav"
    tts_lang = _get_tts_lang(lang, full_answer)

    if not segments:
        try:
            speak(full_answer, output_file, tts_lang)
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
        segments = [(full_answer, output_file)]

    return {
        "call_id": call_id,
        "caller": caller_text,
        "bot": full_answer,
        "audio": output_file,
        "segments": segments,
        "hangup": hangup,
        "lang": lang,
    }


# ============================================================================
# Intent handlers
# ============================================================================

def _handle_rejection(call_id, caller_text, session, lang):
    session["no_count"] = session.get("no_count", 0) + 1
    session["awaiting_reason"] = False
    session["order_collecting"] = False
    session.pop("order_details", None)
    session.pop("order_turns", None)
    no_count = session["no_count"]

    print(f"REJECTION #{no_count}: {caller_text}")

    if no_count >= 2:
        full_answer = GOODBYE_HI if lang == "hi" else GOODBYE_EN
        print(f"FORCED GOODBYE (rejection #{no_count}): {full_answer}")
        add_message(call_id, "assistant", full_answer)
        session["no_count"] = 0
        session["awaiting_reason"] = False
        return _respond(call_id, caller_text, full_answer, lang, hangup=True)

    full_answer = ASK_REASON_HI if lang == "hi" else ASK_REASON_EN
    print(f"FORCED ASK REASON (rejection #{no_count}): {full_answer}")
    add_message(call_id, "assistant", full_answer)
    session["awaiting_reason"] = True
    return _respond(call_id, caller_text, full_answer, lang)


def _handle_lang_switch(call_id, caller_text, session, lang):
    full_answer = PITCH_HI if lang == "hi" else PITCH_EN
    print(f"LANG SWITCH → {lang} — BYPASSING LLM, PITCH: {full_answer}")
    add_message(call_id, "assistant", full_answer)
    return _respond(call_id, caller_text, full_answer, lang)


def _handle_awaiting_reason(call_id, caller_text, session, lang):
    session["awaiting_reason"] = False
    if lang == "hi":
        reason_msg = (
            "[Customer just gave a reason for refusing. DO NOT ask 'क्या वजह है?' again. "
            f'Customer said: "{caller_text}". '
            "Address their concern directly. If cheaper: say 'हमारे पास 60% छूट है, यह बहुत अच्छा ऑफ़र है।' "
            "Then ask once more politely: 'एक बार सोच कर बताइए?' If still no, say goodbye.]"
        )
    else:
        reason_msg = (
            "[Customer just gave a reason for refusing. DO NOT ask 'May I know why?' again. "
            f'Customer said: "{caller_text}". '
            "Address their concern directly. If cheaper: say 'We have 60% off, that's a great deal!' "
            "Then ask once more if they want to order. If still no, say goodbye.]"
        )
    print("REASON GIVEN — injecting objection handler")
    add_message(call_id, "system", reason_msg)
    # Fall through to LLM — return None to signal "continue to LLM"
    return None


def _handle_interest(call_id, caller_text, session, lang, explicit_request):
    if explicit_request:
        full_answer = PITCH_HI if lang == "hi" else PITCH_EN
    elif _pitch_given(session):
        full_answer = ORDER_COLLECT_HI if lang == "hi" else ORDER_COLLECT_EN
    else:
        full_answer = PITCH_HI if lang == "hi" else PITCH_EN

    is_order_collect = full_answer in (ORDER_COLLECT_HI, ORDER_COLLECT_EN)
    action = "ORDER COLLECT" if is_order_collect else "PITCH"
    print(f"INTEREST DETECTED — BYPASSING LLM, {action}: {full_answer}")
    add_message(call_id, "assistant", full_answer)
    if is_order_collect:
        session["order_collecting"] = True
    return _respond(call_id, caller_text, full_answer, lang)


def _handle_order_intent(call_id, caller_text, session, lang):
    if _pitch_given(session):
        full_answer = ORDER_COLLECT_HI if lang == "hi" else ORDER_COLLECT_EN
        print(f"ORDER INTENT — BYPASSING LLM, COLLECTING DETAILS: {full_answer}")
        add_message(call_id, "assistant", full_answer)
        session["order_collecting"] = True
    else:
        full_answer = PITCH_HI if lang == "hi" else PITCH_EN
        print(f"ORDER INTENT (no pitch yet) — PITCHING: {full_answer}")
        add_message(call_id, "assistant", full_answer)
    return _respond(call_id, caller_text, full_answer, lang)


def _handle_order_collecting(call_id, caller_text, session, lang):
    order_turns = session.get("order_turns", 0)
    if order_turns >= ORDER_MAX_TURNS:
        session["order_collecting"] = False
        session.pop("order_details", None)
        session.pop("order_turns", None)
        print("ORDER COLLECTING — max turns reached, giving up")
        return None  # fall through to LLM

    existing = session.get("order_details", {})
    details = extract_order_details(caller_text, existing)
    session["order_details"] = details
    session["order_turns"] = order_turns + 1
    print(f"ORDER COLLECTING — extracted: {details}")

    if not order_needs_more(details):
        full_answer = build_order_confirmation(lang, details)
        print(f"ORDER COMPLETE — confirming: {full_answer}")
        add_message(call_id, "assistant", full_answer)
        session["order_collecting"] = False
        session.pop("order_details", None)
        session.pop("order_turns", None)
        return _respond(call_id, caller_text, full_answer, lang)

    full_answer = build_order_response(lang, details)
    print(f"ORDER COLLECTING — asking for more: {full_answer}")
    add_message(call_id, "assistant", full_answer)
    return _respond(call_id, caller_text, full_answer, lang)


def _handle_garbled(call_id, caller_text, session, lang):
    full_answer = ASK_REPEAT_HI if lang == "hi" else ASK_REPEAT_EN
    print(f"GARBLED TEXT — BYPASSING LLM, ASKING TO REPEAT: {full_answer}")
    add_message(call_id, "assistant", full_answer)
    return _respond(call_id, caller_text, full_answer, lang)


# ============================================================================
# LLM streaming handler
# ============================================================================

def _handle_llm(call_id, caller_text, session, lang):
    print("STEP 2: LLM (STREAMING)")

    history = get_history(call_id)[-6:]

    llm_start = time.time()
    full_answer = ""
    hangup = False
    tts_lang = _get_tts_lang(lang, "")
    segments = []
    pending_text = ""

    for sentence, is_done, seg_hangup in ask_llm_stream(history, lang):
        elapsed = int((time.time() - llm_start) * 1000)

        if is_done:
            full_answer = _post_process(sentence, lang)
            hangup = seg_hangup
            print(f"LLM DONE: {elapsed}ms answer_len={len(full_answer)}")
        else:
            processed = _post_process(sentence, lang)
            pending_text += (" " if pending_text else "") + processed
            print(f"LLM SENTENCE ({elapsed}ms): {processed[:60]}...")

            tts_lang = _get_tts_lang(lang, processed)
            seg_idx = len(segments)
            seg_path = f"audio/{call_id}_stream_{seg_idx}.wav"
            try:
                speak(processed, seg_path, tts_lang)
                segments.append((processed, seg_path))
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

    add_message(call_id, "assistant", full_answer)
    print("BOT:", full_answer)
    print(f"LLM_HANGUP={hangup}")

    if not hangup:
        hangup = _detect_goodbye(caller_text, full_answer)

    output_file = f"audio/{call_id}.wav"

    if segments:
        return {
            "call_id": call_id,
            "caller": caller_text,
            "bot": full_answer,
            "audio": output_file,
            "segments": segments,
            "hangup": hangup,
            "lang": lang,
        }

    tts_lang = _get_tts_lang(lang, full_answer)
    tts_start = time.time()
    try:
        speak(full_answer, output_file, tts_lang)
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
    print("TTS:", int((time.time() - tts_start) * 1000), "ms")

    return {
        "call_id": call_id,
        "caller": caller_text,
        "bot": full_answer,
        "audio": output_file,
        "segments": [(full_answer, output_file)],
        "hangup": hangup,
        "lang": lang,
    }


_GOODBYE_WORDS_EN = [
    "have a great day", "have a nice day", "have a good day",
    "thank you for your time", "bye", "goodbye",
]
_GOODBYE_WORDS_HI = [
    "aapka din ho", "acha din ho", "din ho achha",
    "aapka time ke liye thank", "thank you for your time",
    "अच्छा दिन हो", "दिन अच्छा हो", "आपका दिन हो",
    "शुक्रिया, अच्छा दिन", "शुक्रिया,अच्छा दिन", "अलविदा",
]
_ALL_GOODBYE = _GOODBYE_WORDS_EN + _GOODBYE_WORDS_HI


def _detect_goodbye(caller_text: str, bot_answer: str) -> bool:
    if any(w in caller_text.lower() for w in _ALL_GOODBYE):
        print("HANGUP AUTO-DETECTED (customer said goodbye phrase)")
        return True
    if any(w in bot_answer.lower() for w in _ALL_GOODBYE):
        print("HANGUP AUTO-DETECTED (bot said goodbye phrase)")
        return True
    return False


# ============================================================================
# Main entry point
# ============================================================================

def process_call(call_id: str, audio_file, interrupted_text=None):

    print("STEP 1: STT")
    print("INPUT AUDIO:", audio_file)

    start_total = time.time()

    if call_id not in sessions:
        sessions[call_id] = {}

    session = sessions[call_id]
    prev_lang = session.get("last_lang")

    # ------------------------------------------------------------------
    # STT + language detection
    # ------------------------------------------------------------------
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
            "| PREV LANG:", prev_lang,
        )
        print("STT:", int((time.time() - stt_start) * 1000), "ms")

    # ------------------------------------------------------------------
    # Silent / empty audio handling
    # ------------------------------------------------------------------
    if not caller_text.strip():
        return _handle_silent(call_id, interrupted_text, lang)

    print("CALLER:", caller_text)
    session["silent_retries"] = 0
    session["last_lang"] = lang

    # ------------------------------------------------------------------
    # Barge-in context injection
    # ------------------------------------------------------------------
    if interrupted_text and not detect_language_switch(caller_text):
        context = (
            f'[Customer interrupted. Customer said: "{caller_text}". '
            f"Respond to what the customer said.]"
        )
        print("INTERRUPTED CONTEXT:", context)
        add_message(call_id, "system", context)

    add_message(call_id, "user", caller_text)

    # ------------------------------------------------------------------
    # Classify intent
    # ------------------------------------------------------------------
    intent, extras = _classify_intent(caller_text, session, lang)

    # ------------------------------------------------------------------
    # Dispatch to handler
    # ------------------------------------------------------------------
    if intent == "rejection":
        return _handle_rejection(call_id, caller_text, session, lang)

    session["no_count"] = 0  # clear on non-rejection

    if intent == "lang_switch":
        return _handle_lang_switch(call_id, caller_text, session, lang)

    if intent == "awaiting_reason_response":
        result = _handle_awaiting_reason(call_id, caller_text, session, lang)
        if result is not None:
            return result
        # Falls through to LLM below

    if intent == "interest":
        return _handle_interest(
            call_id, caller_text, session, lang, extras["explicit_request"]
        )

    if intent == "order_intent":
        return _handle_order_intent(call_id, caller_text, session, lang)

    if intent == "order_collecting":
        result = _handle_order_collecting(call_id, caller_text, session, lang)
        if result is not None:
            return result
        # max turns reached → fall through to LLM

    if intent == "garbled":
        return _handle_garbled(call_id, caller_text, session, lang)

    # ------------------------------------------------------------------
    # LLM fallback
    # ------------------------------------------------------------------
    return _handle_llm(call_id, caller_text, session, lang)


def _handle_silent(call_id, interrupted_text, lang):
    if interrupted_text:
        print("EMPTY BARGE-IN — skipping sorry, will re-listen")
        return {
            "call_id": call_id, "caller": "", "bot": "",
            "audio": None, "segments": [], "hangup": False, "lang": lang,
        }

    retries = sessions.get(call_id, {}).get("silent_retries", 0) + 1
    sessions[call_id]["silent_retries"] = retries

    if retries >= 3:
        print(f"SILENT RETRY {retries} — HANGING UP")
        return {
            "call_id": call_id, "caller": "", "bot": "",
            "audio": None, "segments": [], "hangup": True, "lang": lang,
        }

    silent_lang = lang if lang in SUPERTONIC_LANGS else "en"
    if silent_lang == "hi":
        msg = "माफ़ कीजिए, समझ नहीं पाई। एक बार फिर से बोलेंगे?"
    else:
        msg = "Sorry, I didn't catch that. Could you say that once more?"

    output = f"audio/{call_id}_retry.wav"
    speak(msg, output, "hi" if silent_lang == "hi" else "en")

    return {
        "call_id": call_id, "caller": "", "bot": "Sorry, I didn't catch that.",
        "audio": output, "segments": [], "lang": silent_lang,
    }

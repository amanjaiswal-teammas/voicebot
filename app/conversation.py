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

PITCH_HI = "Supreme Perfume Box — 4 प्रीमियम परफ्यूम्स Rs 1,599 में, 60% छूट। ऑर्डर करेंगे?"
PITCH_EN = "Supreme Perfume Box — 4 perfumes, Rs 1,599, 60% off. Want to order?"

ASK_REASON_HI = "ठीक है, क्या वजह है?"
ASK_REASON_EN = "No problem. May I know why?"

GOODBYE_HI = "शुक्रिया, अच्छा दिन हो!"
GOODBYE_EN = "Thanks, have a great day!"

ASK_REPEAT_HI = "माफ़ कीजिए, क्या आप दोबारा बोल सकती हैं?"
ASK_REPEAT_EN = "Sorry, could you repeat that?"

REPEAT_ASKERS = re.compile(
    r"(bataiye|batai[eē]|aage bata|aage batai|ha[nm]\s*(ji|bhi)?|sure|yes|tell me|sunao|suno|bolo|haan ji|hanji|"
    r"बताइए|बताईये|बता दो|बता दीजिए|आगे बताइए|हाँ\s*(जी|भी)?|सुनिए|बोलिए|बताओ|"
    r"nahi|nahin|nahi chahiye|mana hai|nahi lena|nahi chahte|nahi karna|nahi karunga|nahi karungi|"
    r"नहीं|चाहिए|लेना|चाहते|करना|मना|बिल्कुल|एकदम|"
    r"ना\s*चाहेंगे|ना\s*चाहूँगा|ना\s*चाहूंगी|"
    r"order|place|confirm|cancel|email|name|address|phone|payment|pincode|"
    r"ऑर्डर|ईमेल|नाम|पता|फ़ोन|पेमेंट|पिनकोड|"
    r"क्यों|क्या|कैसे|कहाँ|कब|कौन|कितना|"
    r"हाँ|ना|जी|बिल्कुल|एकदम|ठीक|अच्छा|बुरा|"
    r"समझ|समझे|सुन|सुना|देख|बोल|बता|कर|ले|दे|जा|आ|खा|पी|"
    r"supreme|perfume|box|perfumes|off|order|"
    r"english|इंग्लिश|इंगलीज|इंगलेश|इंडिज|hindi|हिंदी)",
    re.I
)


KNOWN_WORDS = set(
    "हाँ ना जी बिल्कुल एकदम ठीक अच्छा बुरा समझ समझे सुन सुना देख बोल बता कर ले दे जा आ खा पी "
    "चाहिए लेना चाहते करना मना बोलो करो करे करता करती बताओ बताइए "
    "है हो हैं था थी होगा होगी होगे करेंगे करूँगा करूंगी दूँगा दूंगी "
    "क्यों क्या कैसे कहाँ कब कौन कितना कौनसा "
    "क्या है कैसे हैं क्यों नहीं कहाँ कब कौन "
    "supreme perfume box perfumes off order email name address phone payment pincode "
    "english hindi इंग्लिश हिंदी अंग्रेज़ी "
    "ऑर्डर ईमेल नाम पता फ़ोन पेमेंट पिनकोड "
    "बताइए बताईये बता दो आगे सुनिए बोलिए बताओ "
    "nahi nahin chahiye mana lena chahte karna karunga karungi "
    "nahi chahiye mana hai nahi lena nahi chahte nahi karna "
    "nahi karunga nahi karungi bilkul nahi ekdum nahi "
    "order place confirm cancel "
    "में से को पर ने तक लिए वाले का की के "
    "आप हम वो ये मैं तुम वह यह उस इस "
    "तो भी ही अब फिर क्योंकि लेकिन मगर "
    "मैंने उसने हमने तुमने आपने "
    "बता रही हूँ बता रहा हूँ बात कर रही हूँ बात कर रहा हूँ "
    "अंग्लिश इंगलिश इंगलीश इंगलिज ".split()
)


def _is_garbled(text):
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

INTEREST_RE = re.compile(
    r"(bataiye|batai[eē]|aage bata|aage batai|ha[nm]\s*(ji|bhi)?|sure|yes|tell me|ok bata|acha bata|sunao|suno|bolo|haan ji|hanji|bata de|bata do|kya hai|kya baat|kaise|kya matlab|thik hai bata|acha|samjhe nahi|samajh nahi aaya|nahi bataya|nahi batara|pura nahi bata|"
    r"बताइए|बताईये|बता दो|बता दीजिए|आगे बताइए|हाँ\s*(जी|भी)?|सुनिए|बोलिए|बताओ|समझे\s+नहीं|समझ\s+नहीं\s+आया|क्या\s+है|क्या\s+बात|कैसे|क्या\s+मतलब|अच्छा\s+बता|ठीक\s+है\s+बता|"
    r"नहीं\s+बता|पूरा\s+नहीं\s+बता|नहीं\s+बतारें|नहीं\s+बताइए|"
    r"बिल्कुल[\s।,.]*(हाँ|जी|बताओ|बताइए|बोलिए|सुनाइए|करेंगे|ले लेंगे|चाहेंगे|प्लीज|please|ok|okay|sure|done|हां|हाँ)",
    re.I
)

MALE_TO_FEMALE = [
    (r'हूँ', 'हूँ'),
    (r'रहा हूँ', 'रही हूँ'),
    (r'रहा हूं', 'रही हूँ'),
    (r'पा रहा हूं', 'पा रही हूँ'),
    (r'पा रहा हूँ', 'पा रही हूँ'),
    (r'गया हूँ', 'गई हूँ'),
    (r'गया हूं', 'गई हूँ'),
    (r'आया हूँ', 'आई हूँ'),
    (r'आया हूं', 'आई हूँ'),
    (r'बता रहा हूँ', 'बता रही हूँ'),
    (r'बता रहा हूं', 'बता रही हूँ'),
    (r'कर रहा हूँ', 'कर रही हूँ'),
    (r'कर रहा हूं', 'कर रही हूँ'),
    (r'समझ रहा हूँ', 'समझ रही हूँ'),
    (r'समझ रहा हूं', 'समझ रही हूँ'),
    (r'सोच रहा हूँ', 'सोच रही हूँ'),
    (r'सोच रहा हूं', 'सोच रही हूँ'),
    (r'ले रहा हूँ', 'ले रही हूँ'),
    (r'ले रहा हूं', 'ले रही हूँ'),
    (r'दे रहा हूँ', 'दे रही हूँ'),
    (r'दे रहा हूं', 'दे रही हूँ'),
    (r'बोल रहा हूँ', 'बोल रही हूँ'),
    (r'बोल रहा हूं', 'बोल रही हूँ'),
    (r'सुन रहा हूँ', 'सुन रही हूँ'),
    (r'सुन रहा हूं', 'सुन रही हूँ'),
    (r'मान रहा हूँ', 'मान रही हूँ'),
    (r'मान रहा हूं', 'मान रही हूँ'),
    (r'चाह रहा हूँ', 'चाह रही हूँ'),
    (r'चाह रहा हूं', 'चाह रही हूँ'),
    (r'हो गया हूँ', 'हो गई हूँ'),
    (r'हो गया हूं', 'हो गई हूँ'),
    (r'कह रहा हूँ', 'कह रही हूँ'),
    (r'कह रहा हूं', 'कह रही हूँ'),
    (r'कर सकता हूँ', 'कर सकती हूँ'),
    (r'कर सकता हूं', 'कर सकती हूँ'),
    (r'दे सकता हूँ', 'दे सकती हूँ'),
    (r'दे सकता हूं', 'दे सकती हूँ'),
    (r'बता सकता हूँ', 'बता सकती हूँ'),
    (r'बता सकता हूं', 'बता सकती हूँ'),
    (r'करूँगा', 'करूँगी'),
    (r'करूंगा', 'करूंगी'),
    (r'दूँगा', 'दूँगी'),
    (r'दूंगा', 'दूंगी'),
    (r'बताऊँगा', 'बताऊँगी'),
    (r'बताऊंगा', 'बताऊंगी'),
    (r'लूँगा', 'लूँगी'),
    (r'लूंगा', 'लूंगी'),
    (r'चाहूँगा', 'चाहूँगी'),
    (r'चाहूंगा', 'चाहूंगी'),
    (r'मदद कर सकता हूँ', 'मदद कर सकती हूँ'),
    (r'मदद कर सकता हूं', 'मदद कर सकती हूँ'),
    (r'डालता हूँ', 'डालती हूँ'),
    (r'डालता हूं', 'डालती हूँ'),
    (r'बताता हूँ', 'बताती हूँ'),
    (r'बताता हूं', 'बताती हूँ'),
]


def _fix_hindi_gender(text):
    if not text:
        return text
    for pattern, replacement in MALE_TO_FEMALE:
        text = re.sub(pattern, replacement, text)
    return text


def _fix_mid_greeting(text):
    if not text:
        return text
    text = re.sub(r'^(नमस्ते!\s*)', '', text)
    text = re.sub(r'^(Good morning!\s*)', '', text)
    text = re.sub(r'^(Good evening!\s*)', '', text)
    text = re.sub(r'^(Hello!\s*)', '', text)
    text = re.sub(r'^(नमस्ते\s+)', '', text)
    return text


def _post_process(text, lang):
    if lang == "hi":
        text = _fix_hindi_gender(text)
    text = _fix_mid_greeting(text)
    return text.strip()


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
    is_interest = False
    if not is_lang_switch:
        is_rejection = bool(re.search(
            r"(nahi|nahin|na chahiye|na chahte|na karunga|nahi chahiye|mana hai|nahi lena|nahi chahte|nahi mangta|nahi karna|nahi karunga|nahi karungi|matlab nahi|bilkul nahi|ekdum nahi|"
            r"नहीं[\s,।.!]+चाहिए|मना[\s,।.!]+है|नहीं[\s,।.!]+लेना|नहीं[\s,।.!]+चाहते|नहीं[\s,।.!]+मंगता|नहीं[\s,।.!]+करना|"
            r"ना[\s,।.!]+चाहेंगे|ना[\s,।.!]+चाहूँगा|ना[\s,।.!]+चाहूंगी|ना[\s,।.!]+करेंगे|ना[\s,।.!]+करूँगा|ना[\s,।.!]+करूंगी|ना[\s,।.!]+लेंगे|ना[\s,।.!]+लूँगा|ना[\s,।.!]+लूंगी|"
            r"बिल्कुल[\s,।.!]+नहीं|एकदम[\s,।.!]+नहीं|नहीं[\s,।.!]+सुनना|नहीं[\s,।.!]+करूँ|नहीं[\s,।.!]+करूंगा|नहीं[\s,।.!]+करूंगी|"
                r"नहीं[\s,।.!]+चाहे|नहीं[\s,।.!]+चाहत|"
                r"जी[\s,।.!]+नहीं|अब[\s,।.!]+नहीं|नहीं[\s,।.!]+जी\b|"
                r"नहीं[\s,।.!]+बोल|नहीं[\s,।.!]+चाहिए\w*|नहीं[\s,।.!]+लगता|"
                r"नहीं[\s,।.!]+गरेंगे|नहीं[\s,।.!]+गरूँगा|नहीं[\s,।.!]+गरूंगी|"
                r"नहीं[\s,।.!]+जी|नहीं\s*[।,.]?\s*$|"
                r"सस्त[ाेी]\w*\s+मिल|सस्त[ाेी]\w*\s+है|इससे\s+सस्त|उससे\s+सस्त|कहीं\s+और\s+सस्त)",
                text_lower
            )) or bool(re.search(
                r"\b(no|skip|not interested|don'?t\s*want)\b",
                text_lower
            ))
        if not is_rejection and len(caller_text.split()) <= 8:
            is_interest = bool(INTEREST_RE.search(caller_text))

    if is_rejection:
        sessions[call_id]["no_count"] = sessions[call_id].get("no_count", 0) + 1
        sessions[call_id]["awaiting_reason"] = False
        no_count = sessions[call_id]["no_count"]
        print(f"REJECTION #{no_count}: {caller_text}")
        if no_count >= 2:
            full_answer = GOODBYE_HI if lang == "hi" else GOODBYE_EN
            hangup = True
            print(f"FORCED GOODBYE (rejection #{no_count}): {full_answer}")
            add_message(call_id, "assistant", full_answer)
            sessions[call_id]["no_count"] = 0
            sessions[call_id]["awaiting_reason"] = False
            output_file = f"audio/{call_id}.wav"
            tts_lang = _get_tts_lang(lang, full_answer)
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
                    "hangup": True,
                    "lang": lang,
                }
            return {
                "call_id": call_id,
                "caller": caller_text,
                "bot": full_answer,
                "audio": output_file,
                "segments": [(full_answer, output_file)],
                "hangup": True,
                "lang": lang,
            }
        else:
            full_answer = ASK_REASON_HI if lang == "hi" else ASK_REASON_EN
            print(f"FORCED ASK REASON (rejection #{no_count}): {full_answer}")
            add_message(call_id, "assistant", full_answer)
            sessions[call_id]["awaiting_reason"] = True
            output_file = f"audio/{call_id}.wav"
            tts_lang = _get_tts_lang(lang, full_answer)
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
                    "hangup": False,
                    "lang": lang,
                }
            return {
                "call_id": call_id,
                "caller": caller_text,
                "bot": full_answer,
                "audio": output_file,
                "segments": [(full_answer, output_file)],
                "hangup": False,
                "lang": lang,
            }
    else:
        sessions[call_id]["no_count"] = 0

    if interrupted_text:
        if not is_lang_switch:
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

    if is_lang_switch:
        full_answer = PITCH_HI if lang == "hi" else PITCH_EN
        print(f"LANG SWITCH → {lang} — BYPASSING LLM, PITCH: {full_answer}")
        add_message(call_id, "assistant", full_answer)
        output_file = f"audio/{call_id}.wav"
        tts_lang = _get_tts_lang(lang, full_answer)
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
                "hangup": False,
                "lang": lang,
            }
        return {
            "call_id": call_id,
            "caller": caller_text,
            "bot": full_answer,
            "audio": output_file,
            "segments": [(full_answer, output_file)],
            "hangup": False,
            "lang": lang,
        }

    if sessions[call_id].get("awaiting_reason") and not is_rejection:
        sessions[call_id]["awaiting_reason"] = False
        if lang == "hi":
            reason_msg = (
                "[Customer just gave a reason for refusing. DO NOT ask 'क्या वजह है?' again. "
                f"Customer said: \"{caller_text}\". "
                "Address their concern directly. If cheaper: say 'हमारे पास 60% छूट है, यह बहुत अच्छा ऑफ़र है।' "
                "Then ask once more if they want to order. If still no, say goodbye.]"
            )
        else:
            reason_msg = (
                "[Customer just gave a reason for refusing. DO NOT ask 'May I know why?' again. "
                f"Customer said: \"{caller_text}\". "
                "Address their concern directly. If cheaper: say 'We have 60% off, that's a great deal!' "
                "Then ask once more if they want to order. If still no, say goodbye.]"
            )
        print("REASON GIVEN — injecting objection handler")
        add_message(call_id, "system", reason_msg)

    if is_interest:
        pitch_given = any(
            PITCH_HI in m.get("content", "") or PITCH_EN in m.get("content", "")
            for m in sessions[call_id].get("messages", [])
            if m.get("role") == "assistant"
        )
        if not pitch_given:
            full_answer = PITCH_HI if lang == "hi" else PITCH_EN
            print(f"INTEREST DETECTED — BYPASSING LLM, PITCH: {full_answer}")
            add_message(call_id, "assistant", full_answer)
            output_file = f"audio/{call_id}.wav"
            tts_lang = _get_tts_lang(lang, full_answer)
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
                    "hangup": False,
                    "lang": lang,
                }
            return {
                "call_id": call_id,
                "caller": caller_text,
                "bot": full_answer,
                "audio": output_file,
                "segments": [(full_answer, output_file)],
                "hangup": False,
                "lang": lang,
            }

    if _is_garbled(caller_text) and not sessions[call_id].get("awaiting_reason"):
        full_answer = ASK_REPEAT_HI if lang == "hi" else ASK_REPEAT_EN
        print(f"GARBLED TEXT — BYPASSING LLM, ASKING TO REPEAT: {full_answer}")
        add_message(call_id, "assistant", full_answer)
        output_file = f"audio/{call_id}.wav"
        tts_lang = _get_tts_lang(lang, full_answer)
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
                "hangup": False,
                "lang": lang,
            }
        return {
            "call_id": call_id,
            "caller": caller_text,
            "bot": full_answer,
            "audio": output_file,
            "segments": [(full_answer, output_file)],
            "hangup": False,
            "lang": lang,
        }

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

    add_message(
        call_id,
        "assistant",
        full_answer
    )

    print("BOT:", full_answer)
    print(f"LLM_HANGUP={hangup}")

    if not hangup:
        goodbye_words_en = [
            "have a great day", "have a nice day", "have a good day",
            "thank you for your time", "bye", "goodbye",
        ]
        goodbye_words_hi = [
            "aapka din ho", "acha din ho", "din ho achha",
            "aapka time ke liye thank", "thank you for your time",
            "अच्छा दिन हो", "दिन अच्छा हो", "आपका दिन हो",
            "शुक्रिया, अच्छा दिन", "शुक्रिया,अच्छा दिन", "अलविदा",
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

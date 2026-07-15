import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT

HINDI_INSTRUCTION = (
    "\n\n== ACTIVE LANGUAGE CONTEXT ==\n"
    "The customer is speaking Hindi/Hinglish. "
    "You MUST respond in Hinglish = Hindi words written in Roman/English script ONLY.\n"
    "CORRECT: 'Aapka order confirm ho gaya hai'\n"
    "WRONG: 'Your order is confirmed' (this is English)\n"
    "WRONG: 'आपका ऑर्डर कन्फर्म हो गया है' (this is Devanagari)\n\n"
    "STRICT RULES:\n"
    "- Use ONLY Roman/English letters. NEVER use Devanagari script (हिंदी characters like क, ख, ग).\n"
    "- Do NOT start sentences with English words like 'Great', 'Sure', 'Okay', 'Yes'.\n"
    "- Use Hinglish equivalents: 'Bilkul', 'Achha', 'Theek hai', 'Haan'.\n"
    "- Keep using Hinglish for the ENTIRE conversation until the customer switches to English.\n"
)

ENGLISH_INSTRUCTION = (
    "\n\n== ACTIVE LANGUAGE CONTEXT ==\n"
    "The customer is speaking English. "
    "You MUST respond in English. Do NOT use Hindi or Hinglish words."
)


def _clean_hinglish(text):
    cleaned = re.sub(r'[\u0900-\u097F]+', ' ', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if not cleaned:
        print(f"WARN: _clean_hinglish stripped ALL text from: {text[:100]}")
        return text
    return cleaned


def ask_llm(messages, lang="en"):

    system_content = SYSTEM_PROMPT
    if lang == "hi":
        system_content += HINDI_INSTRUCTION
    else:
        system_content += ENGLISH_INSTRUCTION

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": system_content
            }
        ] + messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 80,
            "num_ctx": 2048
        }
    }

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=30
        )

        response.raise_for_status()

    except Exception as e:

        print("LLM ERROR:", e)

        return (
            "Sorry, I am having trouble "
            "answering right now."
        ), False

    data = response.json()

    answer = (
        data.get("message", {})
            .get("content", "")
            .strip()
    )

    if "</think>" in answer:
        answer = answer.split("</think>")[-1].strip()

    answer = re.sub(
        r"<think>.*?</think>",
        "",
        answer,
        flags=re.S
    ).strip()

    print(f"LLM RAW: {answer}")

    if lang == "hi":
        answer = _clean_hinglish(answer)

    return answer, False

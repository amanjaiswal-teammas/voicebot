import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT

HINDI_INSTRUCTION = (
    "\n\n== ACTIVE LANGUAGE CONTEXT ==\n"
    "The customer is speaking Hindi. "
    "You MUST respond in Hindi using Devanagari script.\n"
    "CORRECT: 'आपका ऑर्डर कन्फर्म हो गया है'\n"
    "WRONG: 'Aapka order confirm ho gaya hai' (this is Hinglish/Roman)\n"
    "WRONG: 'Your order is confirmed' (this is English)\n\n"
    "STRICT RULES:\n"
    "- Use ONLY Devanagari script (हिंदी characters like क, ख, ग).\n"
    "- NEVER use Roman/English letters for Hindi words.\n"
    "- You may keep English product names (Supreme Perfume Box, PhonePe, etc.) in Roman script.\n"
    "- Do NOT start sentences with English words like 'Great', 'Sure', 'Okay'.\n"
    "- Use Hindi equivalents: 'बिल्कुल', 'अच्छा', 'ठीक है', 'हाँ'.\n"
    "- Keep using Hindi for the ENTIRE conversation until the customer switches to English.\n"
)

ENGLISH_INSTRUCTION = (
    "\n\n== ACTIVE LANGUAGE CONTEXT ==\n"
    "The customer is speaking English. "
    "You MUST respond in English. Do NOT use Hindi or Hinglish words."
)



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
            "num_predict": 150,
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

    return answer, False

import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT

HINDI_INSTRUCTION = (
    "\n\n== HINDI RESPONSE MODE ==\n"
    "The customer is speaking Hindi. Reply in Hindi using Devanagari script.\n\n"
    "CORRECT: 'आपका ऑर्डर कन्फर्म हो गया है'\n"
    "WRONG: 'Aapka order confirm ho gaya hai' (Roman)\n"
    "WRONG: 'Your order is confirmed' (English)\n\n"
    "STRICT RULES:\n"
    "- Use ONLY Devanagari script for Hindi words.\n"
    "- Keep product names (Supreme Perfume Box, PhonePe) in English.\n"
    "- Do NOT make up or invent any words. Use only real Hindi words.\n"
    "- Keep responses SHORT: 1-2 sentences max. This is a phone call.\n"
)

ENGLISH_INSTRUCTION = (
    "\n\n== ENGLISH RESPONSE MODE ==\n"
    "The customer is speaking English. "
    "Reply in English only. Keep responses SHORT: 1-2 sentences max."
)

HANGUP_INSTRUCTION = (
    "\n\nIf the customer says goodbye or the conversation is clearly over, "
    "append [HANGUP] at the very end of your response. "
    "Only when the conversation is truly finished."
)



def ask_llm(messages, lang="en"):

    system_content = SYSTEM_PROMPT + HANGUP_INSTRUCTION
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
            "temperature": 0.2,
            "num_predict": 100,
            "num_ctx": 1536,
            "repeat_penalty": 1.2,
            "top_p": 0.8,
            "top_k": 30,
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

    hangup = False
    if "[HANGUP]" in answer:
        hangup = True
        answer = answer.replace("[HANGUP]", "").strip()

    return answer, hangup

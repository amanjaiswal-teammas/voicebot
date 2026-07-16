import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT

HINDI_INSTRUCTION = (
    "\n\n== HINDI RESPONSE MODE ==\n"
    "The customer is speaking Hindi. Reply in Hindi using Devanagari script.\n\n"
    "STRICT RULES:\n"
    "- Use ONLY Devanagari script for Hindi words.\n"
    "- Keep product names (Supreme Perfume Box, PhonePe) in English.\n"
    "- Do NOT make up or invent any words. Use only real Hindi words.\n"
    "- Keep responses SHORT: 1-2 sentences max. This is a phone call.\n"
    "- Do NOT skip conversation steps. Follow the sales flow step by step.\n"
    "- When customer says 'yes/tell me', first explain the product, THEN ask if they want to order.\n"
    "- When customer objects (cheaper elsewhere, not interested), address their concern, don't say goodbye.\n"
    "- NEVER confirm an order without collecting details first.\n"
)

ENGLISH_INSTRUCTION = (
    "\n\n== ENGLISH RESPONSE MODE ==\n"
    "The customer is speaking English. "
    "Reply in English only. Keep responses SHORT: 1-2 sentences max."
)

HANGUP_INSTRUCTION = ""



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
            "temperature": 0.3,
            "num_predict": 130,
            "num_ctx": 2048,
            "repeat_penalty": 1.1,
            "top_p": 0.9,
            "top_k": 40,
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

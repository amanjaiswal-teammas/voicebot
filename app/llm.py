import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT

HINDI_INSTRUCTION = (
    "\n\n== ACTIVE LANGUAGE CONTEXT ==\n"
    "The customer is speaking Hindi/Hinglish. "
    "You MUST respond in Hinglish (Hindi words written in Roman/English script). "
    "Example: 'Aapka order confirm ho gaya hai' — NOT 'Your order is confirmed'. "
    "Do NOT use English sentences. Do NOT use Devanagari script. "
    "Keep using Hinglish for the ENTIRE conversation until the customer switches language."
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
            "num_ctx": 4096
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

    hangup = "[END]" in answer
    answer = answer.replace("[END]", "").strip()

    return answer, hangup

import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT

HINDI_INSTRUCTION = (
    "\n\n== HINDI MODE ==\n"
    "Reply in Hindi using Devanagari script ONLY.\n"
    "No Roman script for Hindi words. Keep product names in English.\n"
    "1-2 sentences max. Phone call, not chat.\n"
    "After customer says yes/tell me → pitch Supreme Perfume Box.\n"
    "After customer says no → ask reason, then goodbye if repeated.\n"
)

ENGLISH_INSTRUCTION = (
    "\n\n== ENGLISH MODE ==\n"
    "Reply in English. 1-2 sentences max. Phone call, not chat.\n"
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
            "num_predict": 80,
            "num_ctx": 1024,
            "repeat_penalty": 1.1,
            "top_p": 0.9,
            "top_k": 20,
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

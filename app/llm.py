import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import SYSTEM_PROMPT as BP_SYSTEM_PROMPT

SYSTEM_PROMPT = BP_SYSTEM_PROMPT


def ask_llm(messages, lang="en"):

    lang_instruction = (
        "Customer language: hi (Hindi/Hinglish). "
        "Respond in Hinglish."
        if lang == "hi"
        else "Customer language: en (English). "
             "Respond in English."
    )

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "system",
                "content": lang_instruction
            }
        ] + messages,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 120,
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
        )

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

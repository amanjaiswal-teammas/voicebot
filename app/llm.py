import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import CALL_SCRIPT, OBJECTIONS, PRODUCT_DESCRIPTIONS, HINGLISH_SCRIPT

SYSTEM_PROMPT = f"""{CALL_SCRIPT}

{OBJECTIONS}

{PRODUCT_DESCRIPTIONS}

{HINGLISH_SCRIPT}

Rules:
- This is an INBOUND call — the customer called you. Start by welcoming them and asking how you can help.
- Follow the script flow naturally (Opening → Identify Need → Offer → Order → Details → Payment → Confirmation → Closing).
- Use Hinglish (mix of Hindi and English) when the customer speaks Hindi/Hinglish. Use English otherwise.
- Use conversation history naturally.
- Give direct answers. Do not explain your reasoning. Do not show thinking.
- Keep responses natural and conversational, not robotic.
- Handle objections using the provided rebuttals.
- Ask for customer details (name, address, payment) step by step.
- Confirm order details before closing."""


def ask_llm(messages, lang="en"):

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ] + messages,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 300,
            "num_ctx": 4096
        }
    }

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=300
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

    return answer

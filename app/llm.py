import requests

from config import OLLAMA_HOST, MODEL_NAME

SYSTEM_PROMPT = """
You are a phone assistant.

Rules:
- Give direct answers.
- Do not explain your reasoning.
- Do not show thinking.
- Keep responses concise.
"""


def ask_llm(messages):

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
            "temperature": 0.3
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

    return data["message"]["content"].strip()
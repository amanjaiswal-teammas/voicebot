import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import (
    SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT_HI,
    SYSTEM_PROMPT_EN,
)

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


def _build_system_prompt(lang):
    system_content = SYSTEM_PROMPT_BASE
    if lang == "hi":
        system_content += SYSTEM_PROMPT_HI
        system_content += HINDI_INSTRUCTION
    else:
        system_content += SYSTEM_PROMPT_EN
        system_content += ENGLISH_INSTRUCTION
    return system_content


def ask_llm(messages, lang="en"):

    system_content = _build_system_prompt(lang)

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
            "num_predict": 100,
            "num_ctx": 2048,
            "repeat_penalty": 1.0,
            "top_p": 0.8,
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


def ask_llm_stream(messages, lang="en"):
    system_content = _build_system_prompt(lang)

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": system_content
            }
        ] + messages,
        "stream": True,
        "options": {
            "temperature": 0.3,
            "num_predict": 100,
            "num_ctx": 2048,
            "repeat_penalty": 1.0,
            "top_p": 0.8,
            "top_k": 20,
        }
    }

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=30,
            stream=True,
        )
        response.raise_for_status()

    except Exception as e:
        print("LLM STREAM ERROR:", e)
        yield ("Sorry, I am having trouble answering right now.", True, False)
        return

    full_answer = ""
    hangup = False
    think_buf = ""
    in_think = False
    yielded_up_to = 0

    for line in response.iter_lines():
        if not line:
            continue

        try:
            import json
            data = json.loads(line)
        except Exception:
            continue

        if data.get("done"):
            break

        msg = data.get("message", {})
        token = msg.get("content", "")

        if not token:
            continue

        full_answer += token

        if "<think>" in token:
            in_think = True
            think_buf += token
            continue

        if in_think:
            think_buf += token
            if "</think>" in token:
                in_think = False
                think_end = think_buf.find("</think>")
                after_think = think_buf[think_end + 8:]
                if after_think.strip():
                    full_answer = after_think.strip()
                else:
                    full_answer = full_answer.replace(think_buf, "").strip()
                think_buf = ""
            continue

        clean = re.sub(r"<think>.*?</think>", "", full_answer, flags=re.S).strip()
        sentences = re.split(r'(?<=[.!?।])\s+', clean)
        if len(sentences) >= 2:
            ready = " ".join(sentences[:-1]).strip()
            if ready and len(ready) > yielded_up_to:
                new_text = ready[yielded_up_to:]
                yielded_up_to = len(ready)
                h = "[HANGUP]" in new_text
                new_text = new_text.replace("[HANGUP]", "").strip()
                if new_text:
                    yield (new_text, False, h)

    full_answer = re.sub(
        r"<think>.*?</think>",
        "",
        full_answer,
        flags=re.S
    ).strip()

    if "[HANGUP]" in full_answer:
        hangup = True
        full_answer = full_answer.replace("[HANGUP]", "").strip()

    remaining = full_answer[yielded_up_to:].strip()
    print(f"LLM RAW: {full_answer}")

    if remaining:
        yield (remaining, False, hangup)
    yield (full_answer, True, hangup)

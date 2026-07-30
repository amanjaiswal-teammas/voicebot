import requests
import re

from .config import OLLAMA_HOST, MODEL_NAME
from .bellavita_prompt import (
    SYSTEM_PROMPT_BASE as SALES_BASE,
    SYSTEM_PROMPT_HI as SALES_HI,
    SYSTEM_PROMPT_EN as SALES_EN,
)
from .support_prompt import (
    SYSTEM_PROMPT_BASE as SUPPORT_BASE,
    SYSTEM_PROMPT_HI as SUPPORT_HI,
    SYSTEM_PROMPT_EN as SUPPORT_EN,
)

SALES_HINDI_INSTRUCTION = (
    "\n\n== HINDI RESPONSE MODE ==\n"
    "The customer is speaking Hindi. Reply in Hindi using Devanagari script.\n\n"
    "STRICT RULES:\n"
    "- Use ONLY Devanagari script for Hindi words.\n"
    "- Keep product names (Supreme Perfume Box, PhonePe) in English.\n"
    "- Do NOT make up or invent any words or numbers. Use only what is in the PRODUCTS section.\n"
    "- Do NOT invent product features. The product has exactly 4 perfumes, that's it.\n"
    "- Keep responses SHORT: 1-2 sentences max. This is a phone call.\n"
    "- Do NOT skip conversation steps. Follow the sales flow step by step.\n"
    "- When customer says 'yes/tell me', first explain the product, THEN ask if they want to order.\n"
    "- When customer objects (cheaper elsewhere, not interested), address their concern, don't say goodbye.\n"
    "- NEVER confirm an order without collecting details first.\n"
)

SALES_ENGLISH_INSTRUCTION = (
    "\n\n== ENGLISH RESPONSE MODE ==\n"
    "The customer is speaking English. "
    "Reply in English only. Keep responses SHORT: 1-2 sentences max.\n"
    "Do NOT invent product features. The product has exactly 4 perfumes, that's it.\n"
)

SUPPORT_SHORT_INSTRUCTION = (
    "\n\nKeep responses SHORT: 1-2 sentences max. This is a phone call.\n"
    "Speak in the customer's language. Stay calm and empathetic."
)


def _build_system_prompt(lang, mode="sales"):
    if mode == "support":
        system_content = SUPPORT_BASE
        if lang == "hi":
            system_content += SUPPORT_HI
        else:
            system_content += SUPPORT_EN
        system_content += SUPPORT_SHORT_INSTRUCTION
    else:
        system_content = SALES_BASE
        if lang == "hi":
            system_content += SALES_HI
            system_content += SALES_HINDI_INSTRUCTION
        else:
            system_content += SALES_EN
            system_content += SALES_ENGLISH_INSTRUCTION
    return system_content


def ask_llm(messages, lang="en", mode="sales"):

    system_content = _build_system_prompt(lang, mode)

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


def ask_llm_stream(messages, lang="en", mode="sales"):
    system_content = _build_system_prompt(lang, mode)

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
            "temperature": 0.2,
            "num_predict": 100,
            "num_ctx": 1536,
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
    in_think = False
    yielded_up_to = 0

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue

        try:
            data = json.loads(line)
        except Exception:
            continue

        if data.get("done"):
            break

        msg = data.get("message", {})
        token = msg.get("content", "")

        if not token:
            continue

        # ---- Think block handling ----
        if "<think>" in token:
            in_think = True
            before, _, after = token.partition("<think>")
            full_answer += before
            continue

        if in_think:
            if "</think>" in token:
                in_think = False
                _, _, after = token.partition("</think>")
                full_answer += after
                yielded_up_to = min(yielded_up_to, len(full_answer))
            continue

        full_answer += token

        # ---- Sentence detection & yielding ----
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

    # Clean any unclosed think blocks
    if "<think>" in full_answer:
        full_answer = full_answer.split("<think>")[0].strip()

    if "[HANGUP]" in full_answer:
        hangup = True
        full_answer = full_answer.replace("[HANGUP]", "").strip()

    remaining = full_answer[yielded_up_to:].strip()
    print(f"LLM RAW: {full_answer}")

    if remaining:
        yield (remaining, False, hangup)
    yield (full_answer, True, hangup)

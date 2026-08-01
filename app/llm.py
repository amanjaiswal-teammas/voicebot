import requests
import re
import json

from .config import OLLAMA_HOST, MODEL_NAME


def _build_system_prompt(lang, mode="sales"):
    from .agents import get_system_prompt
    return get_system_prompt(mode, lang)


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
        "messages": [{"role": "system", "content": system_content}] + messages,
        "stream": True,
        "options": {
            "temperature": 0.2, "num_predict": 100, "num_ctx": 1536,
            "repeat_penalty": 1.0, "top_p": 0.8, "top_k": 20,
        }
    }
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=30, stream=True)
        response.raise_for_status()
    except Exception as e:
        print("LLM STREAM ERROR:", e)
        yield ("Sorry, I am having trouble answering right now.", True, False)
        return
    full_answer = ""
    hangup = False
    in_think = False
    for line in response.iter_lines(decode_unicode=True):
        if not line: continue
        try: data = json.loads(line)
        except: continue
        if data.get("done"): break
        msg = data.get("message", {})
        token = msg.get("content", "")
        if not token: continue
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
            continue
        full_answer += token
    full_answer = re.sub(r"<think>.*?</think>", "", full_answer, flags=re.S).strip()
    if "<think>" in full_answer:
        full_answer = full_answer.split("<think>")[0].strip()
    if "[HANGUP]" in full_answer:
        hangup = True
        full_answer = full_answer.replace("[HANGUP]", "").strip()
    print(f"LLM RAW: {full_answer}")
    yield (full_answer, True, hangup)

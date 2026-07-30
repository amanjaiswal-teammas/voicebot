"""
Inbound call simulation — stress-tests the bot against a difficult customer persona.

Usage:
    python -m app.test_inbound [--turns 10] [--api http://127.0.0.1:8000]

Requires the FastAPI server to be running (uvicorn app.voice_api:app).
"""

import argparse
import json
import os
import sys
import requests
import base64
import io
import wave
import audioop

API_BASE = "http://127.0.0.1:8000"
AUDIO_DIR = "audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

CUSTOMER_PERSONA = """You are a real Indian customer contacting Bellavita customer support via call or chat.
You are NOT an agent.
You are NOT polite.
You are NOT cooperative.
You are NOT calm for long.
You must behave only as a customer who is emotional, irritated, confused, demanding, and unpredictable.
You must never break character, under any circumstance.

PERSONALITY & EMOTIONAL BEHAVIOR
You are a difficult customer whose emotions change constantly and naturally.
You may switch emotions:
- Between messages
- Mid-sentence
- Without warning

Your emotional states include:
- Irritated
- Angry
- Frustrated
- Accusatory
- Confused
- Doubtful
- Passive-aggressive
- Escalating
- Briefly calm (only for 1-2 turns)

If the agent repeats information, delays, or sounds scripted — you escalate again.

BEHAVIOR RULES (MANDATORY)
You must:
- Interrupt the agent
- Repeat complaints in different words
- Jump between multiple issues without resolving one
- Question the agent's competence
- Accuse Bellavita or the courier partner
- Say the courier is lying or never contacted you
- Demand refund, compensation, or voucher
- Ask for a senior or escalation
- Threaten social media complaints
- Say things like "I'll never order again"
- Refuse instructions initially
- Reluctantly agree later only if pressured
- Behave inconsistently like a real human
- You are never fully satisfied.

CONTEXT, FLOW & MULTI-TOPIC HANDLING (MANDATORY)
The bot must not stay stuck on a single question or issue.
Once the current query is addressed or the agent asks "Anything else?", move to a new concern naturally.

LANGUAGE & TONE
- Use simple Indian English
- Sound emotional, impatient, blunt, or sarcastic
- Avoid professional or polished language

ISSUES YOU CAN COMPLAIN ABOUT (switch between these randomly):
- Delivery delay, "customer not available" falsely updated, no call from delivery agent
- Courier partner lying, order returned without delivery (RTO)
- Wrong product delivered, missing item, damaged product, bad smell
- Refund delayed, partial refund, payment successful but order not created
- App issues: unable to place order, coupon not working, payment failed
- Demand compensation or voucher, ask for a senior, threaten social media

RESPONSE STYLE
- Short to medium responses
- Impatient and emotional
- Ask counter-questions instead of answering clearly
- Sometimes ignore what the agent just said
- Escalate if resolution is slow or unclear

FORBIDDEN ACTIONS (ABSOLUTE)
- Do NOT offer solutions
- Do NOT guide the agent
- Do NOT explain Bellavita's internal processes
- Do NOT behave like customer support
- Do NOT sound calm or helpful
- Do NOT mention AI, bot, system, SOP, or training
- Do NOT close the conversation peacefully on your own

GOAL
Your goal is to:
- Simulate a real, messy, emotional Bellavita customer
- Stress-test agent patience and process
- Force escalation handling
- Create realistic chaos
- Behave like a customer who is never easy to handle
- You are not here to help the agent.
- You are here to behave like a real upset customer."""

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:7b"


def generate_customer_utterance(history):
    """Generate the next customer utterance using the persona."""
    messages = [{"role": "system", "content": CUSTOMER_PERSONA}]
    messages.extend(history)

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.9, "num_predict": 80, "num_ctx": 2048},
    }

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=30)
        r.raise_for_status()
        text = r.json()["message"]["content"].strip()
        # Clean any think tags
        if "</think>" in text:
            text = text.split("</think>")[-1].strip()
        elif "<think>" in text:
            text = text.split("<think>")[0].strip()
        return text
    except Exception as e:
        print(f"  [CUSTOMER LLM ERROR] {e}")
        return "Hello? Hello? Are you there?"


def text_to_wav(text, output_path, lang="en"):
    """Generate a WAV file from text using the bot's own TTS engine."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from supertonic_engine import speak
    try:
        speak(text, output_path, lang)
        return True
    except Exception as e:
        print(f"  [TTS ERROR] {e}")
        return False


def send_to_bot(call_id, audio_path, interrupted_text=None):
    """Send audio to the bot API exactly as voicebot.py does."""
    with open(audio_path, "rb") as f:
        data = {"call_id": call_id}
        if interrupted_text:
            data["interrupted_text"] = interrupted_text
        r = requests.post(
            f"{API_BASE}/voice-audio-segmented",
            files={"audio": f},
            data=data,
            timeout=120,
        )
    if r.status_code != 200:
        print(f"  [API ERROR] {r.status_code}: {r.text}")
        return None
    return r.json()


def get_greeting(call_id, lang="en"):
    """Get the initial greeting from the bot."""
    r = requests.post(
        f"{API_BASE}/voice-audio-segmented",
        data={"call_id": call_id, "outbound": "true", "lang": lang},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"  [GREETING ERROR] {r.status_code}: {r.text}")
        return None
    return r.json()


def decode_segment_audio(audio_b64):
    """Decode base64 ulaw audio back to PCM WAV bytes (for silence detection)."""
    ulaw_bytes = base64.b64decode(audio_b64)
    pcm = audioop.ulaw2lin(ulaw_bytes, 2)
    return pcm


def simulate_conversation(turns=10, lang="en"):
    print("=" * 60)
    print("INBOUND CALL SIMULATION")
    print(f"Model: {MODEL}")
    print(f"API: {API_BASE}")
    print(f"Max turns: {turns}")
    print("=" * 60)

    call_id = None
    history = []  # For customer persona LLM
    turn = 0
    bot_text = ""

    while turn < turns:
        turn += 1
        print(f"\n{'─' * 50}")
        print(f"TURN {turn}")

        # Step 1: Get greeting on first turn, otherwise get the bot response
        if turn == 1:
            print("\n  [BOT GREETING]")
            response = get_greeting(call_id or "", lang)
            if not response:
                print("  Failed to get greeting")
                break
            call_id = response.get("call_id", call_id)
        else:
            # Generate customer audio and send to bot
            customer_text = generate_customer_utterance(history)
            print(f"\n  [CUSTOMER]: {customer_text}")

            # Add to history for context tracking
            history.append({"role": "user", "content": customer_text})

            # Generate audio from customer text
            audio_path = f"{AUDIO_DIR}/sim_{call_id}_turn_{turn}.wav"
            if not text_to_wav(customer_text, audio_path, lang):
                print("  Skipping turn due to TTS error")
                continue

            # Send to bot
            response = send_to_bot(call_id, audio_path)
            os.remove(audio_path)

            if response is None:
                print("  Bot API failed, ending call")
                break

            if response.get("hangup"):
                print("\n  [BOT HUNG UP]")
                break

        # Extract bot response text
        segments = response.get("segments", [])
        bot_text = " ".join(s["text"] for s in segments if s.get("text"))
        call_id = response.get("call_id", call_id)

        if not bot_text.strip():
            print("  [BOT]: (silent)")
            continue

        print(f"\n  [BOT]: {bot_text}")

        # Add bot response to history
        history.append({"role": "assistant", "content": bot_text})

        # Check if customer wants to hangup (detect goodbye in latest response)
        if response.get("hangup"):
            print("\n  [CALL ENDED - bot signaled hangup]")
            break

    print(f"\n{'=' * 60}")
    print(f"CALL COMPLETE ({turn} turns)")
    print(f"Call ID: {call_id}")
    print("=" * 60)

    # Print full transcript
    print("\n\nFULL TRANSCRIPT:")
    print("=" * 60)
    for i, msg in enumerate(history):
        role = "CUSTOMER" if msg["role"] == "user" else "BOT"
        print(f"\n[{role}]: {msg['content']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate inbound calls with difficult customer persona")
    parser.add_argument("--turns", type=int, default=10, help="Max conversation turns")
    parser.add_argument("--api", default=API_BASE, help="FastAPI server URL")
    parser.add_argument("--lang", default="en", choices=["en", "hi"], help="Language")
    args = parser.parse_args()

    API_BASE = args.api
    simulate_conversation(turns=args.turns, lang=args.lang)

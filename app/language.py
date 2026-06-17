import re

HINGLISH_WORDS = {
    "hai",
    "nahi",
    "mera",
    "mujhe",
    "tum",
    "aap",
    "kaise",
    "kya",
    "kyun",
    "haan",
    "acha",
    "theek",
    "batao",
    "karna",
    "karo",
    "hoga",
    "hoon",
    "mein"
}


def detect_language(text):

    if re.search(r"[\u0900-\u097F]", text):
        return "hi"

    words = text.lower().split()

    matches = sum(
        1 for w in words
        if w in HINGLISH_WORDS
    )

    if matches >= 2:
        return "hi"

    return "en"

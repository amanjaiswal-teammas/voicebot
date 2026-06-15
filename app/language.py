# language.py

import re


def detect_language(text):

    hindi = re.search(
        r"[\u0900-\u097F]",
        text
    )

    if hindi:
        return "hi"

    return "en"
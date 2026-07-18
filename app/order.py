"""Order detail extraction from customer utterances.

Pure-regex extraction — no LLM needed. Keeps the phone call fast.
"""

import re

from .patterns import ORDER_COLLECT_HI, ORDER_COLLECT_EN

# Spoken-word → digit mapping (English)
_WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "double one": "11", "double two": "22", "double three": "33",
    "double four": "44", "double five": "55", "double six": "66",
    "double seven": "77", "double eight": "88", "double nine": "99",
    "duble one": "11", "duble two": "22", "duble three": "33",
    "duble four": "44", "duble five": "55", "duble six": "66",
    "duble seven": "77", "duble eight": "88", "duble nine": "99",
}

ALL_FIELDS = ("name", "phone", "email", "address", "pincode")


def extract_order_details(text: str, existing: dict) -> dict:
    """Extract order fields from *text*, merging into *existing* dict."""
    details = dict(existing)
    text_lower = text.lower().strip()

    # ---- phone ----
    if not details.get("phone"):
        phone = _extract_phone(text_lower)
        if phone:
            details["phone"] = phone

    # ---- email ----
    if not details.get("email"):
        email = _extract_email(text_lower)
        if email:
            details["email"] = email

    # ---- pincode ----
    if not details.get("pincode"):
        m = re.search(r'\b(\d{6})\b', text)
        if m:
            details["pincode"] = m.group(1)

    # ---- name ----
    if not details.get("name"):
        name = _extract_field(
            text,
            labels=("name", "my name", "नाम", "मेरा नाम", "नामं"),
            separators=("and", "phone", "email", "address", "pincode",
                         "पता", "फोन", "फ़ोन", "ईमेल", "नाम", "पिनकोड"),
        )
        if name:
            details["name"] = name

    # ---- address ----
    if not details.get("address"):
        addr = _extract_field(
            text,
            labels=("address", "my address", "पता"),
            separators=("and", "phone", "email", "name", "pincode",
                         "फोन", "फ़ोन", "ईमेल", "नाम", "पिनकोड"),
        )
        if addr:
            if details.get("pincode") and details["pincode"] in addr:
                addr = addr.replace(details["pincode"], "").strip().rstrip(',- ')
            details["address"] = addr

    # Fallback: address from context around pincode
    if not details.get("address") and details.get("pincode"):
        words = text.split()
        for i, w in enumerate(words):
            if re.match(r'\d{6}', w):
                addr_words = words[max(0, i - 4):i]
                if addr_words:
                    details["address"] = " ".join(addr_words)
                break

    return details


def order_needs_more(details: dict) -> bool:
    """True if any required field is still missing."""
    return not all(details.get(k) for k in ALL_FIELDS)


def build_order_response(lang: str, details: dict) -> str:
    """Build follow-up asking for missing fields."""
    collected = []
    if details.get("name"):
        collected.append(details["name"])
    if details.get("phone"):
        collected.append(details["phone"])
    if details.get("email"):
        collected.append(details["email"])

    missing = []
    if not details.get("name"):
        missing.append("नाम" if lang == "hi" else "name")
    if not details.get("phone"):
        missing.append("फ़ोन नंबर" if lang == "hi" else "phone number")
    if not details.get("email"):
        missing.append("ईमेल" if lang == "hi" else "email")
    if not details.get("address") or not details.get("pincode"):
        missing.append("पता (पिनकोड सहित)" if lang == "hi" else "address with pincode")

    if not missing:
        return None

    if lang == "hi":
        if collected:
            return f"बहुत अच्छा! आपने {'/'.join(collected)} बताया। कृपया {' '.join(missing)} बता दीजिए।"
        return ORDER_COLLECT_HI
    else:
        if collected:
            return f"Got it! You shared {'/'.join(collected)}. Please provide {' '.join(missing)}."
        return ORDER_COLLECT_EN


def build_order_confirmation(lang: str, details: dict) -> str:
    """Build final confirmation once all fields are present."""
    if lang == "hi":
        return (
            f"बहुत अच्छा! आपका ऑर्डर कन्फ़र्म हो गया है। "
            f"नाम {details.get('name', '')}, फ़ोन {details.get('phone', '')}, "
            f"ईमेल {details.get('email', '')}, पता {details.get('address', '')} {details.get('pincode', '')}। "
            f"Supreme Perfume Box — Rs 1,599। शुक्रिया!"
        )
    else:
        return (
            f"Your order is confirmed! "
            f"Name: {details.get('name', '')}, Phone: {details.get('phone', '')}, "
            f"Email: {details.get('email', '')}, Address: {details.get('address', '')} {details.get('pincode', '')}. "
            f"Supreme Perfume Box — Rs 1,599. Thank you!"
        )


# ---- internal helpers ----------------------------------------------------------

def _extract_phone(text_lower: str):
    """Try multiple phone patterns: 10-digit, 5+5, 7-9 digit, spoken words."""
    m = re.search(r'(\d{10})', text_lower)
    if not m:
        m = re.search(r'(\d{5}\s?\d{5})', text_lower)
    if not m:
        m = re.search(r'(\d{7,9})', text_lower)

    if m:
        return m.group(1).replace(" ", "")

    # Spoken-word fallback
    digits_only = text_lower
    for word, digit in sorted(_WORD_TO_DIGIT.items(), key=lambda x: -len(x[0])):
        digits_only = digits_only.replace(word, digit)
    digits_only = re.sub(r'[^0-9]', '', digits_only)
    if len(digits_only) >= 7:
        return digits_only
    return None


def _extract_email(text_lower: str):
    """Standard email or 'word at word dot com' format."""
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w+', text_lower)
    if m:
        return m.group(0)
    m = re.search(r'([\w.+-]+)\s+at\s+([\w.+-]+)\s*\.\s*(com|in|org)', text_lower)
    if m:
        return f"{m.group(1)}@{m.group(2)}.{m.group(3)}"
    return None


def _extract_field(text: str, labels: tuple, separators: tuple):
    """Generic labelled field extractor (e.g. 'name is X and ...')."""
    label_pat = "|".join(re.escape(l) for l in labels)
    sep_pat = "|".join(re.escape(s) for s in separators)
    m = re.search(
        rf'(?:{label_pat})\s+(?:is\s+|है\s+)?(.+?)(?:\s+(?:{sep_pat})|\s*$)',
        text, re.I,
    )
    if m:
        return m.group(1).strip().rstrip('.,।')
    return None

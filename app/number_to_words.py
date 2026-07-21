"""Convert integers to spoken words in English and Hindi."""

# ---------------------------------------------------------------------------
# English
# ---------------------------------------------------------------------------

_ONES_EN = [
    "", "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]

_TENS_EN = [
    "", "", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety",
]


def _under_thousand_en(n):
    """Convert 0-999 to English words."""
    if n == 0:
        return ""
    parts = []
    if n >= 100:
        parts.append(_ONES_EN[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        tens = _TENS_EN[n // 10]
        ones = n % 10
        parts.append(tens + ("-" + _ONES_EN[ones] if ones else ""))
    elif n > 0:
        parts.append(_ONES_EN[n])
    return " ".join(parts)


def int_to_words_en(n):
    """Convert an integer to English words.

    Handles up to 99,99,99,999 (ninety-nine crore ninety-nine lakh ...).
    Uses Indian numbering for large numbers so it reads naturally on Indian
    telephony.
    """
    if n == 0:
        return "zero"

    negative = n < 0
    n = abs(n)

    # Indian scale: crore, lakh, thousand, hundred
    scales = [
        (10000000, "crore"),
        (100000, "lakh"),
        (1000, "thousand"),
    ]

    parts = []
    for divisor, name in scales:
        if n >= divisor:
            chunk = n // divisor
            parts.append(_under_thousand_en(chunk) + " " + name)
            n %= divisor

    if n > 0:
        parts.append(_under_thousand_en(n))

    result = " ".join(parts)
    if negative:
        result = "minus " + result
    return result


# ---------------------------------------------------------------------------
# Hindi
# ---------------------------------------------------------------------------

_ONES_HI = [
    "", "एक", "दो", "तीन", "चार", "पाँच",
    "छह", "सात", "आठ", "नौ", "दस",
    "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह",
    "सोलह", "सत्रह", "अठारह", "उन्नीस",
]

_TENS_HI = [
    "", "", "बीस", "तीस", "चालीस", "पचास",
    "साठ", "सत्तर", "अस्सी", "नब्बे",
]


def _under_thousand_hi(n):
    """Convert 0-999 to Hindi words."""
    if n == 0:
        return ""
    parts = []
    if n >= 100:
        hundred = n // 100
        if hundred == 1:
            parts.append("सौ")
        else:
            parts.append(_ONES_HI[hundred] + " सौ")
        n %= 100
    if n >= 20:
        tens = _TENS_HI[n // 10]
        ones = n % 10
        if ones:
            parts.append(_ONES_HI[ones] + " " + tens)
        else:
            parts.append(tens)
    elif n > 0:
        parts.append(_ONES_HI[n])
    return " ".join(parts)


def int_to_words_hi(n):
    """Convert an integer to Hindi words."""
    if n == 0:
        return "शून्य"

    negative = n < 0
    n = abs(n)

    scales = [
        (10000000, "करोड़"),
        (100000, "लाख"),
        (1000, "हज़ार"),
    ]

    parts = []
    for divisor, name in scales:
        if n >= divisor:
            chunk = n // divisor
            parts.append(_under_thousand_hi(chunk) + " " + name)
            n %= divisor

    if n > 0:
        parts.append(_under_thousand_hi(n))

    result = " ".join(parts)
    if negative:
        result = "ऋण " + result
    return result

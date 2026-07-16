import re

HINGLISH_WORDS = {
    # Pronouns
    "mai", "mein", "mujhe", "mera", "meri", "mere", "tum", "tumhe",
    "tumhara", "tumhari", "aap", "aapko", "aapka", "aapki", "ye",
    "wo", "yeh", "woh", "kya", "kaun", "maine", "tune", "usne",
    # Common verbs / auxiliaries
    "hai", "ho", "hain", "tha", "thi", "the", "hoga", "hogi", "hoge",
    "karna", "karo", "kare", "karta", "karti", "batao", "bolo", "suno",
    "chahiye", "lagega", "milega", "de", "do", "le", "lo", "ja", "aa",
    "gaya", "gai", "gaye", "raha", "rahi", "rahe", "dunga", "dungi",
    "karunga", "karungi", "bataunga", "bataungi", "samajh", "samjhe",
    "samjh", "bol", "bolna", "sunna", "dekhna", "dekh", "lenge",
    "karenge", "pasand", "chahta", "chahti", "chahunga", "chahungi",
    # Question words
    "kaise", "kaisa", "kaisi", "kyun", "kyu", "kab", "kidhar", "kahan",
    "kitna", "kitne", "kitni", "kaunsa", "kaunsi", "kaunse",
    # Negation
    "nahi", "na", "mat", "matlab",
    # Common affirmatives / responses
    "haan", "bilkul", "acha", "accha", "theek", "sahi", "ji",
    "zaroor",
    # Relationship / address terms
    "bhai", "yaar", "didi", "aunty", "uncle", "mem",
    # Particles / postpositions / connectors
    "ka", "ki", "ke", "ko", "se", "par", "pe", "ne",
    "aur", "ya", "toh", "phir", "abhi", "wahi", "yahin", "lekin",
    "magar", "bas", "sirf", "bhi",
}

HINGLISH_PHRASES = {
    "nahi chahiye",
    "koi baat",
    "bata dijiye",
    "ho jayega",
    "order lena",
    "order place",
    "payment mode",
    "samajh sakti",
    "samajh gaya",
    "samajh gayi",
    "sunna chahenge",
    "sunna chahta",
    "sunna chahti",
    "karna chahenge",
    "karna chahta",
    "karna chahiye",
    "lena chahenge",
    "lena chahta",
    "bataiye ga",
    "boliye ga",
    "order confirm",
    "order cancel",
    "discount milega",
    "delivery charge",
    "nahi karenge",
    "nahi hai",
    "nahi karna",
    "bilkul nahi",
    "ji haan",
    "samajh sakti hoon",
    "bol rahi hoon",
    "baat kar",
    "kaunsa product",
    "kaunsi product",
    "kya aap",
    "kya hai",
    "kaise hain",
    "acha din",
    "din ho",
    "time ke liye",
    "kyun nahi",
    "kyu nahi",
}


def detect_language(text):

    if re.search(r"[\u0900-\u097F]", text):
        return "hi"

    text_lower = text.lower()

    for phrase in HINGLISH_PHRASES:
        if phrase in text_lower:
            return "hi"

    words = text_lower.split()

    matches = sum(
        1 for w in words
        if w in HINGLISH_WORDS
    )

    if matches >= 1:
        return "hi"

    return "en"


SWITCH_TO_EN = re.compile(
    r"(english|अंग्रेज़ी|अंग्रेजी|angrezi|inglish)",
    re.IGNORECASE,
)

SWITCH_TO_HI = re.compile(
    r"(hindi|हिंदी|hindī)",
    re.IGNORECASE,
)


def detect_language_switch(text):
    text_lower = text.lower()
    if SWITCH_TO_EN.search(text_lower):
        return "en"
    if SWITCH_TO_HI.search(text_lower):
        return "hi"
    return None

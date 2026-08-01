from . import db
from .bellavita_prompt import (
    SYSTEM_PROMPT_BASE as SALES_BASE,
    SYSTEM_PROMPT_HI as SALES_HI,
    SYSTEM_PROMPT_EN as SALES_EN,
    SALES_HINDI_INSTRUCTION,
    SALES_ENGLISH_INSTRUCTION,
)
from .support_prompt import (
    SYSTEM_PROMPT_BASE as SUPPORT_BASE,
    SYSTEM_PROMPT_EN as SUPPORT_EN,
    SUPPORT_SHORT_INSTRUCTION,
)

SUPPORTED_TYPES = ("sales", "support")


def _builtin_system_prompt(agent_type, lang):
    if agent_type == "support":
        return SUPPORT_BASE + SUPPORT_EN + SUPPORT_SHORT_INSTRUCTION
    content = SALES_BASE
    if lang == "hi":
        content += SALES_HI + SALES_HINDI_INSTRUCTION
    else:
        content += SALES_EN + SALES_ENGLISH_INSTRUCTION
    return content


def get_system_prompt(agent_type, lang):
    if agent_type not in SUPPORTED_TYPES:
        agent_type = "sales"
    prompt = db.get_system_prompt(agent_type, lang)
    if prompt:
        return prompt
    return _builtin_system_prompt(agent_type, lang)


def get_agent(agent_type, lang):
    return db.get_agent(agent_type, lang) or {}


def list_agents():
    return db.list_agents()

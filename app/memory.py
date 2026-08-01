from . import db
from .session import get_or_create_session
from .session_store import flush_session


def get_history(call_id):
    session = get_or_create_session(call_id)
    messages = session.setdefault("messages", [])
    if not messages and db.available():
        try:
            rows = db.get_history(call_id, limit=20)
            messages.extend({"role": r["role"], "content": r["content"]} for r in rows)
        except Exception as e:
            print(f"MEMORY: load error {e}")
    return messages


def add_message(call_id, role, content):
    session = get_or_create_session(call_id)
    session["messages"].append({
        "role": role,
        "content": content
    })

    # keep last 20 messages
    if len(session["messages"]) > 20:
        session["messages"] = session["messages"][-20:]

    if db.available():
        try:
            db.insert_message(call_id, role, content)
            flush_session(call_id)
        except Exception as e:
            print(f"MEMORY: persist error {e}")

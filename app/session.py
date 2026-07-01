import uuid
from .session_store import sessions

# sessions = {}


def create_session():
    call_id = str(uuid.uuid4())

    sessions[call_id] = {
        "active": True,
        "messages": []
    }

    return call_id


def end_session(call_id):
    session = sessions.get(call_id)
    if session:
        session["active"] = False
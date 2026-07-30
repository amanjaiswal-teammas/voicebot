import uuid
import time
from .session_store import sessions

SESSION_TTL = 1800  # 30 minutes


def create_session():
    call_id = str(uuid.uuid4())

    sessions[call_id] = {
        "active": True,
        "messages": [],
        "no_count": 0,
        "_created_at": time.time(),
    }

    return call_id


def end_session(call_id):
    session = sessions.get(call_id)
    if session:
        session["active"] = False


def cleanup_expired():
    now = time.time()
    expired = [
        cid for cid, s in list(sessions.items())
        if s.get("_created_at", 0) + SESSION_TTL < now
    ]
    for cid in expired:
        sessions.pop(cid, None)
    if expired:
        print(f"CLEANUP: removed {len(expired)} expired sessions")
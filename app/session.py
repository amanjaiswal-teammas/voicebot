import uuid
import time
from .session_store import sessions, sessions_lock

SESSION_TTL = 1800  # 30 minutes idle


def get_or_create_session(call_id):
    now = time.time()
    with sessions_lock:
        session = sessions.get(call_id)
        if session is None:
            session = {
                "active": True,
                "messages": [],
                "no_count": 0,
                "_created_at": now,
            }
            sessions[call_id] = session
        session["_last_active"] = now
        return session


def create_session():
    call_id = str(uuid.uuid4())
    now = time.time()
    with sessions_lock:
        sessions[call_id] = {
            "active": True,
            "messages": [],
            "no_count": 0,
            "_created_at": now,
            "_last_active": now,
        }
    return call_id


def end_session(call_id):
    with sessions_lock:
        session = sessions.get(call_id)
        if session:
            session["active"] = False


def cleanup_expired():
    now = time.time()
    with sessions_lock:
        expired = [
            cid for cid, s in list(sessions.items())
            if s.get("_last_active", s.get("_created_at", 0)) + SESSION_TTL < now
        ]
        for cid in expired:
            sessions.pop(cid, None)
    if expired:
        print(f"CLEANUP: removed {len(expired)} expired sessions")

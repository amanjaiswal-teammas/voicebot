import uuid
import time

from . import db
from .config import SESSION_TTL
from .session_store import sessions, sessions_lock
from .session_store import (
    start_conversation as _start_conversation,
    get_or_create_session as _get_or_create_session,
    flush_session,
    end_conversation as _end_conversation,
)


def start_conversation(call_id, agent_type="sales", direction="outbound", lang="en"):
    return _start_conversation(call_id, agent_type, direction, lang)


def get_or_create_session(call_id, agent_type=None, direction=None, lang=None):
    return _get_or_create_session(call_id, agent_type, direction, lang)


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
            "_conv_id": None,
        }
    return call_id


def end_session(call_id):
    with sessions_lock:
        session = sessions.get(call_id)
        if session:
            session["active"] = False


def end_conversation(call_id, status="hangup", outcome=None):
    _end_conversation(call_id, status)
    if db.available():
        try:
            db.log_event(call_id, "conversation_ended", status)
        except Exception:
            pass


def cleanup_expired():
    now = time.time()
    with sessions_lock:
        expired = [
            cid for cid, s in list(sessions.items())
            if s.get("_last_active", s.get("_created_at", 0)) + SESSION_TTL < now
        ]
        for cid in expired:
            sessions.pop(cid, None)
    for cid in expired:
        if db.available():
            try:
                db.end_conversation(cid, "ended")
                db.log_event(cid, "conversation_ended", "timeout")
            except Exception:
                pass
    if expired:
        print(f"CLEANUP: removed {len(expired)} expired sessions")

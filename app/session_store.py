import threading
import time
import json

from . import db

sessions = {}
sessions_lock = threading.RLock()

_TRANSITORY = {"messages", "_created_at", "_last_active", "_conv_id"}


def _new_session():
    return {
        "active": True,
        "messages": [],
        "no_count": 0,
        "_created_at": time.time(),
    }


def _state_blob(session):
    return json.dumps(
        {k: v for k, v in session.items() if k not in _TRANSITORY},
        ensure_ascii=False,
    )


def _restore_session(blob, conv_id):
    session = _new_session()
    try:
        data = json.loads(blob)
        if isinstance(data, dict):
            session.update(data)
    except Exception:
        pass
    session["_conv_id"] = conv_id
    return session


def _load_messages(call_id):
    try:
        rows = db.get_history(call_id, limit=20)
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception:
        return []


def start_conversation(call_id, agent_type="sales", direction="outbound", lang="en"):
    now = time.time()
    with sessions_lock:
        session = sessions.get(call_id)
        if session is None:
            session = _new_session()
            sessions[call_id] = session
        session.setdefault("_conv_id", None)
        session["_last_active"] = now
    if db.available() and not session["_conv_id"]:
        db.defer(lambda cid=call_id, a=agent_type, d=direction, l=lang:
                 _persist_start(cid, a, d, l))
    return session


def _persist_start(call_id, agent_type, direction, lang):
    try:
        conv_id = db.start_conversation(call_id, agent_type, direction, lang)
        with sessions_lock:
            s = sessions.get(call_id)
            if s is not None and s.get("_conv_id") is None:
                s["_conv_id"] = conv_id
    except Exception as e:
        print(f"SESSION: start_conversation error {e}")


def get_or_create_session(call_id, agent_type=None, direction=None, lang=None):
    now = time.time()
    with sessions_lock:
        session = sessions.get(call_id)
        if session is None:
            session = _new_session()
            if db.available():
                try:
                    row = db.get_conversation_state(call_id)
                    if row:
                        session = _restore_session(
                            row.get("state_value"), row.get("conversation_id")
                        )
                        session["messages"] = _load_messages(call_id)
                    else:
                        session["_conv_id"] = db.start_conversation(
                            call_id,
                            agent_type or "sales",
                            direction or "outbound",
                            lang or "en",
                        )
                except Exception as e:
                    print(f"SESSION: load error {e}")
                    session["_conv_id"] = None
            sessions[call_id] = session
        session["_last_active"] = now
        return session


def get_session(call_id):
    with sessions_lock:
        return sessions.get(call_id)


def flush_session(call_id):
    if not db.available():
        return
    with sessions_lock:
        session = sessions.get(call_id)
        if session is None:
            return
        blob = _state_blob(session)
    try:
        db.upsert_session_state(call_id, blob)
    except Exception as e:
        print(f"SESSION: flush error {e}")


def end_conversation(call_id, status="hangup"):
    if not db.available():
        return
    try:
        db.end_conversation(call_id, status)
    except Exception as e:
        print(f"SESSION: end error {e}")
    flush_session(call_id)


def clear(call_id):
    with sessions_lock:
        sessions.pop(call_id, None)

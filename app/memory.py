from .session_store import sessions

# sessions = {}


def get_history(call_id):

    if call_id not in sessions:
        sessions[call_id] = []

    return sessions[call_id]


def add_message(call_id, role, content):

    history = get_history(call_id)

    history.append({
        "role": role,
        "content": content
    })

    # keep last 20 messages
    if len(history) > 20:
        sessions[call_id] = history[-20:]
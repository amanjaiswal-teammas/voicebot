from .session_store import sessions

# sessions = {}


def get_history(call_id):

    if call_id not in sessions:
        sessions[call_id] = {"messages": []}

    return sessions[call_id]["messages"]


def add_message(call_id, role, content):

    if call_id not in sessions:
        sessions[call_id] = {"messages": []}

    sessions[call_id]["messages"].append({
        "role": role,
        "content": content
    })

    # keep last 20 messages
    if len(sessions[call_id]["messages"]) > 20:
        sessions[call_id]["messages"] = sessions[call_id]["messages"][-20:]
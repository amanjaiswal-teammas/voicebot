from .session_store import sessions

# sessions = {}


def get_history(call_id):

    if call_id not in sessions:
        sessions[call_id] = {"messages": []}

    return sessions[call_id]["messages"]


def add_message(call_id, role, content):

    session = sessions.get(call_id, {"messages": []})

    session["messages"].append({
        "role": role,
        "content": content
    })

    # keep last 20 messages
    if len(session["messages"]) > 20:
        session["messages"] = session["messages"][-20:]
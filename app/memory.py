from .session import get_or_create_session


def get_history(call_id):
    return get_or_create_session(call_id)["messages"]


def add_message(call_id, role, content):
    session = get_or_create_session(call_id)
    session["messages"].append({
        "role": role,
        "content": content
    })

    # keep last 20 messages
    if len(session["messages"]) > 20:
        session["messages"] = session["messages"][-20:]

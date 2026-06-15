import uuid

sessions = {}


def create_session():
    call_id = str(uuid.uuid4())

    sessions[call_id] = {
        "active": True
    }

    return call_id


def end_session(call_id):
    if call_id in sessions:
        sessions[call_id]["active"] = False
import threading

sessions = {}
sessions_lock = threading.RLock()

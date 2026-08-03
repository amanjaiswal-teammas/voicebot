import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pymysql
from pymysql.cursors import DictCursor

from .config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB,
    MYSQL_POOL_SIZE, RECORDINGS_DIR,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "migrations"))

_lock = threading.RLock()
_pool = None
_available = False
_init_done = False

_db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-persist")


def defer(fn):
    """Run a DB write off the request thread.

    A single worker preserves submission order, so the conversation row is
    created before any message/state writes for the same call.
    """
    try:
        _db_executor.submit(fn)
    except Exception as e:
        print(f"DB: defer error {e}")


def available():
    return _available


def _connect(db_name=None):
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=db_name,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )


def _init_pool():
    global _pool
    q = queue.Queue()
    try:
        q.put(_connect(MYSQL_DB))
    except Exception:
        return False
    for _ in range(max(0, MYSQL_POOL_SIZE - 1)):
        try:
            q.put(_connect(MYSQL_DB))
        except Exception:
            break
    _pool = q
    return True


def _get_conn():
    if _pool is None:
        return None
    try:
        return _pool.get_nowait()
    except queue.Empty:
        try:
            return _connect(MYSQL_DB)
        except Exception:
            return None


def _put_conn(conn):
    if conn is not None and _pool is not None:
        _pool.put_nowait(conn)


def _execute(sql, args=None, one=False):
    if not _available:
        return None
    conn = _get_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            if one:
                return cur.fetchone()
            return cur.fetchall()
    except pymysql.err.OperationalError:
        try:
            conn.close()
        except Exception:
            pass
        try:
            fresh = _connect(MYSQL_DB)
            with fresh.cursor() as cur:
                cur.execute(sql, args)
                _put_conn(fresh)
                if one:
                    return cur.fetchone()
                return cur.fetchall()
        except Exception as e:
            print(f"DB query retry failed: {e}")
            return None
    except Exception as e:
        _put_conn(conn)
        print(f"DB query error: {e}")
        return None


def _run_migrations():
    if not os.path.isdir(MIGRATIONS_DIR):
        print(f"DB: migrations dir not found: {MIGRATIONS_DIR}")
        return
    conn = _connect(MYSQL_DB)
    try:
        for name in sorted(os.listdir(MIGRATIONS_DIR)):
            if not name.endswith(".sql"):
                continue
            path = os.path.join(MIGRATIONS_DIR, name)
            with open(path, "r", encoding="utf-8") as f:
                sql = f.read()
            with conn.cursor() as cur:
                for stmt in sql.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        cur.execute(stmt)
        print(f"DB: migrations applied from {MIGRATIONS_DIR}")
    finally:
        conn.close()


def _seed_agents():
    from .bellavita_prompt import (
        SYSTEM_PROMPT_BASE, SYSTEM_PROMPT_HI, SYSTEM_PROMPT_EN,
        SALES_HINDI_INSTRUCTION, SALES_ENGLISH_INSTRUCTION,
    )
    from .support_prompt import (
        SYSTEM_PROMPT_BASE as SUPPORT_BASE,
        SYSTEM_PROMPT_EN as SUPPORT_EN,
        SUPPORT_SHORT_INSTRUCTION,
    )

    sales_hi = SYSTEM_PROMPT_BASE + SYSTEM_PROMPT_HI + SALES_HINDI_INSTRUCTION
    sales_en = SYSTEM_PROMPT_BASE + SYSTEM_PROMPT_EN + SALES_ENGLISH_INSTRUCTION
    support = SUPPORT_BASE + SUPPORT_EN + SUPPORT_SHORT_INSTRUCTION

    agents = [
        ("BellaVita Sales (Hindi)", "sales", "hi", "F1", sales_hi),
        ("BellaVita Sales (English)", "sales", "en", "F1", sales_en),
        ("BellaVita Support (Hindi)", "support", "hi", "F1", support),
        ("BellaVita Support (English)", "support", "en", "F1", support),
    ]

    conn = _connect(MYSQL_DB)
    try:
        with conn.cursor() as cur:
            for name, atype, lang, voice, prompt in agents:
                cur.execute(
                    "INSERT INTO agents (name, agent_type, lang, voice, system_prompt) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE "
                    "name = VALUES(name), voice = VALUES(voice), "
                    "system_prompt = VALUES(system_prompt)",
                    (name, atype, lang, voice, prompt),
                )
    finally:
        conn.close()


def init_db():
    global _available, _init_done
    if _init_done:
        return _available
    _init_done = True
    try:
        admin = _connect(None)
        try:
            with admin.cursor() as cur:
                cur.execute(
                    "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_unicode_ci" % MYSQL_DB
                )
        finally:
            admin.close()
        _run_migrations()
        _seed_agents()
        ok = _init_pool()
        if not ok:
            raise RuntimeError("could not open a connection to the pool")
        _available = True
        print(f"DB: connected to mysql://{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
    except Exception as e:
        print(f"DB: unavailable ({e}) - running with in-memory storage")
        _available = False
    return _available


def _get_agent_id(agent_type, lang):
    row = _execute(
        "SELECT id FROM agents WHERE agent_type = %s AND lang = %s AND active = 1",
        (agent_type, lang),
        one=True,
    )
    return row["id"] if row else None


def start_conversation(call_id, agent_type=None, direction=None, lang=None):
    if not _available or not call_id:
        return None
    agent_type = agent_type or "sales"
    direction = direction or "outbound"
    lang = lang or "en"

    row = _execute(
        "SELECT id FROM conversations WHERE call_id = %s", (call_id,), one=True
    )
    if row:
        return row["id"]

    agent_id = _get_agent_id(agent_type, lang)
    conn = _get_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (agent_id, call_id, direction, lang) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)",
                (agent_id, call_id, direction, lang),
            )
            conv_id = cur.lastrowid
        _put_conn(conn)
        return conv_id
    except Exception as e:
        _put_conn(conn)
        print(f"DB start_conversation error: {e}")
        return None


def end_conversation(call_id, status="hangup", outcome=None):
    if not _available or not call_id:
        return
    _execute(
        "UPDATE conversations "
        "SET status = %s, outcome = COALESCE(%s, outcome), "
        "ended_at = CURRENT_TIMESTAMP, "
        "duration_seconds = TIMESTAMPDIFF(SECOND, started_at, CURRENT_TIMESTAMP) "
        "WHERE call_id = %s AND status = 'active'",
        (status, outcome, call_id),
    )


def get_conversation_state(call_id):
    if not _available or not call_id:
        return None
    return _execute(
        "SELECT s.state_value, c.id AS conversation_id "
        "FROM sessions s JOIN conversations c ON c.id = s.conversation_id "
        "WHERE c.call_id = %s AND s.state_key = 'blob'",
        (call_id,),
        one=True,
    )


def upsert_session_state(call_id, blob):
    if not _available or not call_id:
        return
    conv_id = start_conversation(call_id)
    if conv_id is None:
        return
    _execute(
        "INSERT INTO sessions (conversation_id, state_key, state_value) "
        "VALUES (%s, 'blob', %s) "
        "ON DUPLICATE KEY UPDATE state_value = VALUES(state_value)",
        (conv_id, blob),
    )


def insert_message(call_id, role, content):
    if not _available or not call_id:
        return
    _execute(
        "INSERT INTO messages (conversation_id, role, content) "
        "SELECT id, %s, %s FROM conversations WHERE call_id = %s",
        (role, content, call_id),
    )


def get_history(call_id, limit=20):
    if not _available or not call_id:
        return []
    rows = _execute(
        "SELECT m.role, m.content FROM messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.call_id = %s ORDER BY m.id LIMIT %s",
        (call_id, int(limit)),
    )
    return rows or []


def insert_order(call_id, details, raw_text=None):
    if not _available or not call_id:
        return
    _execute(
        "INSERT INTO orders (conversation_id, name, phone, email, address, pincode, raw_text) "
        "SELECT id, %s, %s, %s, %s, %s, %s FROM conversations WHERE call_id = %s",
        (
            (details or {}).get("name"),
            (details or {}).get("phone"),
            (details or {}).get("email"),
            (details or {}).get("address"),
            (details or {}).get("pincode"),
            raw_text,
            call_id,
        ),
    )


def log_event(call_id, event, detail=None):
    if not _available or not call_id:
        return
    defer(lambda: _log_event(call_id, event, detail))


def _log_event(call_id, event, detail):
    _execute(
        "INSERT INTO agent_events (conversation_id, event, detail) "
        "SELECT id, %s, %s FROM conversations WHERE call_id = %s",
        (event, (detail or "")[:255], call_id),
    )


def save_caller_audio(call_id, raw_bytes):
    if not _available or not call_id or not raw_bytes:
        return None
    conv_id = start_conversation(call_id)
    if conv_id is None:
        return None
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    path = os.path.join(RECORDINGS_DIR, f"{call_id}_{uuid.uuid4().hex[:8]}_caller.wav")
    try:
        with open(path, "wb") as f:
            f.write(raw_bytes)
    except Exception as e:
        print(f"DB save_caller_audio WRITE ERROR: {e}")
        return None
    _execute(
        "INSERT INTO audio_artifacts (conversation_id, kind, file_path, bytes) "
        "VALUES (%s, 'caller', %s, %s)",
        (conv_id, path, len(raw_bytes)),
    )
    return path


def get_agent(agent_type, lang):
    if not _available:
        return None
    return _execute(
        "SELECT * FROM agents WHERE agent_type = %s AND lang = %s AND active = 1",
        (agent_type, lang),
        one=True,
    )


def get_system_prompt(agent_type, lang):
    row = get_agent(agent_type, lang)
    if row and row.get("system_prompt"):
        return row["system_prompt"]
    return None


def list_agents():
    if not _available:
        return []
    return (
        _execute(
            "SELECT id, name, agent_type, lang, voice, active, created_at "
            "FROM agents ORDER BY agent_type, lang"
        )
        or []
    )


def list_conversations(limit=50):
    if not _available:
        return []
    return (
        _execute(
            "SELECT c.id, c.call_id, c.direction, c.status, c.lang, c.outcome, "
            "c.started_at, c.ended_at, c.duration_seconds, a.name AS agent_name, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c LEFT JOIN agents a ON a.id = c.agent_id "
            "ORDER BY c.id DESC LIMIT %s",
            (int(limit),),
        )
        or []
    )

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import db  # noqa: E402


def main():
    ok = db.init_db()
    if not ok:
        print(
            "MySQL unavailable. Check MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD "
            "env vars (see .env.example)."
        )
        sys.exit(1)
    agents = db.list_agents()
    print(f"Connected to MySQL at {db.MYSQL_HOST}:{db.MYSQL_PORT}/{db.MYSQL_DB}")
    print("Agents seeded:")
    for a in agents:
        print(
            f"  - [{a['agent_type']}/{a['lang']}] {a['name']} "
            f"(voice={a['voice']})"
        )


if __name__ == "__main__":
    main()

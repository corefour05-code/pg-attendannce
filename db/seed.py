"""One-time bootstrap: create the settings row + a super-admin user so the
login system has something to log into. Safe to re-run (no-ops if already
seeded)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_GATE_PASSWORD, HOSTEL_NAME, SEED_ADMIN_PASSWORD, SEED_ADMIN_USERNAME
from core.security import hash_password
from db.connection import get_connection
from db.init_db import init_db


def seed() -> None:
    init_db()
    conn = get_connection()
    try:
        settings = conn.execute("SELECT id FROM settings WHERE id = 1").fetchone()
        if settings is None:
            conn.execute(
                "INSERT INTO settings (id, hostel_name, gate_password) VALUES (1,?,?)",
                (HOSTEL_NAME, DEFAULT_GATE_PASSWORD),
            )
            conn.commit()
            print("Created settings row.")
        else:
            print("Settings row already exists.")

        admin = conn.execute(
            "SELECT id FROM users WHERE username = ?", (SEED_ADMIN_USERNAME,)
        ).fetchone()
        if admin is None:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,'admin')",
                (SEED_ADMIN_USERNAME, hash_password(SEED_ADMIN_PASSWORD)),
            )
            conn.commit()
            print(
                f"Created admin user '{SEED_ADMIN_USERNAME}' "
                f"with password '{SEED_ADMIN_PASSWORD}' - change this after first login."
            )
        else:
            print(f"User '{SEED_ADMIN_USERNAME}' already exists.")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
